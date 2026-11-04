"""Dubs installer CLI: plan, doctor, install, configure, bootstrap, check, update, backup.

plan and doctor never mutate the system and degrade to stdlib-only checks when
python3-requests is not installed yet. install/configure/bootstrap/update/backup
are idempotent and print the operator steps that remain, honestly.
"""
import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from . import system
from .deployment import Deployment, ConfigError, read_env

TEMPLATES = Path(__file__).resolve().parents[1] / 'templates' / 'systemd'

UNIT_SUBSTITUTIONS = (
    '@REPO@', '@PYTHON@', '@PANEL_USER@', '@PANEL_GROUP@', '@WORKER_USER@',
    '@BACKEND_GROUP@', '@SHARED_GROUP@', '@CONFIG_ROOT@',
    '@STATE_ROOT@', '@WORKER_ROOT@', '@STUDIOS@', '@FEED_STATE@', '@COVERS@',
    '@TORRENT_HOST@', '@SHOWS_HOST@', '@MEDIA_GROUP@',
)

# Service configs holding credentials the web uid must never read: only the
# backend uid (operations/cleanup/automations) talks to these apps.
BACKEND_PRIVATE_CONFIGS = ('transmission', 'prowlarr', 'radarr', 'lidarr',
                           'jackett')
# sonarr.json is read by the web role (per-series policy UI); jellyfin.json
# holds a READONLY metadata identity the web login flow needs for base_url.
SHARED_CONFIGS = ('sonarr', 'jellyfin', 'config', 'security', 'studios')


def _deployment(args):
    return Deployment.load(env_file=args.env_file, config_root=args.config_root,
                           state_root=args.state_root)


def _connections_safe(dep):
    """Service configs if present; empty dict on any config problem."""
    try:
        return dep.connections()
    except Exception:
        return {}


def _discover(dep, docker=None):
    from . import discovery
    conn = _connections_safe(dep)
    out = {}
    for name in discovery.SERVICE_ORDER:
        try:
            out[name] = discovery.detect(name, conn, docker=docker)
        except Exception as exc:
            # FAIL CLOSED: a broken probe must never look "installable".
            # 'error' is treated like a blocked/uncertain state everywhere:
            # install will not compose-up such a service, check flags it.
            # The declared base_url is retained in `external` so a declared
            # service can never degrade to external=False/unavailable, which
            # would make plan/install compose-up a duplicate local instance.
            declared = conn.get(name) or {}
            out[name] = {'class': 'error', 'blocked': True,
                         'detail': 'probe failed: %s (never auto-installs; '
                                   'verify %s or fix the endpoint)' % (
                                       type(exc).__name__,
                                       declared.get('base_url') or 'connection json'),
                         'external': bool(declared.get('base_url')),
                         'managed': False}
    return out


def _plan_lines(dep, docker=None):
    from . import discovery
    lines = []
    did, ver = system.distro()
    lines.append('distro: %s %s%s' % (did or '?', ver or '?',
                                      '' if system.supported_distro() else '  (NOT supported; need Debian 12/13 or Ubuntu 24.04)'))
    engine, invocation, version = system.docker_info()
    lines.append('docker: %s; compose: %s %s' % (
        'present' if engine else 'MISSING',
        ' '.join(invocation) if invocation else 'MISSING',
        version or ''))
    ok, msg = system.compose_v2_check(invocation, version) if invocation else (False, 'compose v2 required')
    if not ok:
        lines.append('  ! %s' % msg)
    services = _discover(dep, docker=docker)
    for name in discovery.SERVICE_ORDER:
        r = services[name]
        flags = []
        if r.get('external'):
            flags.append('external/adopted — will NOT be installed locally')
        if r.get('managed'):
            flags.append('managed by compose')
        lines.append('%-14s %-13s %s%s' % (name, r['class'], r['detail'],
                                           (' [' + '; '.join(flags) + ']') if flags else ''))
    conn = _connections_safe(dep)
    missing_cfg = [n for n in ('sonarr', 'prowlarr', 'transmission', 'jellyfin')
                   if n not in conn]
    if missing_cfg:
        lines.append('config: missing service configs: %s (run `configure`)' % ', '.join(missing_cfg))
    else:
        lines.append('config: service configs present')
    return lines, services


def cmd_plan(args):
    dep = _deployment(args)
    print('PLAN (read-only%s)' % ('; --no-mutate is implicit and accepted' if args.no_mutate else ''))
    lines, services = _plan_lines(dep)
    for line in lines:
        print('  ' + line)
    env = read_env(dep.compose_dir / '.env')
    if env.get('MEDIA_ROOT'):
        sentinel = Path(env['MEDIA_ROOT']) / '.dubs-storage-ok'
        print('storage: MEDIA_ROOT=%s sentinel %s' % (
            env['MEDIA_ROOT'], 'armed' if sentinel.is_file() else 'MISSING (arm once after verifying the mount)'))
    else:
        print('storage: compose/.env MEDIA_ROOT not set — compose will refuse to start')
    to_install = [n for n, r in services.items()
                  if r['class'] == 'unavailable' and not r.get('external')]
    core = sorted(n for n in to_install if n not in ('jackett', 'flaresolverr'))
    extras = sorted(n for n in to_install if n in ('jackett', 'flaresolverr'))
    print('install would: apt deps; create web uid %s + backend uid %s-worker + '
          '%s code copy; create dirs; '
          'render systemd units; compose up missing core: %s%s' % (
              dep.user, dep.user, dep.install_root,
              ', '.join(core) if core else '(none)',
              ('; extras profile (opt-in): ' + ', '.join(extras)) if extras else ''))
    print('remaining operator steps after install/configure/bootstrap: tracker accounts, '
          'Jellyfin owner+readonly users, Seerr wizard, config.json values')
    return 0


