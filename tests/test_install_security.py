"""Targeted installer security tests.

Covers: the two-uid isolation model (web uid vs backend uid, media PGID as a
worker-only supplementary group), the whitelist code copy (ignored secrets
never reach /opt, operator files stay untouched), private-from-creation
backups (0600 before any secret byte, consistent SQLite snapshots, manifest
restore mapping, compose-data refusal), Transmission credentials (0600 before
append, idempotent reuse), the feed dependency gate (valid JSON, private
bridge gateways only), sentinel-first media subdirs, discovery fail-closed,
and the config readiness gate (nonzero until owners/keys/origins configured).

Everything runs in temp dirs against mocks and stub HTTP servers — no
production calls, no systemd/package mutations, no secrets in output.
"""
import contextlib
import io
import json
import os
import sqlite3
import sys
import tarfile
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from installer import cli, discovery, system  # noqa: E402
from installer.deployment import ConfigError, Deployment  # noqa: E402

TEMPLATES = Path(cli.__file__).resolve().parents[1] / 'templates' / 'systemd'

ACCOUNTS = {'web_user': 'dubs', 'shared_group': 'dubs',
            'worker_user': 'dubs-worker', 'backend_group': 'dubs-backend'}


class FakeProc:
    def __init__(self, rc=0, out=''):
        self.returncode = rc
        self.stdout = out


def make_dep(tmp, **overrides):
    opt = tmp / 'opt-dubs'
    values = dict(
        source_repo=tmp / 'repo', install_root=opt, repo=opt,
        compose_dir=tmp / 'compose', user='dubs', group='dubs', pgid='1000',
        config_root=tmp / 'cfg', state_root=tmp / 'state',
        worker_root=tmp / 'worker', feed_state=tmp / 'feedstate',
        covers=tmp / 'covers', studios=tmp / 'cfg' / 'studios.json',
        torrent_host=tmp / 'media' / 'torrents',
        shows_host=tmp / 'media' / 'shows',
    )
    values.update(overrides)
    return Deployment(**values)


def render(name, dep, media_group='mediagrp'):
    return cli._unit_text(dep, (TEMPLATES / name).read_text(),
                          accounts=ACCOUNTS, media_group=media_group)


def capture_run(record):
    def fake_run(cmd, **kwargs):
        record.append(list(cmd))
        if cmd and cmd[0] == 'getent':
            return FakeProc(1)          # everything "missing" -> full creation
        return FakeProc(0)
    return fake_run