def cmd_doctor(args):
    dep = _deployment(args)
    problems = []
    lines, services = _plan_lines(dep)
    for line in lines:
        print('  ' + line)
    from . import discovery
    for name, r in services.items():
        if r['class'] == 'ready':
            continue
        if r['class'] == 'unavailable' and r.get('external'):
            continue
        problems.append('%s: %s (%s)' % (name, r['class'], r['detail']))
    # storage
    env = read_env(dep.compose_dir / '.env')
    if env.get('MEDIA_ROOT'):
        sentinel = Path(env['MEDIA_ROOT']) / '.dubs-storage-ok'
        if not sentinel.is_file():
            problems.append('storage sentinel missing: %s' % sentinel)
    else:
        problems.append('compose/.env MEDIA_ROOT is not set')
    ok, reason = _storage_ready_safe(dep)
    if not ok:
        problems.append('storage not ready: %s' % reason)
    # config sanity + perms (explicit per-file policy; 0640 group-readable is
    # the CORRECT state for web-needed configs — only world-readable or a
    # backend-private file leaking to the shared group is a problem)
    cfg = Path(dep.config_root)
    for fname in ('config.json', 'security.json'):
        p = cfg / fname
        if not p.exists():
            problems.append('missing %s (run `configure`)' % p)
            continue
        mode = p.stat().st_mode & 0o777
        if mode & 0o004:
            problems.append('%s is world-readable (%04o); chmod 0640' % (p, mode))
    accounts = _account_names(dep)
    import grp as _grp
    for svc in BACKEND_PRIVATE_CONFIGS:
        p = cfg / (svc + '.json')
        if not p.exists():
            continue
        st = p.stat()
        mode = st.st_mode & 0o777
        try:
            gname = _grp.getgrgid(st.st_gid).gr_name
        except KeyError:
            gname = str(st.st_gid)
        if mode & 0o004 or gname != accounts['backend_group']:
            problems.append('%s must be backend-private root:%s 0640 (found %04o, '
                            'group %s) — the web uid must not read it'
                            % (p.name, accounts['backend_group'], mode, gname))
    try:
        sec = json.loads((cfg / 'security.json').read_text())
        if not sec.get('signing_key'):
            problems.append('security.json has no signing_key')
        if not sec.get('owner_names') or not sec.get('owner_ids'):
            problems.append('security.json owner_names/owner_ids empty — the panel '
                            'login stays closed until the Jellyfin owner allowlist '
                            'is filled')
        if not sec.get('allowed_origins'):
            problems.append('security.json allowed_origins empty — logins from browsers will fail')
    except FileNotFoundError:
        pass
    # units + timers
    owners = _owners_configured(dep)
    for unit in ('dubs-panel@%s' % dep.instance, 'dubs-worker@%s' % dep.instance,
                 'dubs-operations@%s' % dep.instance, 'dubs-myshows-feed',
                 'dubs-recent-episodes.timer', 'dubs-recovery.timer',
                 'dubs-jackett-sync.timer'):
        active, enabled = system.unit_state(unit)
        if enabled in ('not-found', 'disabled'):
            if not Path('/etc/systemd/system/%s' % unit).exists() and '@' not in unit:
                problems.append('unit not installed: %s (run `install`)' % unit)
            elif enabled == 'disabled':
                # A disabled panel with an unconfigured owner allowlist is the
                # deliberate install gate, not a problem.
                if unit.startswith('dubs-panel') and not owners:
                    continue
                problems.append('unit disabled: %s' % unit)
    # feed
    feed_ok, feed_detail = _feed_health(dep)
    if not feed_ok:
        problems.append('feed: %s' % feed_detail)
    else:
        print('  feed: %s' % feed_detail)
    if problems:
        print('DOCTOR: %d problem(s)' % len(problems))
        for p in problems:
            print('  ! %s' % p)
        return 1
    print('DOCTOR: no problems found')
    return 0


def _storage_ready_safe(dep):
    sys.path.insert(0, str(dep.repo / 'src'))
    os.environ.setdefault('SERIES_CONTROL_CONFIG_ROOT', str(dep.config_root))
    try:
        import runtime as rt
        rt.reset_cache()
        return rt.storage_ready()
    except Exception as exc:
        return False, 'storage check failed: %s' % type(exc).__name__
    finally:
        rt.reset_cache()


def _feed_health(dep):
    import urllib.request
    try:
        with urllib.request.urlopen(
                'http://127.0.0.1:%d/health' % dep.feed_port, timeout=3) as r:
            data = json.loads(r.read(4096))
        updated = data.get('updated', '?')
        return True, 'serving, last refresh %s' % updated
    except Exception:
        return False, 'no answer on 127.0.0.1:%d/health' % dep.feed_port


def _unit_text(dep, template_text, accounts=None, media_group=None):
    """Pure template substitution (testable without touching /etc).

    accounts is the dict returned by _account_names/_ensure_user; media_group
    is the compose PGID group NAME (empty string when none) — only the worker
    unit ever references it.
    """
    accounts = accounts or {
        'web_user': dep.user, 'shared_group': dep.group,
        'worker_user': dep.user + '-worker',
        'backend_group': dep.user + '-backend',
    }
    subs = {
        '@REPO@': str(dep.repo), '@PYTHON@': dep.python,
        '@PANEL_USER@': accounts['web_user'],
        '@PANEL_GROUP@': accounts['shared_group'],
        '@SHARED_GROUP@': accounts['shared_group'],
        '@WORKER_USER@': accounts['worker_user'],
        '@BACKEND_GROUP@': accounts['backend_group'],
        '@CONFIG_ROOT@': str(dep.config_root), '@STATE_ROOT@': str(dep.state_root),
        '@WORKER_ROOT@': str(dep.worker_root), '@STUDIOS@': str(dep.studios),
        '@FEED_STATE@': str(dep.feed_state), '@COVERS@': str(dep.covers),
        '@TORRENT_HOST@': str(dep.torrent_host), '@SHOWS_HOST@': str(dep.shows_host),
        '@MEDIA_GROUP@': media_group or '',
    }
    text = template_text
    for k, v in subs.items():
        text = text.replace(k, v)
    return text


def _render_units(dep, accounts=None, media_group=None):
    written = []
    for template in sorted(TEMPLATES.glob('*')):
        text = _unit_text(dep, template.read_text(), accounts=accounts,
                          media_group=media_group)
        target = Path('/etc/systemd/system') / template.name
        if target.exists() and target.read_text() == text:
            continue
        target.write_text(text)
        written.append(target.name)
    return written


def _compose_up(dep, names=None, extras=False, no_deps=False):
    _, invocation, _ = system.docker_info()
    ok, msg = system.compose_v2_check(invocation, system.docker_info()[2])
    if not ok:
        raise system.CommandError(msg)
    cmd = invocation + ['-f', str(dep.compose_dir / 'docker-compose.yml')]
    if extras:
        cmd += ['--profile', 'extras']
    cmd += ['up', '-d']
    if no_deps:
        # Explicitly named, already-running services only: compose would
        # otherwise also start depends_on dependencies — including services
        # an operator intentionally stopped or that belong to an
        # adopted/external setup this compose file does not own.
        cmd += ['--no-deps']
    cmd += list(names) if names else []
    return system.run(cmd, check=False, timeout=600)


def _ensure_transmission_credentials(env_path):
    """Generate RPC credentials into compose/.env once; never print them.

    The file is private (0600) BEFORE the first secret byte is appended —
    never created world-readable and chmodded afterwards. Existing
    TRANSMISSION_USER/PASS are reused idempotently (an adopted daemon keeps
    its credentials); only genuinely missing keys are generated.
    """
    env_path = Path(env_path)
    env = read_env(env_path)
    missing = [k for k in ('TRANSMISSION_USER', 'TRANSMISSION_PASS')
               if not env.get(k)]
    if not missing:
        # Idempotent reuse: keep the existing credential pair, but never let
        # the secret file stay group/world-readable.
        if env_path.exists() and os.stat(env_path).st_mode & 0o077:
            os.chmod(env_path, 0o600)
        return None
    import secrets
    lines = []
    if not env.get('TRANSMISSION_USER'):
        lines.append('TRANSMISSION_USER=dubs-%s' % secrets.token_urlsafe(6))
    if not env.get('TRANSMISSION_PASS'):
        lines.append('TRANSMISSION_PASS=%s' % secrets.token_urlsafe(24))
    if env_path.exists():
        os.chmod(env_path, 0o600)   # before appending any secret byte
        fd = os.open(str(env_path), os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
    else:
        fd = os.open(str(env_path),
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as f:
        f.write('\n# Generated Transmission RPC credentials (secret; not shown)\n')
        for line in lines:
            f.write(line + '\n')
    return [k for k, _ in (line.split('=', 1) for line in lines)]


def _resolve_group_name(gid):
    """Group NAME for a numeric gid, or None when it is not in the group db."""
    proc = system.run(['getent', 'group', str(gid)], check=False)
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.split(':', 1)[0]
    return None


def _account_names(dep):
    """Pure identity model (no system mutation): the two-uid split.

    web uid (dep.user, primary group dep.group): serves the UI, owns web
      state, NO media access, never a member of the media or backend groups.
    backend uid (dep.user + '-worker'): runs operations/cleanup/feed/oneshot
      automations; primary group is the private backend group that owns
      backend-only credentials; media access ONLY via the compose PGID group
      as a supplementary group (library deletes go through the Sonarr API).
    shared status group (dep.group): lets the web role read public backend
      output (job/snapshot/history files, 0640 in setgid 0750 dirs).
    """
    worker_user = dep.user + '-worker'
    backend_group = dep.user + '-backend'
    media_group = _resolve_group_name(dep.pgid)
    # The media group must never alias one of our own groups, or "web is not
    # a media member" would silently become false.
    if media_group in (dep.group, backend_group, worker_user):
        media_group = None
    return {'web_user': dep.user, 'shared_group': dep.group,
            'worker_user': worker_user, 'backend_group': backend_group,
            'media_group': media_group}


def _ensure_user(dep):
    """Create the isolation accounts (idempotent); never gives the web uid
    media or backend access.

    Mutations: groupadd dep.group, groupadd dep.user-backend,
    useradd dep.user (primary dep.group), useradd dep.user-worker (primary
    dep.user-backend), then usermod -aG <shared>[,<media>] on the WORKER uid
    only. The web uid is never added to any supplementary group.
    Returns the _account_names dict.
    """
    names = _account_names(dep)
    if system.run(['getent', 'group', names['shared_group']], check=False).returncode != 0:
        system.run(['groupadd', '--system', names['shared_group']])
    if system.run(['getent', 'group', names['backend_group']], check=False).returncode != 0:
        system.run(['groupadd', '--system', names['backend_group']])
    if system.run(['getent', 'passwd', names['web_user']], check=False).returncode != 0:
        system.run(['useradd', '--system', '--gid', names['shared_group'],
                    '--home-dir', '/var/lib/%s' % names['web_user'],
                    '--create-home', '--shell', '/usr/sbin/nologin',
                    names['web_user']])
    if system.run(['getent', 'passwd', names['worker_user']], check=False).returncode != 0:
        system.run(['useradd', '--system', '--gid', names['backend_group'],
                    '--home-dir', '/var/lib/%s' % names['worker_user'],
                    '--create-home', '--shell', '/usr/sbin/nologin',
                    names['worker_user']])
    # Worker supplementary groups: shared status group (reads web-owned
    # preferences/myshows-auth, public job markers) + compose PGID media
    # group (torrent write). Nothing else — in particular never docker.
    supplementary = [names['shared_group']]
    if names['media_group']:
        supplementary.append(names['media_group'])
    system.run(['usermod', '-aG', ','.join(supplementary), names['worker_user']],
               check=False)
    return names


# Whitelisted top-level directories copied to the install root: runtime code,
# the installer itself (re-runnable from /opt), systemd templates and compose.
SYNC_DIRS = ('src', 'automations', 'installer', 'templates')
# Public top-level files (docs/licensing/meta); everything else in the
# checkout (private keys, ignored runtime configs, .env.local, state, data,
# backups, venv, .git, node_modules, tests, docs) is NEVER copied.
SYNC_FILES = ('studios.json', 'requirements.txt', 'README.md', 'LICENSE',
              'THIRD_PARTY.md')
# Inside compose/ only real compose assets are copied — among env files only
# .env.example; operator .env/.env.local and any other dotfile stay behind.
COMPOSE_SYNC_SUFFIXES = ('.yml', '.yaml', '.sh', '.example')


def _sync_code(dep):
    """Whitelist-copy the project into install_root (root:<shared>, 0755/0644).

    Skipped when running from the install tree itself. Only SYNC_DIRS,
    compose assets (docker-compose.yml/*.sh/.env.example) and SYNC_FILES are
    copied, so an ignored secret in the checkout (runtime configs, private
    keys, .env.local, state, venv) can never become readable under /opt.
    Files already present in the install root are NOT touched: no chmod or
    chown walk over the whole tree, an operator compose/.env stays 0600 and
    byte-identical. Units run with ProtectHome=yes, so an install root under
    /root or /home is refused outright.
    """
    src, dst = Path(dep.source_repo), Path(dep.install_root)
    if src.resolve() == dst.resolve():
        return None
    resolved = dst.resolve()
    for base in (Path('/root'), Path('/home')):
        if base == resolved or base in resolved.parents:
            raise ConfigError(
                'install_root %s sits under %s but every unit runs with '
                'ProtectHome=yes — the code must live in /opt (e.g. /opt/dubs)'
                % (dst, base))
    dst.mkdir(parents=True, exist_ok=True)
    copied = []

    for name in SYNC_DIRS:
        s = src / name
        d = dst / name
        if not s.is_dir():
            continue
        # Snapshot what already exists: an operator file inside a copied
        # directory (e.g. a key under src/) must keep its mode/owner — only
        # NEWLY created paths get 0755/0644 and root:<shared>.
        created = not d.exists()
        pre = {d}
        if d.exists():
            for r, dirs, files in os.walk(d):
                pre.add(Path(r))
                pre.update(Path(r) / x for x in dirs)
                pre.update(Path(r) / x for x in files)
        shutil.copytree(s, d,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc',
                                                      '.dubs-*'),
                        dirs_exist_ok=True)
        if created:
            os.chmod(d, 0o755)
            copied.append(d)
        for r, dirs, files in os.walk(d):
            for path, is_dir in ([(Path(r) / x, True) for x in dirs] +
                                 [(Path(r) / x, False) for x in files]):
                if path in pre:
                    continue
                os.chmod(path, 0o755 if is_dir else 0o644)
                copied.append(path)

    comp = src / 'compose'
    if comp.is_dir():
        dcomp = dst / 'compose'
        created = not dcomp.exists()
        dcomp.mkdir(parents=True, exist_ok=True)
        for entry in sorted(comp.iterdir()):
            if entry.is_dir() or entry.name.startswith('.dubs-'):
                continue
            if entry.suffix not in COMPOSE_SYNC_SUFFIXES:
                continue  # .env, .env.local, keys, editor droppings: never
            target = dcomp / entry.name
            shutil.copyfile(entry, target)
            os.chmod(target, 0o644)
            copied.append(target)
        if created:
            os.chmod(dcomp, 0o755)
            copied.append(dcomp)

    for name in SYNC_FILES:
        s = src / name
        if s.is_file():
            target = dst / name
            shutil.copyfile(s, target)
            os.chmod(target, 0o644)
            copied.append(target)

    # Chown only what this copy created — never a recursive walk over the
    # install root (operator files there keep their owner and 0600 modes).
    for path in copied:
        system.run(['chown', 'root:%s' % dep.group, str(path)], check=False)
    return dst


def _harden_config_perms(dep, accounts=None):
    """Explicit ownership/modes for config_root contents (idempotent).

    Backend-only credentials (transmission/prowlarr/radarr/lidarr/jackett) are
    root:<backend group> 0640 — the web uid cannot read them even though the
    directory is traversable. Web-needed configs (sonarr, the readonly
    jellyfin identity, config/security/studios) are root:<shared group> 0640.
    TLS private keys are web-uid-only 0600. The directory itself is
    root:<shared group> 0750.
    """
    accounts = accounts or _account_names(dep)
    root = Path(dep.config_root)
    if not root.is_dir():
        return
    for svc in BACKEND_PRIVATE_CONFIGS:
        p = root / (svc + '.json')
        if p.exists():
            os.chmod(p, 0o640)
            system.run(['chown', 'root:%s' % accounts['backend_group'], str(p)],
                       check=False)
    for name in SHARED_CONFIGS:
        p = root / (name + '.json')
        if p.exists():
            os.chmod(p, 0o640)
            system.run(['chown', 'root:%s' % accounts['shared_group'], str(p)],
                       check=False)
    for p in root.iterdir():
        if p.is_file() and (p.suffix in ('.key', '.pem') or p.name.endswith('.key')):
            os.chmod(p, 0o600)
            system.run(['chown', '%s:%s' % (accounts['web_user'],
                                            accounts['shared_group']), str(p)],
                       check=False)
    os.chmod(root, 0o750)
    system.run(['chown', 'root:%s' % accounts['shared_group'], str(root)],
               check=False)


def _create_media_subdirs(media, puid, pgid):
    """Create torrents/shows/movies/music under an ARMED media root only.

    Sentinel-first mount order: returns pending strings instead of creating
    stub directories when the operator-verified .dubs-storage-ok marker is
    absent (the same marker compose storage-sentinel.sh checks) or when the
    parent of MEDIA_ROOT is missing (unmounted external disk). Never
    world-writable; ownership is the compose PUID/PGID pair.
    """
    media = Path(media)
    sentinel = media / '.dubs-storage-ok'
    if not sentinel.is_file():
        return ['MEDIA_ROOT subdirs NOT created: sentinel %s is missing — verify '
                'the mount, arm it (install -m 0644 /dev/null "%s"), then re-run '
                'install --create-storage' % (sentinel, sentinel)]
    media.mkdir(parents=True, exist_ok=True)
    os.chmod(media, 0o2775)
    system.run(['chown', '%s:%s' % (puid, pgid), str(media)], check=False)
    for sub in ('torrents', 'shows', 'movies', 'music'):
        d = media / sub
        d.mkdir(parents=True, exist_ok=True, mode=0o2775)
        os.chmod(d, 0o2775)
        system.run(['chown', '%s:%s' % (puid, pgid), str(d)], check=False)
    return []


def cmd_install(args):
    dep = _deployment(args)
    if not system.is_root():
        print('install requires root (sudo)'); return 1
    did, ver = system.distro()
    print('install: distro %s %s' % (did, ver))
    # 1. Docker engine + Compose v2 (preserves a working existing Docker).
    for item in system.ensure_docker_deps():
        print('installed: %s' % item)
    # 2. Runtime deps for panel/automations/doctor.
    system.apt_install(['python3-requests', 'python3-yaml', 'curl'], did, ver)
    print('installed: python3-requests python3-yaml curl')
    # 3. Isolation accounts + whitelisted code copy.
    accounts = _ensure_user(dep)
    print('identity: web uid %s (group %s; NO media access); backend uid %s '
          '(private group %s; supplementary: %s%s); media writes are the '
          'worker uid only, library deletes go through the Sonarr API'
          % (accounts['web_user'], accounts['shared_group'],
             accounts['worker_user'], accounts['backend_group'],
             accounts['shared_group'],
             (',' + accounts['media_group']) if accounts['media_group'] else ''))
    copied = _sync_code(dep)
    print('code: %s' % ('whitelist-copied to %s (root:%s, 0755/0644 — units only read)'
                        % (copied, accounts['shared_group']) if copied
                        else 'running from the install tree'))
    # 4. Config + state directories (explicit ownership per uid role).
    for d, owner, mode in (
            (dep.config_root, 'root:%s' % accounts['shared_group'], 0o750),
            (dep.state_root, '%s:%s' % (accounts['web_user'],
                                        accounts['shared_group']), 0o750),
            # setgid so backend-written public job/snapshot files land in the
            # shared group and the web role can read them (0640).
            (dep.worker_root, '%s:%s' % (accounts['worker_user'],
                                         accounts['shared_group']), 0o2750),
            (dep.feed_state, '%s:%s' % (accounts['worker_user'],
                                        accounts['shared_group']), 0o2750),
            (dep.covers, '%s:%s' % (accounts['web_user'],
                                    accounts['shared_group']), 0o2775)):
        Path(d).mkdir(parents=True, exist_ok=True, mode=mode)
        os.chmod(d, mode)
        system.run(['chown', owner, str(d)], check=False)
    _harden_config_perms(dep, accounts)
    print('dirs ready: config(0750)/state(web)/worker(backend+shared)/feed/covers')
    # 5. compose/.env (private from creation)
    env_path = dep.compose_dir / '.env'
    example = dep.compose_dir / '.env.example'
    if not env_path.exists() and example.exists():
        fd = os.open(str(env_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(example.read_bytes())
        print('created %s from .env.example (0600) — fill MEDIA_ROOT / DUBS_DATA_ROOT / PUID / PGID' % env_path)
    env = read_env(env_path)
    media = env.get('MEDIA_ROOT')
    data = env.get('DUBS_DATA_ROOT')
    pending = []
    if not media or not data:
        pending.append('fill MEDIA_ROOT and DUBS_DATA_ROOT in %s (absolute paths)' % env_path)
    else:
        from .deployment import require_absolute
        require_absolute('MEDIA_ROOT', media)
        require_absolute('DUBS_DATA_ROOT', data)
        puid, pgid = env.get('PUID', dep.puid), env.get('PGID', dep.pgid)
        # Mount order: media subdirs are created ONLY after the operator has
        # armed the sentinel on the VERIFIED mount — never as stubs under a
        # missing external disk. storage_sentinel stays true; it is never
        # silently disabled.
        if args.create_storage:
            created_pending = _create_media_subdirs(media, puid, pgid)
            if created_pending:
                pending.extend(created_pending)
            else:
                print('storage subdirs created under %s (torrents/shows/movies/music, '
                      '02775 %s:%s; sentinel was armed first)' % (media, puid, pgid))
        if not Path(media, '.dubs-storage-ok').exists():
            pending.append('arm the storage sentinel once the mount is verified:\n'
                           '    install -m 0644 /dev/null "%s/.dubs-storage-ok"' % media)
        # Transmission RPC credentials for daemon + panel client (item: fresh
        # daemon must actually require auth, not just the client config).
        generated = _ensure_transmission_credentials(env_path)
        if generated:
            print('generated secret(s) into %s: %s (values not shown)'
                  % (env_path, ', '.join(generated)))
        env = read_env(env_path)
        # Service config dirs are owned by the container PUID/PGID (image defaults);
        # the panel user only needs group read via membership in that group.
        for svc in ('seerr', 'jellyfin', 'sonarr', 'radarr', 'lidarr', 'transmission',
                    'prowlarr', 'jackett'):
            d = Path(data) / svc
            d.mkdir(parents=True, exist_ok=True, mode=0o775)
            system.run(['chown', '%s:%s' % (puid, pgid), str(d)], check=False)
            system.run(['chmod', 'g+rwx,o-rwx', str(d)], check=False)
        # 6. Start missing services only (external/adopted never installed).
        services = _discover(dep)
        missing = [n for n, r in services.items()
                   if r['class'] == 'unavailable' and not r.get('external')
                   and n != 'flaresolverr']
        names = {'jellyfin', 'seerr', 'sonarr', 'radarr', 'lidarr',
                 'transmission', 'prowlarr'} & set(missing)
        if names:
            proc = _compose_up(dep, names=sorted(names), extras=False)
            print('compose up: %s (rc=%d)' % (', '.join(sorted(names)), proc.returncode))
            if proc.returncode != 0:
                pending.append('compose up failed — check docker compose logs')
        else:
            print('compose: no missing core services')
    # 7. systemd units (panel roles + feed + automations), enabled but NOT started.
    written = _render_units(dep, media_group=media_group)
    system.systemctl('daemon-reload', '')
    for unit in ('dubs-worker@%s' % dep.instance, 'dubs-operations@%s' % dep.instance,
                 'dubs-myshows-feed', 'dubs-recent-episodes.timer',
                 'dubs-recovery.timer', 'dubs-jackett-sync.timer'):
        system.systemctl('enable', unit, check=False)
    # The web panel is enabled ONLY once the Jellyfin owner allowlist is
    # valid: an empty security.json owner_names/owner_ids means the login
    # would stay closed anyway, and the unit must not auto-start at boot
    # before the operator has configured real owners.
    if _owners_configured(dep):
        system.systemctl('enable', 'dubs-panel@%s' % dep.instance, check=False)
    else:
        pending.append('panel unit NOT enabled (owner allowlist unconfigured): fill '
                       'security.json owner_names/owner_ids (`configure`), then '
                       'systemctl enable --now dubs-panel@%s' % dep.instance)
    if written:
        print('units written: %s' % ', '.join(written))
    # 8. Feed reachability from the bridge: bind loopback + PRIVATE docker
    # bridge gateway IPs only — the personal watchlist feed must never bind
    # a wildcard or public address.
    binds = dep.feed_bind
    gateways = _bridge_gateways()
    if gateways:
        binds = tuple(dict.fromkeys(list(dep.feed_bind) + gateways))
    dropin = Path('/etc/systemd/system/dubs-myshows-feed.service.d')
    if binds != dep.feed_bind:
        dropin.mkdir(parents=True, exist_ok=True)
        (dropin / 'bind.conf').write_text(
            '[Service]\nEnvironment=DUBS_FEED_BIND=%s\n' % ','.join(binds))
        system.systemctl('daemon-reload', '')
        print('feed binds: %s (private bridge gateways included so containerized '
              'Sonarr can reach the host feed; nothing public)' % ','.join(binds))
    else:
        pending.append('compose not up yet — bridge gateway for the feed bind '
                       'will be added on the next `install` run')
    print('PENDING OPERATOR STEPS' + ('' if not pending else ':'))
    for p in pending:
        print('  - %s' % p)
    print('next: fill compose/.env and service API keys (`configure`), then `bootstrap`')
    return 0


def _is_private_gateway(gw):
    """True only for private, non-wildcard addresses — never 0.0.0.0/::."""
    import ipaddress
    try:
        ip = ipaddress.ip_address(gw)
    except ValueError:
        return False
    return not ip.is_unspecified and ip.is_private


def _bridge_gateways():
    if not system.have('docker'):
        return []
    proc = system.run(['docker', 'network', 'inspect', 'dubs_default'], check=False)
    if proc.returncode != 0:
        return []
    try:
        nets = json.loads(proc.stdout)
    except ValueError:
        return []
    out = []
    for net in nets:
        for cfg_ in net.get('IPAM', {}).get('Config', []) or []:
            gw = cfg_.get('Gateway')
            if gw and _is_private_gateway(gw):
                out.append(gw)
    return out


def _owners_configured(dep):
    """True when security.json carries a real Jellyfin owner allowlist."""
    try:
        sec = json.loads((Path(dep.config_root) / 'security.json').read_text())
    except (FileNotFoundError, ValueError, OSError):
        return False
    return bool(sec.get('owner_names')) and bool(sec.get('owner_ids'))


def _config_gate(dep):
    """Readiness problems that must gate `check`/`configure` honestly.

    With skeleton defaults (empty owner_names/owner_ids/allowed_origins, empty
    API keys) nothing may claim readiness: every gap is returned as a PENDING
    problem and `check` exits nonzero until the operator fills real values.
    """
    problems = []
    sec_path = Path(dep.config_root) / 'security.json'
    try:
        sec = json.loads(sec_path.read_text())
    except (OSError, ValueError) as exc:
        problems.append('PENDING: security.json missing/invalid (%s; run `configure`)'
                        % type(exc).__name__)
        sec = {}
    if not sec.get('owner_names') or not sec.get('owner_ids'):
        problems.append('PENDING: security.json owner_names/owner_ids empty — the '
                        'panel login stays closed until the Jellyfin owner '
                        'allowlist is filled')
    if not sec.get('allowed_origins'):
        problems.append('PENDING: security.json allowed_origins empty — browser '
                        'logins behind the reverse proxy will be refused')
    conn = _connections_safe(dep)
    for name in ('sonarr', 'prowlarr', 'jellyfin', 'transmission'):
        cfg = conn.get(name)
        if not cfg:
            problems.append('PENDING: %s.json missing (run `configure`)' % name)
            continue
        if name == 'transmission':
            if not (cfg.get('user') and cfg.get('password')):
                problems.append('PENDING: transmission RPC credentials missing '
                                '(transmission.json)')
        elif name == 'jellyfin':
            if not (cfg.get('token') or cfg.get('key')):
                problems.append('PENDING: jellyfin.json readonly metadata token '
                                'missing (never an admin token)')
        elif not cfg.get('key'):
            problems.append('PENDING: %s API key missing (%s.json)' % (name, name))
    return problems


def cmd_configure(args):
    dep = _deployment(args)
    env_path = dep.compose_dir / '.env'
    generated = None
    if env_path.exists():
        generated = _ensure_transmission_credentials(env_path)
        if generated:
            print('generated secret(s) into %s: %s (values not shown)'
                  % (env_path, ', '.join(generated)))
    env = read_env(env_path)
    sys.path.insert(0, str(dep.repo / 'installer'))
    import configgen
    created, skipped = configgen.generate(
        dep.config_root, force=args.force,
        torrent_host=dep.torrent_host, shows_host=dep.shows_host,
        media_root=dep.media_root,
        transmission_user=env.get('TRANSMISSION_USER'),
        transmission_pass=env.get('TRANSMISSION_PASS'))
    if created:
        print('created: %s' % ', '.join(str(p) for p in created))
    if skipped:
        print('kept existing: %s (--force overwrites)' % ', '.join(str(p) for p in skipped))
    configgen.validate(dep.config_root)
    _harden_config_perms(dep)
    print('config skeleton validates through src/runtime')
    print('skeletons only — NOT functional until you fill:')
    print('  config.json: login (MyShows), public_url, jellyfin_url')
    print('  security.json: owner_names + owner_ids (Jellyfin owners — without them '
          'the panel login stays closed), allowed_origins, trusted_proxies, public_hosts')
    print('  sonarr/prowlarr keys; transmission user/password; radarr/lidarr keys '
          '(optional apps); jackett key (extras profile)')
    print('  jellyfin.json: base_url + READONLY metadata user token and userId — '
          'NEVER an admin token')
    gate = _config_gate(dep)
    if gate:
        print('CONFIGURE: %d PENDING item(s) — the stack is NOT ready:' % len(gate))
        for p in gate:
            print('  ! %s' % p)
    else:
        print('CONFIGURE: owners, trust origins and service keys present')
    return 0


def _feed_reachable(feed_url):
    """(ok, detail) — /health must answer with VALID JSON.

    The Sonarr Custom Import list parses this endpoint at creation time, so a
    connection-refused, 503 (empty cache, first refresh still running) or
    non-JSON answer never counts as wired. No external MyShows source is
    required to boot an empty cache — bootstrap just defers honestly.
    """
    import urllib.request
    try:
        with urllib.request.urlopen(feed_url.rstrip('/') + '/health', timeout=5) as r:
            body = r.read(65536)
            status = getattr(r, 'status', '?')
        json.loads(body)
        return True, 'HTTP %s, valid JSON' % status
    except ValueError:
        return False, 'endpoint answered but not with valid JSON'
    except Exception as exc:
        return False, '%s: %s' % (type(exc).__name__, exc)


def _warn_transmission_parity(dep):
    """Print an honest warning when transmission.json differs from compose .env.

    The managed 'Dubs Transmission' clients bootstrap wires use the
    transmission.json credentials; the fresh compose daemon requires the
    TRANSMISSION_USER/PASS from .env. On a fresh owned install they must be
    the same pair (configure seeds them); an adopted external daemon may
    legitimately differ, so this warns instead of failing.
    """
    env = read_env(dep.compose_dir / '.env')
    t = _connections_safe(dep).get('transmission') or {}
    mismatched = [env_key for env_key, cfg_key in
                  (('TRANSMISSION_USER', 'user'), ('TRANSMISSION_PASS', 'password'))
                  if env.get(env_key) and t.get(cfg_key) and env[env_key] != t[cfg_key]]
    if mismatched:
        print('WARNING: transmission.json %s differ from compose .env %s — the '
              'managed Dubs Transmission client would fail to authenticate the '
              'local daemon. On a fresh owned install re-run `configure --force`; '
              'values are never printed here.'
              % ('/'.join({'TRANSMISSION_USER': 'user',
                           'TRANSMISSION_PASS': 'password'}[k] for k in mismatched),
                 '/'.join(mismatched)))


def cmd_bootstrap(args):
    dep = _deployment(args)
    try:
        import requests  # noqa: F401
    except ImportError:
        print('bootstrap needs python3-requests; run `install` first (doctor '
              'never installs anything)'); return 1
    from . import bootstrap as bs
    studios = json.loads(Path(dep.studios).read_text())
    # The feed unit is a hard dependency: the Custom Import validates the
    # feed at creation time. Make sure the configured private feed is
    # actually started and serving valid JSON before wiring anything.
    unit = 'dubs-myshows-feed'
    active, enabled = system.unit_state(unit)
    if enabled != 'not-found' and active != 'active':
        print('feed unit %s is %s — starting it' % (unit, active))
        system.systemctl('start', unit, check=False)
    gateways = _bridge_gateways() or ['127.0.0.1']
    feed_url = args.feed_url or 'http://%s:%d' % (gateways[0], dep.feed_port)
    ok, detail = _feed_reachable(feed_url)
    if not ok:
        print('feed not serving valid JSON at %s/health (%s).' % (feed_url, detail))
        print('bootstrap refuses to create the Sonarr Custom Import against a '
              'connection-refused or non-JSON endpoint; nothing was wired.')
        print('start and verify the feed first: systemctl start dubs-myshows-feed; '
              'curl %s/health' % feed_url)
        print('if the feed URL differs, pass --feed-url http://host:port')
        return 1
    print('feed reachable: %s (%s)' % (feed_url, detail))
    _warn_transmission_parity(dep)
    report = bs.bootstrap(dep, studios, _veto_format(dep), feed_url)
    print('bootstrap report: %s' % json.dumps(report, ensure_ascii=False, indent=2))
    print('PENDING OPERATOR STEPS:')
    for step in bs.human_steps(report, dep):
        print('  - %s' % step)
    return 0


def _veto_format(dep):
    sys.path.insert(0, str(dep.repo / 'src'))
    os.environ.setdefault('SERIES_CONTROL_CONFIG_ROOT', str(dep.config_root))
    import runtime as rt
    rt.reset_cache()
    try:
        return rt.settings()['veto_format']
    finally:
        rt.reset_cache()


def _panel_health():
    """(ok, detail) for the loopback panel probe (extracted for tests)."""
    import urllib.request
    try:
        req = urllib.request.Request('http://127.0.0.1:%d/' % 9192, method='GET')
        with urllib.request.urlopen(req, timeout=3) as r:
            return True, 'HTTP %s on 9192' % r.status
    except Exception as exc:
        status = getattr(exc, 'code', None)
        if status in (301, 302, 307, 403):
            return True, 'answering (HTTP %s redirect/protection) on 9192' % status
        return False, 'no answer on 9192 (%s)' % type(exc).__name__


def cmd_check(args):
    dep = _deployment(args)
    services = _discover(dep)
    bad = []
    for name, r in services.items():
        print('%-14s %-13s %s' % (name, r['class'], r['detail']))
        if r['class'] not in ('ready',) and not (r.get('external') and r['class'] == 'unavailable'):
            bad.append(name)
    ok, reason = _storage_ready_safe(dep)
    print('storage: %s' % ('ready' if ok else 'NOT READY: %s' % reason))
    if not ok:
        bad.append('storage')
    feed_ok, feed_detail = _feed_health(dep)
    print('feed: %s' % feed_detail)
    if not feed_ok:
        bad.append('feed')
    panel_ok, panel_detail = _panel_health()
    print('panel: %s' % panel_detail)
    if not panel_ok:
        bad.append('panel')
    gate = _config_gate(dep)
    for p in gate:
        print('config: %s' % p)
    if gate:
        bad.append('config')
    if bad:
        print('CHECK: not ready: %s' % ', '.join(bad))
        return 1
    print('CHECK: all components ready')
    return 0


def cmd_update(args):
    dep = _deployment(args)
    src = dep.source_repo
    failures = 0
    if (src / '.git').exists():
        dirty = system.run(['git', '-C', str(src), 'status', '--porcelain'],
                           check=False).stdout.strip()
        if dirty:
            print('repository has local changes; refusing to pull'); return 1
        system.run(['git', '-C', str(src), 'pull', '--ff-only'], timeout=120)
        print('repository updated')
    copied = _sync_code(dep)
    if copied:
        print('code re-copied to %s (operator .env and data dirs untouched)' % copied)
    units_broken = False
    if (dep.compose_dir / '.env').exists():
        _, invocation, _ = system.docker_info()
        if invocation:
            compose = invocation + ['-f', str(dep.compose_dir / 'docker-compose.yml')]
            pull = system.run(compose + ['pull'], check=False, timeout=600)
            if pull.returncode != 0:
                # Propagate the failure instead of silently continuing with
                # stale images.
                print('compose pull failed (rc=%d) — skipping compose up; '
                      'images may be stale' % pull.returncode)
                failures += 1
                units_broken = True
            else:
                # Refresh ONLY compose services that are ALREADY running, and
                # without their dependencies: an intentionally stopped
                # service, an unmanaged dependency and adopted/external
                # services (not in this compose file at all) stay untouched.
                ps = system.run(compose + ['ps', '--services', '--status=running'],
                                check=False)
                running = [s for s in (ps.stdout or '').split() if s]
                if running:
                    proc = _compose_up(dep, names=running, no_deps=True)
                    print('compose up (running only, --no-deps): %s (rc=%d)'
                          % (', '.join(running), proc.returncode))
                    if proc.returncode != 0:
                        print('compose up failed — units NOT restarted on a '
                              'broken stack')
                        failures += 1
                        units_broken = True
                else:
                    print('compose: no running services — nothing started by update')
    if not units_broken:
        # Restart only units that are actually running: a stopped role is a
        # deliberate operator choice and stays stopped.
        system.systemctl('daemon-reload', '')
        for unit in ('dubs-panel@%s' % dep.instance, 'dubs-worker@%s' % dep.instance,
                     'dubs-operations@%s' % dep.instance, 'dubs-myshows-feed'):
            active, _ = system.unit_state(unit)
            if active == 'active':
                system.systemctl('restart', unit, check=False)
        print('running panel units restarted; timers and stopped roles untouched')
    if failures:
        print('UPDATE: finished with %d failure(s)' % failures)
        return 1
    return 0


def cmd_backup(args):
    dep = _deployment(args)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    # Unique archive name: two runs within the same second must not collide,
    # and O_EXCL below guarantees an existing file (or symlink) is never
    # overwritten.
    import secrets as _secrets
    archive = dest / ('dubs-backup-%s-%s.tar.gz' % (stamp, _secrets.token_hex(4)))
    # Unique arcnames: basenames can collide (config_root and state_root are
    # both "series-control*"); the manifest records the exact restore mapping.
    members = [
        ('config', Path(dep.config_root)),
        ('web-state', Path(dep.state_root)),
        ('worker-state', Path(dep.worker_root)),
        ('feed-state', Path(dep.feed_state)),
        ('covers', Path(dep.covers)),
    ]
    if args.with_compose_data and dep.data_root:
        running = system.docker_services()
        if running:
            # Clear refusal: file-copying live container SQLite databases is
            # torn and inconsistent. Quiesced/offline is the only supported
            # mode for compose data.
            print('refusing --with-compose-data: compose services are running '
                  '(%s). Quiesce first (docker compose stop) — live container '
                  'SQLite cannot be archived consistently by file copy.'
                  % ', '.join(sorted(running)))
            return 2
        members.append(('compose-data', Path(dep.data_root)))
    # Collision validation: logical arcnames and restore targets must be
    # pairwise distinct, and two members must never resolve to the same
    # directory (misconfigured roots would silently double-archive).
    seen_names, name_by_path = {}, {}
    for name, m in members:
        if name in seen_names:
            print('refusing: duplicate archive member name %s' % name); return 2
        rp = str(Path(m).resolve())
        if rp in name_by_path:
            print('refusing: member %s resolves to the same directory as %s (%s)'
                  % (name, name_by_path[rp], rp)); return 2
        seen_names[name] = m
        name_by_path[rp] = name
    manifest = {
        'created': stamp, 'user': dep.user, 'group': dep.group,
        'restore': {name: str(path) for name, path in members},
        'note': 'stop units, extract to a staging dir, copy each dir to its '
                'restore path (verify the mount first), start units',
        'sqlite': 'web-state SQLite DBs were snapshotted with the sqlite3 '
                  'backup API (WAL folded in, -wal/-shm not archived)',
    }
    import sqlite3
    import urllib.parse
    import io as _io
    import time as _time
    with tempfile.TemporaryDirectory(prefix='dubs-backup-') as staging:
        # Snapshot web-state SQLite DBs (security.sqlite3 uses WAL) with the
        # sqlite3 backup API: a raw file copy while the panel is running
        # could tear the WAL and lose recent sessions. The staged snapshot is
        # archived instead of the live file; -wal/-shm siblings are folded in.
        snaps = {}
        for name, m in members:
            if name != 'web-state' or not m.is_dir():
                continue
            for idx, p in enumerate(sorted(m.rglob('*.sqlite3')) +
                                    sorted(m.rglob('*.sqlite')) +
                                    sorted(m.rglob('*.db'))):
                uri = 'file:' + urllib.parse.quote(str(p)) + '?mode=ro'
                try:
                    src = sqlite3.connect(uri, uri=True, timeout=5)
                    staged = Path(staging) / ('%d-%s' % (idx, p.name))
                    dst = sqlite3.connect(str(staged))
                    with dst:
                        src.backup(dst)
                    src.close()
                    dst.close()
                    snaps[p.resolve()] = staged
                except sqlite3.Error as exc:
                    print('refusing: cannot snapshot %s (%s) — archive would be '
                          'inconsistent' % (p, type(exc).__name__))
                    return 2
        # Private from creation: 0600 BEFORE any secret byte is written,
        # O_EXCL|O_NOFOLLOW so an existing file or symlink is never opened.
        fd = os.open(str(archive), os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     os.O_NOFOLLOW, 0o600)
        archive_file = os.fdopen(fd, 'wb')
        try:
            with tarfile.open(fileobj=archive_file, mode='w:gz') as tar:
                for name, m in members:
                    if not m.exists():
                        continue

                    def _add_tree(base, arcname, _name=name):
                        for root, dirs, files in os.walk(base):
                            for f in sorted(files):
                                p = Path(root) / f
                                rel = p.relative_to(base)
                                target = (Path(arcname) / rel).as_posix()
                                if p.name.endswith(('-wal', '-shm')):
                                    continue  # folded into the snapshot
                                snap = snaps.get(p.resolve())
                                if snap is not None:
                                    tar.add(str(snap), arcname=target)
                                else:
                                    if p.suffix in ('.sqlite3', '.sqlite', '.db') \
                                            and _name != 'web-state':
                                        print('WARNING: %s archived as a raw file; '
                                              'stop the units for a guaranteed '
                                              'consistent copy' % p)
                                    tar.add(str(p), arcname=target)
                            for d in sorted(dirs):
                                tar.add(str(Path(root) / d),
                                        arcname=(Path(arcname) /
                                                 (Path(root) / d).relative_to(base)
                                                 ).as_posix(), recursive=False)

                    _add_tree(m, name)
                info = tarfile.TarInfo('restore-manifest.json')
                data = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
                info.size = len(data)
                info.mode = 0o600
                info.mtime = int(_time.time())
                tar.addfile(info, fileobj=_io.BytesIO(data))
        finally:
            archive_file.close()
    print('backup: %s' % archive)
    for name, m in members:
        print('  %-14s <- %s%s' % (name, m, '' if m.exists() else '  (missing, skipped)'))
    print('restore: systemctl stop the panel units; tar -xzf into a STAGING dir; '
          'copy each dir from the manifest "restore" mapping to its restore path; '
          'never tar -C / blindly')
    print('NOTE: the archive contains signing keys and API keys — mode 0600, store safely')
    print('NOTE: web-state SQLite is a consistent online snapshot; other SQLite '
          'data is raw — keep units stopped for those')
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog='dubs-installer',
                                description='Dubs media stack installer')
    p.add_argument('--env-file', default=None, help='compose .env path')
    p.add_argument('--config-root', default=None)
    p.add_argument('--state-root', default=None)
    sub = p.add_subparsers(dest='command', required=True)

    plan = sub.add_parser('plan', help='read-only discovery + intended actions')
    plan.add_argument('--no-mutate', action='store_true', dest='no_mutate',
                      help='accepted; plan never mutates')
    plan.set_defaults(func=cmd_plan)

    doc = sub.add_parser('doctor', help='read-only health check')
    doc.set_defaults(func=cmd_doctor)

    ins = sub.add_parser('install', help='install deps, dirs, units, missing services')
    ins.add_argument('--create-storage', action='store_true',
                     help='create MEDIA_ROOT subdirs when the mount exists')
    ins.set_defaults(func=cmd_install)

    cfg = sub.add_parser('configure', help='write config skeletons (never overwrites)')
    cfg.add_argument('--force', action='store_true', help='overwrite managed skeletons')
    cfg.set_defaults(func=cmd_configure)

    boo = sub.add_parser('bootstrap', help='wire Arr integrations via schema APIs')
    boo.add_argument('--feed-url', default=None,
                     help='override the MyShows feed URL Sonarr should pull')
    boo.set_defaults(func=cmd_bootstrap)

    chk = sub.add_parser('check', help='end-to-end readiness')
    chk.set_defaults(func=cmd_check)

    upd = sub.add_parser('update', help='git pull + compose pull/up + restart units')
    upd.set_defaults(func=cmd_update)

    bak = sub.add_parser('backup', help='tar.gz config+state+feed state')
    bak.add_argument('--dest', default='/var/backups')
    bak.add_argument('--with-compose-data', action='store_true',
                     help='include compose data dirs (large)')
    bak.set_defaults(func=cmd_backup)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, system.CommandError) as exc:
        print('error: %s' % exc)
        return 2


if __name__ == '__main__':
    sys.exit(main())