class IdentityModelTests(unittest.TestCase):
    """Task 1: two-uid split; the web uid never gets media/backend access."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='dubs-id-'))
        self.dep = make_dep(self.tmp)

    def test_account_names_split(self):
        with mock.patch.object(cli, '_resolve_group_name', return_value='mediagrp'):
            names = cli._account_names(self.dep)
        self.assertEqual(names['web_user'], 'dubs')
        self.assertEqual(names['shared_group'], 'dubs')
        self.assertEqual(names['worker_user'], 'dubs-worker')
        self.assertEqual(names['backend_group'], 'dubs-backend')
        self.assertEqual(names['media_group'], 'mediagrp')

    def test_media_group_aliasing_is_rejected(self):
        # If the compose PGID resolves to one of our own groups it must not
        # silently become the "media group" (web is in the shared group).
        for alias in ('dubs', 'dubs-backend', 'dubs-worker'):
            with mock.patch.object(cli, '_resolve_group_name', return_value=alias):
                self.assertIsNone(cli._account_names(self.dep)['media_group'], alias)

    def test_ensure_user_worker_gets_shared_plus_media_only(self):
        record = []
        with mock.patch.object(cli, '_resolve_group_name', return_value='mediagrp'), \
             mock.patch.object(cli.system, 'run', side_effect=capture_run(record)):
            names = cli._ensure_user(self.dep)
        usermod = [c for c in record if c[0] == 'usermod']
        # Exactly one supplementary-group mutation, on the WORKER uid only.
        self.assertEqual(len(usermod), 1)
        self.assertEqual(usermod[0], ['usermod', '-aG', 'dubs,mediagrp',
                                      'dubs-worker'])
        useradds = [c for c in record if c[0] == 'useradd']
        self.assertEqual(len(useradds), 2)
        web = next(c for c in useradds if c[-1] == 'dubs')
        worker = next(c for c in useradds if c[-1] == 'dubs-worker')
        self.assertIn('--gid', web)
        self.assertEqual(web[web.index('--gid') + 1], 'dubs')
        self.assertEqual(worker[worker.index('--gid') + 1], 'dubs-backend')
        # The web uid is never touched by usermod and never lands in the
        # media/backend group.
        for cmd in record:
            self.assertNotIn('mediagrp', cmd[1:-1] if cmd[-1] == 'dubs' else [],
                             cmd)
            if cmd[0] == 'useradd' and cmd[-1] == 'dubs':
                self.assertNotIn('mediagrp', cmd)

    def test_ensure_user_without_media_group(self):
        record = []
        with mock.patch.object(cli, '_resolve_group_name', return_value=None), \
             mock.patch.object(cli.system, 'run', side_effect=capture_run(record)):
            cli._ensure_user(self.dep)
        usermod = next(c for c in record if c[0] == 'usermod')
        self.assertEqual(usermod[2], 'dubs')           # shared group only
        self.assertEqual(usermod[3], 'dubs-worker')    # on the worker uid


class UnitRenderTests(unittest.TestCase):
    """Task 1: rendered units enforce the uid/group split."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix='dubs-units-'))
        cls.dep = make_dep(cls.tmp)
        cls.rendered = {}
        for template in sorted(TEMPLATES.glob('*.service')):
            cls.rendered[template.name] = render(template.name, cls.dep)

    def test_no_unrendered_placeholders(self):
        import re
        for name, text in self.rendered.items():
            self.assertIsNone(re.search(r'@[A-Z_]+@', text), name)

    def test_web_role_is_distinct_uid_without_media_or_backend(self):
        text = self.rendered['dubs-panel@.service']
        self.assertIn('User=dubs\n', text)
        self.assertIn('Group=dubs\n', text)
        self.assertNotIn('SupplementaryGroups', text)
        self.assertNotIn('mediagrp', text)
        self.assertNotIn('dubs-backend', text)
        # Media roots, container runtime sockets and backend-only credential
        # files are inaccessible; public worker output stays readable.
        self.assertIn('InaccessiblePaths=-%s' % self.dep.torrent_host, text)
        self.assertIn('-%s' % self.dep.shows_host, text)
        self.assertIn('-/run/docker.sock', text)
        self.assertIn('-/run/containerd/', text)
        self.assertIn('-%s/transmission.json' % self.dep.config_root, text)
        self.assertIn('-%s/prowlarr.json' % self.dep.config_root, text)
        self.assertIn('-%s/managed-downloads.json' % self.dep.worker_root, text)
        self.assertIn('ReadOnlyPaths=%s %s %s' % (self.dep.config_root,
                                                  self.dep.feed_state,
                                                  self.dep.worker_root), text)
        # The web role never writes media.
        rw = next(l for l in text.splitlines() if l.startswith('ReadWritePaths='))
        self.assertNotIn(str(self.dep.torrent_host), rw)
        self.assertNotIn(str(self.dep.shows_host), rw)

    def test_worker_uid_with_media_pgid_writes_torrent_and_own_state_only(self):
        text = self.rendered['dubs-worker@.service']
        self.assertIn('User=dubs-worker\n', text)
        self.assertIn('Group=dubs-backend\n', text)
        self.assertIn('SupplementaryGroups=dubs mediagrp\n', text)
        rw = next(l for l in text.splitlines() if l.startswith('ReadWritePaths='))
        self.assertIn(str(self.dep.worker_root), rw)
        self.assertIn('-%s' % self.dep.torrent_host, rw)
        # Library deletes go through the Sonarr API: shows tree read-only.
        ro = next(l for l in text.splitlines() if l.startswith('ReadOnlyPaths='))
        self.assertIn('-%s' % self.dep.shows_host, ro)
        self.assertNotIn(str(self.dep.shows_host), rw)
        self.assertIn(str(self.dep.state_root), ro)

    def test_oneshots_get_backend_uid_shared_group_only(self):
        for name in ('dubs-myshows-feed.service', 'dubs-recovery.service',
                     'dubs-recent-episodes.service', 'dubs-jackett-sync.service'):
            text = self.rendered[name]
            self.assertIn('User=dubs-worker\n', text, name)
            self.assertIn('Group=dubs-backend\n', text, name)
            self.assertIn('SupplementaryGroups=dubs\n', text, name)
            self.assertNotIn('mediagrp', text, name)
            self.assertNotIn(str(self.dep.torrent_host),
                             next((l for l in text.splitlines()
                                   if l.startswith('ReadWritePaths=')), ''), name)

    def test_operations_reads_media_for_space_guard_only(self):
        # Regression: the operations role statvfs's torrent/shows storage for
        # the space guard (runtime.storage_ready), so it needs READ access via
        # the compose PGID media group — but every write still goes through
        # the Sonarr/Transmission APIs, so media paths are read-only here.
        text = self.rendered['dubs-operations@.service']
        self.assertIn('User=dubs-worker\n', text)
        self.assertIn('Group=dubs-backend\n', text)
        self.assertIn('SupplementaryGroups=dubs mediagrp\n', text)
        ro = next(l for l in text.splitlines() if l.startswith('ReadOnlyPaths='))
        self.assertIn('-%s' % self.dep.torrent_host, ro)
        self.assertIn('-%s' % self.dep.shows_host, ro)
        rw = next(l for l in text.splitlines() if l.startswith('ReadWritePaths='))
        self.assertIn(str(self.dep.worker_root), rw)
        self.assertNotIn(str(self.dep.torrent_host), rw)
        self.assertNotIn(str(self.dep.shows_host), rw)

    def test_media_group_only_in_worker_and_operations(self):
        media_units = [n for n, t in self.rendered.items() if 'mediagrp' in t]
        self.assertEqual(media_units,
                         ['dubs-operations@.service', 'dubs-worker@.service'])

    def test_hardening_flags_everywhere(self):
        for name, text in self.rendered.items():
            self.assertIn('NoNewPrivileges=yes', text, name)
            self.assertIn('ProtectHome=yes', text, name)
            self.assertIn('ProtectSystem=strict', text, name)
            self.assertNotIn('0.0.0.0', text, name)

    def test_automation_state_moved_to_private_worker_root(self):
        recovery = self.rendered['dubs-recovery.service']
        self.assertIn('DUBS_RECOVERY_STATE=%s/recovery/state.json'
                      % self.dep.worker_root, recovery)
        recent = self.rendered['dubs-recent-episodes.service']
        self.assertIn('DUBS_RECENT_STATE=%s/recent-episodes.json'
                      % self.dep.worker_root, recent)
        sync = self.rendered['dubs-jackett-sync.service']
        self.assertIn('DUBS_AUTOMATION_STATE=%s/automations' % self.dep.worker_root,
                      sync)


class SyncCodeTests(unittest.TestCase):
    """Task 2: whitelist copy; ignored secrets never reach the install root."""

    def build_repo(self, tmp):
        repo = tmp / 'repo'
        for rel in ('src/control.py', 'src/runtime.py',
                    'automations/myshows_feed.py', 'installer/cli.py',
                    'templates/systemd/dubs-panel@.service',
                    'compose/docker-compose.yml', 'compose/storage-sentinel.sh',
                    'compose/.env.example', 'studios.json', 'README.md',
                    'LICENSE', 'requirements.txt', 'THIRD_PARTY.md'):
            p = repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('# %s\n' % rel)
        # Secrets / ignored / runtime content that must NEVER be copied.
        (repo / '.env.local').write_text('SECRET_TOKEN=abc\n')
        (repo / 'secrets').mkdir()
        (repo / 'secrets' / 'id_rsa').write_text('PRIVATE KEY MATERIAL\n')
        (repo / 'compose' / '.env').write_text('MEDIA_ROOT=/leak\n')
        (repo / 'venv').mkdir()
        (repo / 'venv' / 'junk.py').write_text('x\n')
        (repo / 'data').mkdir(); (repo / 'data' / 'db.sqlite3').write_text('x')
        (repo / 'tests').mkdir(); (repo / 'tests' / 't.py').write_text('x')
        (repo / 'docs').mkdir(); (repo / 'docs' / 'd.md').write_text('x')
        (repo / '.git').mkdir(); (repo / '.git' / 'HEAD').write_text('ref')
        return repo

    def test_whitelist_copy_and_secret_exclusion(self):
        tmp = Path(tempfile.mkdtemp(prefix='dubs-sync-'))
        repo = self.build_repo(tmp)
        dep = make_dep(tmp)
        # Operator state that already lives in the install root.
        (dep.install_root / 'compose').mkdir(parents=True)
        (dep.install_root / 'compose' / '.env').write_text('KEEP_MY_ENV=1\n')
        os.chmod(dep.install_root / 'compose' / '.env', 0o600)
        (dep.install_root / 'operator-notes.txt').write_text('mine\n')
        os.chmod(dep.install_root / 'operator-notes.txt', 0o600)
        (dep.install_root / 'src').mkdir()
        (dep.install_root / 'src' / 'local-secret.pem').write_text('KEY\n')
        os.chmod(dep.install_root / 'src' / 'local-secret.pem', 0o600)
        with mock.patch.object(cli.system, 'run', return_value=FakeProc(0)):
            dst = cli._sync_code(dep)
        self.assertEqual(dst, dep.install_root)
        # Whitelisted code/assets are present with 0755/0644.
        self.assertTrue((dst / 'src' / 'control.py').is_file())
        self.assertEqual(os.stat(dst / 'src' / 'control.py').st_mode & 0o777, 0o644)
        self.assertEqual(os.stat(dst / 'src').st_mode & 0o777, 0o755)
        for rel in ('automations/myshows_feed.py', 'installer/cli.py',
                    'templates/systemd/dubs-panel@.service',
                    'compose/docker-compose.yml', 'compose/storage-sentinel.sh',
                    'compose/.env.example', 'studios.json', 'README.md'):
            self.assertTrue((dst / rel).is_file(), rel)
            self.assertEqual(os.stat(dst / rel).st_mode & 0o777, 0o644, rel)
        # Nothing secret/ignored/runtime was copied. (compose/.env is asserted
        # below as the PRESERVED operator file — it was never copied over.)
        for rel in ('.env.local', 'secrets/id_rsa', 'venv/junk.py',
                    'data/db.sqlite3', 'tests/t.py', 'docs/d.md', '.git/HEAD'):
            self.assertFalse((dst / rel).exists(), rel)
        self.assertFalse((dst / 'secrets').exists())
        self.assertFalse((dst / 'venv').exists())
        self.assertFalse((dst / 'data').exists())
        self.assertFalse((dst / 'tests').exists())
        # Operator files already in the install root stay byte-identical 0600.
        env = dst / 'compose' / '.env'
        self.assertEqual(env.read_text(), 'KEEP_MY_ENV=1\n')
        self.assertEqual(os.stat(env).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(dst / 'operator-notes.txt').st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(dst / 'src' / 'local-secret.pem').st_mode & 0o777,
                         0o600)

    def test_install_root_under_root_is_refused(self):
        tmp = Path(tempfile.mkdtemp(prefix='dubs-sync2-'))
        repo = self.build_repo(tmp)
        dep = make_dep(tmp, install_root=Path('/root/dubs-must-not-exist'))
        with mock.patch.object(cli.system, 'run', return_value=FakeProc(0)):
            with self.assertRaises(ConfigError):
                cli._sync_code(dep)
        self.assertFalse(Path('/root/dubs-must-not-exist').exists())

    def test_same_tree_is_a_noop(self):
        tmp = Path(tempfile.mkdtemp(prefix='dubs-sync3-'))
        repo = self.build_repo(tmp)
        dep = make_dep(tmp, install_root=repo, repo=repo)
        with mock.patch.object(cli.system, 'run', return_value=FakeProc(0)) as run:
            self.assertIsNone(cli._sync_code(dep))
            chowns = [c for c in run.call_args_list if
                      c.args and c.args[0][:1] == ['chown']]
            self.assertEqual(chowns, [])

    def test_resync_over_existing_runtime_root_keeps_operator_state(self):
        # Re-running install against an EXISTING runtime root: fresh upstream
        # content lands (a managed file an operator chmod'ed is re-normalized
        # to the managed 0644 — its bytes are upstream's and units only read),
        # while an operator-ONLY file inside a synced dir keeps its bytes,
        # mode and owner (never chmod'ed, never chowned).
        tmp = Path(tempfile.mkdtemp(prefix='dubs-sync4-'))
        repo = self.build_repo(tmp)
        dep = make_dep(tmp)
        with mock.patch.object(cli.system, 'run', return_value=FakeProc(0)):
            cli._sync_code(dep)
        managed = dep.install_root / 'src' / 'runtime.py'
        os.chmod(managed, 0o600)
        operator = dep.install_root / 'src' / 'operator-creds.pem'
        operator.write_text('OPERATOR KEY\n')
        os.chmod(operator, 0o600)
        # Upstream code changed between runs.
        (repo / 'src' / 'runtime.py').write_text('# updated runtime\n')
        record = []
        with mock.patch.object(cli.system, 'run',
                               side_effect=lambda c, **k: (record.append(list(c)),
                                                           FakeProc(0))[1]):
            cli._sync_code(dep)
        self.assertEqual(managed.read_text(), '# updated runtime\n')
        self.assertEqual(os.stat(managed).st_mode & 0o777, 0o644)
        self.assertEqual(operator.read_text(), 'OPERATOR KEY\n')
        self.assertEqual(os.stat(operator).st_mode & 0o777, 0o600)
        chowned = [c[2] for c in record if c[0] == 'chown']
        self.assertNotIn(str(managed), chowned)
        self.assertNotIn(str(operator), chowned)


class ConfigPermsTests(unittest.TestCase):
    """Task 1/4: explicit backend-private vs shared config permissions."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='dubs-cfg-'))
        self.dep = make_dep(self.tmp)
        cfg = self.dep.config_root
        cfg.mkdir()
        for name in ('transmission', 'prowlarr', 'radarr', 'lidarr', 'jackett',
                     'sonarr', 'jellyfin', 'security', 'config', 'studios'):
            (cfg / (name + '.json')).write_text('{}\n')
        (cfg / 'server.key').write_text('PRIVATE\n')
        (cfg / 'notes.txt').write_text('x\n')
        os.chmod(cfg / 'notes.txt', 0o644)

    def test_backend_private_and_shared_split(self):
        record = []
        with mock.patch.object(cli.system, 'run',
                               side_effect=lambda c, **k: (record.append(list(c)),
                                                           FakeProc(0))[1]):
            cli._harden_config_perms(self.dep, ACCOUNTS)
        mode = lambda p: os.stat(self.dep.config_root / p).st_mode & 0o777
        for svc in cli.BACKEND_PRIVATE_CONFIGS:
            self.assertEqual(mode(svc + '.json'), 0o640, svc)
            self.assertIn(['chown', 'root:dubs-backend',
                           str(self.dep.config_root / (svc + '.json'))], record)
        for name in cli.SHARED_CONFIGS:
            self.assertEqual(mode(name + '.json'), 0o640, name)
            self.assertIn(['chown', 'root:dubs',
                           str(self.dep.config_root / (name + '.json'))], record)
        # Private key: web-uid-only 0600.
        self.assertEqual(mode('server.key'), 0o600)
        self.assertIn(['chown', 'dubs:dubs',
                       str(self.dep.config_root / 'server.key')], record)
        # Unmanaged files are untouched; the directory is 0750.
        self.assertEqual(mode('notes.txt'), 0o644)
        self.assertEqual(os.stat(self.dep.config_root).st_mode & 0o777, 0o750)


class TransmissionCredentialsTests(unittest.TestCase):
    """Task 4: private from creation; idempotent reuse; never printed."""

    def test_created_private_from_start(self):
        tmp = Path(tempfile.mkdtemp(prefix='dubs-cred-'))
        env = tmp / '.env'
        env.write_text('MEDIA_ROOT=/mnt/media\n')
        os.chmod(env, 0o644)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            keys = cli._ensure_transmission_credentials(env)
        self.assertEqual(sorted(keys), ['TRANSMISSION_PASS', 'TRANSMISSION_USER'])
        text = env.read_text()
        self.assertIn('MEDIA_ROOT=/mnt/media\n', text)
        self.assertIn('TRANSMISSION_USER=dubs-', text)
        self.assertIn('TRANSMISSION_PASS=', text)
        self.assertEqual(os.stat(env).st_mode & 0o777, 0o600)
        # Values never reach stdout, and the returned key list is names only.
        self.assertNotIn('TRANSMISSION_PASS=', buf.getvalue())
        for key in keys:
            self.assertNotIn(text.split(key + '=', 1)[1].split('\n')[0], buf.getvalue())

    def test_existing_pair_is_reused_idempotently(self):
        tmp = Path(tempfile.mkdtemp(prefix='dubs-cred2-'))
        env = tmp / '.env'
        env.write_text('TRANSMISSION_USER=keeper\nTRANSMISSION_PASS=keeppw\n')
        before = env.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(cli._ensure_transmission_credentials(env))
        self.assertEqual(env.read_bytes(), before)

    def test_loose_mode_with_existing_secrets_is_tightened(self):
        tmp = Path(tempfile.mkdtemp(prefix='dubs-cred3-'))
        env = tmp / '.env'
        env.write_text('TRANSMISSION_USER=keeper\nTRANSMISSION_PASS=keeppw\n')
        os.chmod(env, 0o644)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(cli._ensure_transmission_credentials(env))
        self.assertEqual(os.stat(env).st_mode & 0o777, 0o600)
        self.assertIn('keeppw', env.read_text())


class SentinelStorageTests(unittest.TestCase):
    """Task 6: media subdirs only after the operator-armed sentinel."""

    def test_no_sentinel_no_stubs(self):
        tmp = Path(tempfile.mkdtemp(prefix='dubs-storage-'))
        media = tmp / 'media'
        pending = cli._create_media_subdirs(media, '1000', '1000')
        self.assertEqual(len(pending), 1)
        self.assertIn('sentinel', pending[0])
        self.assertFalse(media.exists())

    def test_sentinel_directory_does_not_count(self):
        tmp = Path(tempfile.mkdtemp(prefix='dubs-storage2-'))
        media = tmp / 'media'
        media.mkdir()
        (media / '.dubs-storage-ok').mkdir()
        pending = cli._create_media_subdirs(media, '1000', '1000')
        self.assertTrue(pending)
        self.assertFalse((media / 'torrents').exists())

    def test_armed_sentinel_creates_group_writable_subdirs(self):
        tmp = Path(tempfile.mkdtemp(prefix='dubs-storage3-'))
        media = tmp / 'media'
        media.mkdir()
        (media / '.dubs-storage-ok').write_text('')
        record = []
        with mock.patch.object(cli.system, 'run',
                               side_effect=lambda c, **k: (record.append(list(c)),
                                                           FakeProc(0))[1]):
            pending = cli._create_media_subdirs(media, '1000', '1000')
        self.assertEqual(pending, [])
        for sub in ('torrents', 'shows', 'movies', 'music'):
            d = media / sub
            self.assertTrue(d.is_dir(), sub)
            self.assertEqual(os.stat(d).st_mode & 0o7777, 0o2775, sub)
            self.assertIn(['chown', '1000:1000', str(d)], record)


class FeedGateTests(unittest.TestCase):
    """Task 5: the feed must serve valid JSON before anything is wired."""

    @staticmethod
    def serve(handler):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    def test_valid_json_health_passes(self):
        class Ok(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = json.dumps({'updated': 'x', 'mapped': 1}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(body)
        srv = self.serve(Ok)
        try:
            ok, detail = cli._feed_reachable(
                'http://127.0.0.1:%d' % srv.server_port)
            self.assertTrue(ok, detail)
            self.assertIn('valid JSON', detail)
        finally:
            srv.shutdown(); srv.server_close()

    def test_non_json_answer_never_counts_as_wired(self):
        class Html(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'<html>not json</html>')
        srv = self.serve(Html)
        try:
            ok, detail = cli._feed_reachable(
                'http://127.0.0.1:%d' % srv.server_port)
            self.assertFalse(ok)
            self.assertIn('valid JSON', detail)
        finally:
            srv.shutdown(); srv.server_close()

    def test_connection_refused_is_deferred(self):
        blocker = __import__('socket').socket()
        blocker.bind(('127.0.0.1', 0))
        port = blocker.getsockname()[1]
        blocker.close()
        ok, detail = cli._feed_reachable('http://127.0.0.1:%d' % port)
        self.assertFalse(ok)
        self.assertIn('Connection refused', detail)

    def test_only_private_gateways_are_bound(self):
        self.assertTrue(cli._is_private_gateway('172.18.0.1'))
        self.assertTrue(cli._is_private_gateway('192.168.5.1'))
        self.assertTrue(cli._is_private_gateway('10.0.0.2'))
        for bad in ('0.0.0.0', '::', '*', '8.8.8.8', '1.2.3.4', 'garbage', ''):
            self.assertFalse(cli._is_private_gateway(bad), bad)

    def test_transmission_parity_warning(self):
        tmp = Path(tempfile.mkdtemp(prefix='dubs-parity-'))
        dep = make_dep(tmp)
        (dep.compose_dir).mkdir(parents=True)
        (dep.compose_dir / '.env').write_text(
            'TRANSMISSION_USER=envuser\nTRANSMISSION_PASS=envpass\n')
        conn = {'transmission': {'user': 'envuser', 'password': 'MISMATCH'}}
        buf = io.StringIO()
        with mock.patch.object(cli, '_connections_safe', return_value=conn), \
                contextlib.redirect_stdout(buf):
            cli._warn_transmission_parity(dep)
        self.assertIn('WARNING', buf.getvalue())
        self.assertIn('password', buf.getvalue())
        self.assertNotIn('envpass', buf.getvalue())
        self.assertNotIn('MISMATCH', buf.getvalue())
        # Matching credentials print nothing.
        conn['transmission']['password'] = 'envpass'
        buf = io.StringIO()
        with mock.patch.object(cli, '_connections_safe', return_value=conn), \
                contextlib.redirect_stdout(buf):
            cli._warn_transmission_parity(dep)
        self.assertNotIn('WARNING', buf.getvalue())


class BridgeGatewayTests(unittest.TestCase):
    """Regression: gateways come from the ACTUAL managed compose network.

    The compose file declares top-level `name: dubs` with a network keyed
    `dubs`, so Docker names it `dubs_dubs`; `dubs_default` never exists and
    must never be probed. Only exact private gateways are ever returned.
    """

    def _inspect_out(self, name, gateways):
        return json.dumps([{'Name': name,
                            'IPAM': {'Config': [{'Gateway': g} for g in gateways]}}])

    def test_gateways_from_the_managed_project_network_only(self):
        record = []

        def fake_run(cmd, **kwargs):
            record.append(list(cmd))
            if cmd[:3] == ['docker', 'network', 'inspect']:
                if 'dubs_dubs' in cmd:
                    return FakeProc(0, self._inspect_out(
                        'dubs_dubs', ['172.18.0.1', '172.18.0.1', '172.19.0.1']))
                return FakeProc(1, '')
            return FakeProc(0)

        with mock.patch.object(cli.system, 'have', return_value=True), \
                mock.patch.object(cli.system, 'run', side_effect=fake_run):
            gateways = cli._bridge_gateways()
        self.assertEqual(gateways, ['172.18.0.1', '172.19.0.1'])  # dedup, order
        inspected = [c[3] for c in record if c[:3] == ['docker', 'network', 'inspect']]
        self.assertIn('dubs_dubs', inspected)
        self.assertNotIn('dubs_default', inspected)   # the old wrong guess

    def test_non_private_gateways_are_dropped(self):
        def fake_run(cmd, **kwargs):
            if cmd[:3] == ['docker', 'network', 'inspect']:
                return FakeProc(0, self._inspect_out(
                    'dubs_dubs', ['8.8.8.8', '0.0.0.0', '::', 'garbage', '10.1.2.3']))
            return FakeProc(0)

        with mock.patch.object(cli.system, 'have', return_value=True), \
                mock.patch.object(cli.system, 'run', side_effect=fake_run):
            self.assertEqual(cli._bridge_gateways(), ['10.1.2.3'])

    def test_missing_network_or_bad_output_is_empty(self):
        cases = (FakeProc(1, ''),                             # network absent
                 FakeProc(0, ''),                             # empty output
                 FakeProc(0, 'not json'),                     # non-JSON
                 FakeProc(0, self._inspect_out('dubs_dubs', [])))
        for proc in cases:
            with mock.patch.object(cli.system, 'have', return_value=True), \
                    mock.patch.object(cli.system, 'run', return_value=proc):
                self.assertEqual(cli._bridge_gateways(), [], proc)

    def test_no_docker_runs_nothing(self):
        record = []
        with mock.patch.object(cli.system, 'have', return_value=False), \
                mock.patch.object(cli.system, 'run',
                                  side_effect=lambda c, **k: record.append(list(c))):
            self.assertEqual(cli._bridge_gateways(), [])
        self.assertEqual(record, [])


class DiscoveryFailClosedTests(unittest.TestCase):
    """Task 7: a broken probe must never degrade to installable/unavailable."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='dubs-disc-'))
        self.dep = make_dep(self.tmp)

    def test_error_class_retains_declared_external(self):
        conn = {'sonarr': {'base_url': 'http://192.0.2.10:8989'},
                'jellyfin': {'base_url': 'http://127.0.0.1:8096'}}
        with mock.patch.object(cli, '_connections_safe', return_value=conn), \
                mock.patch.object(discovery, 'detect',
                                  side_effect=RuntimeError('boom')):
            out = cli._discover(self.dep)
        self.assertEqual(out['sonarr']['class'], 'error')
        self.assertTrue(out['sonarr']['blocked'])
        self.assertTrue(out['sonarr']['external'])       # declared URL kept
        self.assertFalse(out['sonarr']['managed'])
        self.assertEqual(out['jellyfin']['class'], 'error')
        self.assertTrue(out['jellyfin']['blocked'])
        # Undeclared services are blocked too — never 'unavailable' (which
        # plan/install would compose-up as a duplicate).
        r = out['seerr']
        self.assertEqual(r['class'], 'error')
        self.assertTrue(r['blocked'])

    def test_install_set_never_includes_blocked_or_external(self):
        conn = {'sonarr': {'base_url': 'http://192.0.2.10:8989'}}
        services = dict.fromkeys(discovery.SERVICE_ORDER,
                                 {'class': 'error', 'blocked': True,
                                  'external': False, 'managed': False})
        services['sonarr'] = {'class': 'error', 'blocked': True,
                              'external': True, 'managed': False}
        missing = [n for n, r in services.items()
                   if r['class'] == 'unavailable' and not r.get('external')
                   and n != 'flaresolverr']
        self.assertEqual(missing, [])


class ConfigGateTests(unittest.TestCase):
    """Task 8: nonzero check until owners, keys and trust origins are real."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='dubs-gate-'))
        self.dep = make_dep(self.tmp)
        self.dep.config_root.mkdir()

    def write(self, name, data):
        (self.dep.config_root / name).write_text(json.dumps(data))

    def test_skeleton_defaults_are_all_pending(self):
        self.write('security.json', {'signing_key': 'k', 'owner_names': [],
                                     'owner_ids': [], 'allowed_origins': []})
        problems = cli._config_gate(self.dep)
        joined = '\n'.join(problems)
        self.assertIn('owner_names/owner_ids', joined)
        self.assertIn('allowed_origins', joined)
        self.assertIn('sonarr.json', joined)
        self.assertIn('transmission', joined)
        self.assertTrue(all(p.startswith('PENDING') for p in problems))

    def test_missing_security_json_is_pending(self):
        problems = cli._config_gate(self.dep)
        self.assertTrue(any('security.json missing' in p for p in problems))

    def test_filled_config_gates_pass(self):
        self.write('security.json', {'signing_key': 'k', 'owner_names': ['me'],
                                     'owner_ids': ['uid-1'],
                                     'allowed_origins': ['https://panel.example']})
        conn = {'sonarr': {'base_url': 'http://127.0.0.1:8989', 'key': 'sk'},
                'prowlarr': {'base_url': 'http://127.0.0.1:9696', 'key': 'pk'},
                'jellyfin': {'base_url': 'http://127.0.0.1:8096', 'token': 'rt'},
                'transmission': {'base_url': 'http://127.0.0.1:9091',
                                 'user': 'u', 'password': 'p'}}
        with mock.patch.object(cli, '_connections_safe', return_value=conn):
            self.assertEqual(cli._config_gate(self.dep), [])
        # Partially filled (owners still empty) stays pending.
        self.write('security.json', {'signing_key': 'k', 'owner_names': [],
                                     'owner_ids': [], 'allowed_origins': ['x']})
        with mock.patch.object(cli, '_connections_safe', return_value=conn):
            problems = cli._config_gate(self.dep)
        self.assertTrue(any('owner_names/owner_ids' in p for p in problems))

    def test_owners_configured_gate(self):
        self.assertFalse(cli._owners_configured(self.dep))
        self.write('security.json', {'owner_names': ['me'], 'owner_ids': ['u1']})
        self.assertTrue(cli._owners_configured(self.dep))

    def test_check_is_nonzero_until_configured(self):
        self.write('security.json', {'signing_key': 'k', 'owner_names': [],
                                     'owner_ids': [], 'allowed_origins': []})
        ready = {n: {'class': 'ready', 'detail': 'ok', 'external': False,
                     'managed': True} for n in discovery.SERVICE_ORDER}
        with mock.patch.object(cli, '_deployment', return_value=self.dep), \
                mock.patch.object(cli, '_discover', return_value=ready), \
                mock.patch.object(cli, '_storage_ready_safe',
                                  return_value=(True, '')), \
                mock.patch.object(cli, '_feed_health', return_value=(True, 'ok')), \
                mock.patch.object(cli, '_panel_health', return_value=(True, 'ok')):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.cmd_check(mock.Mock())
            self.assertEqual(rc, 1)
            self.assertIn('owner_names/owner_ids', buf.getvalue())
            self.assertIn('CHECK: not ready', buf.getvalue())
        # Fully configured + healthy => ready.
        self.write('security.json', {'signing_key': 'k', 'owner_names': ['me'],
                                     'owner_ids': ['u1'],
                                     'allowed_origins': ['https://p']})
        conn = {'sonarr': {'base_url': 'u', 'key': 'k'},
                'prowlarr': {'base_url': 'u', 'key': 'k'},
                'jellyfin': {'base_url': 'u', 'token': 't'},
                'transmission': {'base_url': 'u', 'user': 'a', 'password': 'b'}}
        with mock.patch.object(cli, '_deployment', return_value=self.dep), \
                mock.patch.object(cli, '_discover', return_value=ready), \
                mock.patch.object(cli, '_storage_ready_safe',
                                  return_value=(True, '')), \
                mock.patch.object(cli, '_feed_health', return_value=(True, 'ok')), \
                mock.patch.object(cli, '_panel_health', return_value=(True, 'ok')), \
                mock.patch.object(cli, '_connections_safe', return_value=conn):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.cmd_check(mock.Mock())
            self.assertEqual(rc, 0, buf.getvalue())
            self.assertIn('CHECK: all components ready', buf.getvalue())


class UpdateScopeTests(unittest.TestCase):
    """Task 7: update touches only running owned services, with propagation."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='dubs-update-'))
        self.dep = make_dep(self.tmp)
        (self.dep.source_repo / 'src').mkdir(parents=True)
        (self.dep.source_repo / 'src' / 'x.py').write_text('x\n')
        (self.dep.source_repo / 'compose').mkdir()
        (self.dep.source_repo / 'compose' / 'docker-compose.yml').write_text('x')
        self.dep.compose_dir.mkdir(parents=True)
        (self.dep.compose_dir / '.env').write_text('MEDIA_ROOT=/mnt/x\n')
        (self.dep.compose_dir / 'docker-compose.yml').write_text('x')

    def test_compose_up_no_deps_flag(self):
        record = []
        with mock.patch.object(cli.system, 'docker_info',
                               return_value=('docker', ['docker', 'compose'], '2.20')), \
                mock.patch.object(cli.system, 'run',
                                  side_effect=lambda c, **k:
                                      (record.append(list(c)), FakeProc(0))[1]):
            cli._compose_up(self.dep, names=['sonarr'], no_deps=True)
            self.assertIn('--no-deps', record[-1])
            self.assertEqual(record[-1][-1], 'sonarr')
            cli._compose_up(self.dep, names=['sonarr'])
            self.assertNotIn('--no-deps', record[-1])

    def _update(self, pull_rc, up_rc=0, running='sonarr\nradarr\n'):
        record = []

        def fake_run(cmd, **kwargs):
            record.append(list(cmd))
            if cmd[:2] == ['docker', 'compose'] and 'pull' in cmd:
                return FakeProc(pull_rc)
            if cmd[:2] == ['docker', 'compose'] and 'ps' in cmd:
                return FakeProc(0, running)
            return FakeProc(0 if cmd[0] != 'docker' else up_rc)
        with mock.patch.object(cli, '_deployment', return_value=self.dep), \
                mock.patch.object(cli.system, 'docker_info',
                                  return_value=('docker', ['docker', 'compose'], '2.20')), \
                mock.patch.object(cli.system, 'run', side_effect=fake_run), \
                mock.patch.object(cli.system, 'unit_state',
                                  return_value=('active', 'enabled')), \
                mock.patch.object(cli.system, 'systemctl',
                                  side_effect=lambda a, u, **k:
                                      record.append(['systemctl', a, u])):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.cmd_update(mock.Mock())
        return rc, record, buf.getvalue()

    def test_pull_failure_is_propagated_and_no_up_no_restart(self):
        rc, record, out = self._update(pull_rc=1)
        self.assertEqual(rc, 1)
        self.assertFalse([c for c in record if 'up' in c])
        self.assertFalse([c for c in record if c[:1] == ['systemctl']
                          and c[1] == 'restart'])
        self.assertIn('compose pull failed', out)

    def test_up_only_running_services_without_dependencies(self):
        rc, record, out = self._update(pull_rc=0)
        self.assertEqual(rc, 0)
        ups = [c for c in record if 'up' in c]
        self.assertEqual(len(ups), 1)
        self.assertIn('--no-deps', ups[0])
        for name in ('sonarr', 'radarr'):
            self.assertIn(name, ups[0])
        self.assertNotIn('jellyfin', ups[0])     # stopped service stays stopped
        restarted = [c for c in record if c[:1] == ['systemctl']
                     and c[1] == 'restart']
        self.assertEqual(sorted(c[2] for c in restarted),
                         sorted(['dubs-panel@main', 'dubs-worker@main',
                                 'dubs-operations@main', 'dubs-myshows-feed']))
        self.assertNotIn('compose up failed', out)

    def test_failed_up_stops_unit_restarts(self):
        rc, record, out = self._update(pull_rc=0, up_rc=1)
        self.assertEqual(rc, 1)
        self.assertFalse([c for c in record if c[:1] == ['systemctl']
                          and c[1] == 'restart'])
        self.assertIn('compose up failed', out)


class BackupSecurityTests(unittest.TestCase):
    """Task 3: 0600 from creation, consistent SQLite, staged restore mapping."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='dubs-backup-'))
        self.dep = make_dep(self.tmp, data_root=self.tmp / 'compose-data')
        for d in (self.dep.config_root, self.dep.state_root, self.dep.worker_root,
                  self.dep.feed_state, self.dep.covers):
            d.mkdir(parents=True)
        (self.dep.config_root / 'security.json').write_text(
            json.dumps({'signing_key': 'TOPSECRETKEY'}))
        (self.dep.config_root / 'transmission.json').write_text(
            json.dumps({'user': 'u', 'password': 'RPCSECRETPW'}))
        db = self.dep.state_root / 'security.sqlite3'
        conn = sqlite3.connect(str(db))
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('CREATE TABLE sessions (sid TEXT PRIMARY KEY)')
        conn.execute("INSERT INTO sessions VALUES ('sess-1')")
        conn.commit()
        conn.close()
        (self.dep.state_root / 'cleanup-request.json').write_text('{"op":"x"}')
        (self.dep.worker_root / 'cleanup-status.json').write_text('{"state":"ok"}')
        (self.dep.feed_state / 'status.json').write_text('{"updated":"now"}')
        (self.dep.covers / 'c.jpg').write_bytes(b'jpg')

    def _backup(self, with_compose=False, docker=None, dep=None):
        dep = dep or self.dep
        args = mock.Mock(with_compose_data=with_compose, dest=str(self.tmp / 'bak'))
        with mock.patch.object(cli, '_deployment', return_value=dep), \
                mock.patch.object(cli.system, 'docker_services',
                                  return_value=docker or {}):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.cmd_backup(args)
        return rc, buf.getvalue()

    def test_private_from_creation_members_and_manifest(self):
        rc, out = self._backup()
        self.assertEqual(rc, 0, out)
        archives = list((self.tmp / 'bak').glob('dubs-backup-*.tar.gz'))
        self.assertEqual(len(archives), 1)
        self.assertEqual(os.stat(archives[0]).st_mode & 0o777, 0o600)
        with tarfile.open(archives[0], 'r:gz') as tar:
            names = tar.getnames()
            self.assertIn('config/security.json', names)
            self.assertIn('config/transmission.json', names)
            self.assertIn('web-state/security.sqlite3', names)
            self.assertIn('worker-state/cleanup-status.json', names)
            self.assertIn('feed-state/status.json', names)
            self.assertIn('covers/c.jpg', names)
            self.assertIn('restore-manifest.json', names)
            # WAL sidecars are folded into the snapshot, never archived raw.
            self.assertNotIn('web-state/security.sqlite3-wal', names)
            self.assertNotIn('web-state/security.sqlite3-shm', names)
            manifest = json.load(tar.extractfile('restore-manifest.json'))
            snap = tar.extractfile('web-state/security.sqlite3').read()
        # Manifest restore mapping roundtrip (staged restore, never tar -C /).
        self.assertEqual(manifest['restore']['config'],
                         str(self.dep.config_root))
        self.assertEqual(manifest['restore']['web-state'],
                         str(self.dep.state_root))
        self.assertIn('staging', manifest['note'])
        # The SQLite snapshot is a valid, consistent database.
        outdir = self.tmp / 'verify'
        outdir.mkdir()
        dbfile = outdir / 'security.sqlite3'
        dbfile.write_bytes(snap)
        conn = sqlite3.connect(str(dbfile))
        rows = conn.execute('SELECT sid FROM sessions').fetchall()
        conn.close()
        self.assertEqual(rows, [('sess-1',)])
        # No secrets in stdout.
        self.assertNotIn('TOPSECRETKEY', out)
        self.assertNotIn('RPCSECRETPW', out)

    def test_unique_archive_names(self):
        self._backup()
        self._backup()
        archives = list((self.tmp / 'bak').glob('dubs-backup-*.tar.gz'))
        self.assertEqual(len(archives), 2)
        self.assertEqual(len({a.name for a in archives}), 2)

    def test_compose_data_refused_while_services_running(self):
        rc, out = self._backup(with_compose=True,
                               docker={'sonarr': {'name': 'dubs-sonarr'}})
        self.assertEqual(rc, 2)
        self.assertIn('refusing', out)
        self.assertIn('Quiesce', out)

    def test_colliding_member_paths_are_refused(self):
        # Misconfigured roots pointing at the same directory must not produce
        # a silently double-archived (or restore-ambiguous) archive.
        dep = make_dep(self.tmp, state_root=self.dep.config_root)
        rc, out = self._backup(dep=dep)
        self.assertEqual(rc, 2)
        self.assertIn('same directory', out)


if __name__ == '__main__':
    unittest.main()
