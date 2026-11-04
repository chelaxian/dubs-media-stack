"""REAL-SERVICES bootstrap smoke (flag-gated; NOT part of offline discovery).

Run only with DUBS_SMOKE=1 on a host with Docker + compose v2 and network
access to image registries:

    cd /path/to/repo && DUBS_SMOKE=1 python3 tests/integration_smoke.py

Without DUBS_SMOKE=1 this module exposes NO test cases, so
`python3 -m unittest discover tests` stays offline and hermetic.

What it does (fresh, isolated compose project, temp dirs, no trackers, no
media, no pre-existing credentials):
  1. `docker compose up -d storage-sentinel sonarr radarr lidarr transmission
     prowlarr` with generated TRANSMISSION_USER/PASS, PUID/PGID matched to
     the host dirs (runner uid, or a chowned test uid under root), an
     explicitly created (verified) storage layout + armed sentinel marker;
  2. waits for every API, reads the GENERATED ApiKey from each config.xml;
  3. writes private connection JSONs (mode 0600) into a temp config root;
  4. serves the MyShows feed (empty [] startup cache) on the docker bridge
     gateway so containerized Sonarr can reach it;
  5. calls the REAL installer.bootstrap.bootstrap(deployment, studios,
     veto_format, feed_url) and verifies against the live APIs: quality
     profiles (only 1080/2160), RU veto format, Transmission client with the
     actual daemon credentials, remote path mapping, root folder, Prowlarr
     applications (container-internal URLs), Dubs indexer profile
     (minimumSeeders), CustomImport list with FUTURE-only monitoring;
  6. runs bootstrap a second time and asserts full idempotence (stable ids,
     no duplicated managed objects);
  7. proves the Transmission daemon really accepts the generated credentials
     (409 handshake -> 200) and rejects a wrong password (401).
"""
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import xml.etree.ElementTree as ET
from http.server import ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COMPOSE_DIR = REPO / 'compose'
SMOKE = os.environ.get('DUBS_SMOKE') == '1'

SERVICES = ('sonarr', 'radarr', 'lidarr', 'transmission', 'prowlarr')
PORTS = {'sonarr': 8989, 'radarr': 7878, 'lidarr': 8686,
         'transmission': 9091, 'prowlarr': 9696}
API_TIMEOUT = 300  # first boot + image pull can be slow on fresh runners


def log(msg):
    print('[smoke %s] %s' % (time.strftime('%H:%M:%S'), msg), flush=True)


def http(url, method='GET', headers=None, body=None, auth=None, timeout=10):
    import base64
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if auth:
        req.add_header('Authorization', 'Basic ' +
                       base64.b64encode(('%s:%s' % auth).encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        # Connection refused/reset while containers are still booting right
        # after `compose up`: signal the caller to retry instead of aborting.
        return None, b''


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


class SmokeStack:
    """Isolated compose project lifecycle + generated secrets."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix='dubs-smoke-'))
        self.media = self.root / 'media'
        self.data = self.root / 'data'
        self.cfg = self.root / 'config'
        self.feed_state = self.root / 'feed'
        self.cfg.mkdir()
        self.project = 'dubs-smoke-%s' % secrets.token_hex(3)
        self.transmission_user = 'smoke-' + secrets.token_hex(4)
        self.transmission_pass = secrets.token_urlsafe(18)
        self.env_path = self.root / 'compose.env'

    @staticmethod
    def container_ids():
        """PUID/PGID matching the host dirs the test process creates.

        Non-root (GitHub hosted runner uid 1001): the containers must run as
        the same uid that owns the freshly created media/data roots, otherwise
        0755 dirs stay unwritable and bootstrap cannot import. Root: choose a
        fixed non-root test uid and chown the temp trees to it.
        """
        if os.getuid() != 0:
            return os.getuid(), os.getgid()
        return 1000, 1000

    def mask_credentials(self):
        """Register ::add-mask:: so CI logs never echo generated secrets."""
        print('::add-mask::%s' % self.transmission_pass, flush=True)
        for svc in ('sonarr', 'radarr', 'lidarr', 'prowlarr'):
            try:
                print('::add-mask::%s' % self.api_key(svc), flush=True)
            except (OSError, RuntimeError):
                pass  # config.xml not generated yet; masked later if it appears

    # -- storage layout: the explicit, VERIFIED install step ---------------
    def create_storage(self):
        for sub in ('torrents', 'shows', 'movies', 'music'):
            (self.media / sub).mkdir(parents=True)
        for svc in ('sonarr', 'radarr', 'lidarr', 'transmission', 'prowlarr'):
            (self.data / svc).mkdir(parents=True)
        # arm the sentinel exactly like the documented operator step
        (self.media / '.dubs-storage-ok').write_text('')
        os.chmod(self.media / '.dubs-storage-ok', 0o644)
        if os.getuid() == 0:
            # Root-run CI picked a non-root container uid above; the host
            # trees it writes into must belong to that uid (no chmod 777).
            puid, pgid = self.container_ids()
            for tree in (self.media, self.data):
                for p in (tree, *tree.rglob('*')):
                    os.chown(p, puid, pgid)

    def write_env(self):
        puid, pgid = self.container_ids()
        # 0600 from creation: the file carries generated daemon credentials.
        fd = os.open(self.env_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                     0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(
                'MEDIA_ROOT=%s\nDUBS_DATA_ROOT=%s\nPUID=%d\nPGID=%d\n'
                'TRANSMISSION_USER=%s\nTRANSMISSION_PASS=%s\n'
                % (self.media, self.data, puid, pgid,
                   self.transmission_user, self.transmission_pass))

    # -- compose ------------------------------------------------------------
    def compose(self, *args):
        cmd = ['docker', 'compose', '--env-file', str(self.env_path),
               '-p', self.project] + list(args)
        return run(cmd, cwd=str(COMPOSE_DIR), timeout=900)

    def up(self):
        res = self.compose('up', '-d', 'storage-sentinel', *SERVICES)
        if res.returncode != 0:
            raise RuntimeError('compose up failed: %s%s'
                               % (res.stdout[-500:], res.stderr[-1500:]))

    def down(self):
        self.compose('down', '-v', '--remove-orphans', '--timeout', '20')

    def network_gateway(self):
        res = run(['docker', 'network', 'inspect', self.project + '_dubs',
                   '--format', '{{(index .IPAM.Config 0).Gateway}}'])
        gw = res.stdout.strip()
        if not gw:
            raise RuntimeError('cannot determine bridge gateway: %s' % res.stderr)
        return gw

    # -- waiting / generated keys -------------------------------------------
    def wait_api(self, name):
        deadline = time.time() + API_TIMEOUT
        while time.time() < deadline:
            if name == 'transmission':
                status, _ = http('http://127.0.0.1:9091/transmission/rpc',
                                 method='POST', body=b'{}',
                                 headers={'Content-Type': 'application/json'})
                if status in (200, 401, 409):
                    return
            else:
                status, _ = http('http://127.0.0.1:%d/ping' % PORTS[name])
                if status == 200:
                    return
            time.sleep(3)
        raise RuntimeError('%s API did not come up within %ss'
                           % (name, API_TIMEOUT))

    def api_key(self, svc):
        xml = (self.data / svc / 'config.xml').read_text()
        m = re.search(r'<ApiKey>([^<]+)</ApiKey>', xml)
        if not m or not m.group(1):
            raise RuntimeError('%s: no generated ApiKey in config.xml yet' % svc)
        return m.group(1)

    def write_connections(self):
        import stat
        for svc in ('sonarr', 'radarr', 'lidarr', 'prowlarr'):
            p = self.cfg / (svc + '.json')
            p.write_text(json.dumps({'base_url': 'http://127.0.0.1:%d' % PORTS[svc],
                                     'key': self.api_key(svc)}))
            os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        p = self.cfg / 'transmission.json'
        p.write_text(json.dumps({'base_url': 'http://127.0.0.1:%d' % PORTS['transmission'],
                                 'user': self.transmission_user,
                                 'password': self.transmission_pass}))
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)


def feed_server(stack, gateway):
    """Serve the watchlist feed on the bridge gateway with an EMPTY cache."""
    sys.path.insert(0, str(REPO))
    import _dubs_env  # noqa: F401  (runtime roots -> throwaway dirs)
    import myshows_feed
    myshows_feed.STATE = stack.feed_state
    stack.feed_state.mkdir(parents=True)
    myshows_feed.ensure_startup_cache()
    srv = ThreadingHTTPServer((gateway, 0), myshows_feed.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, 'http://%s:%d' % (gateway, srv.server_port)


if SMOKE:

    class IntegrationSmoke(unittest.TestCase):
        @classmethod
        def setUpClass(cls):
            requests = __import__('requests')
            cls.requests = requests
            cls.stack = SmokeStack()
            cls.addClassCleanup(cls._teardown)
            # Generated RPC password exists from now on; mask before anything
            # can log it (compose/native failures may echo environments).
            cls.stack.mask_credentials()
            log('storage layout + sentinel marker (explicit verified step)')
            cls.stack.create_storage()
            cls.stack.write_env()
            log('compose project %s: up %s' % (cls.stack.project, ', '.join(SERVICES)))
            cls.stack.up()
            for svc in SERVICES:
                cls.stack.wait_api(svc)
                log('%s API up' % svc)
            cls.stack.write_connections()
            cls.stack.mask_credentials()  # now the generated ApiKeys exist
            cls.gateway = cls.stack.network_gateway()
            log('bridge gateway %s' % cls.gateway)
            cls.feed_srv, cls.feed_url = feed_server(cls.stack, cls.gateway)
            cls.addClassCleanup(cls.feed_srv.shutdown)
            log('feed serving %s (empty [] startup cache)' % cls.feed_url)
            status, body = http(cls.feed_url + '/health')
            if status != 200:
                raise RuntimeError('feed health failed: HTTP %s from %s' % (status, cls.feed_url))
            # Deployment from the SAME compose env + temp config root
            from installer.deployment import Deployment
            cls.dep = Deployment.load(
                env_file=str(cls.stack.env_path),
                config_root=str(cls.stack.cfg),
                state_root=str(cls.stack.root / 'state'),
                worker_root=str(cls.stack.root / 'worker'),
                feed_state=str(cls.stack.feed_state),
                covers=str(cls.stack.root / 'covers'),
                studios=str(REPO / 'studios.json'))
            from installer import bootstrap as bs
            cls.bs = bs
            cls.studios = json.loads((REPO / 'studios.json').read_text())
            from installer.configgen import CONFIG_EXAMPLE
            cls.veto = CONFIG_EXAMPLE['veto_format']
            # the REAL bootstrap, once, shared by the ordered assertions
            log('calling bootstrap(deployment, studios, %r, %s)'
                % (cls.veto, cls.feed_url))
            cls.report = cls.do_bootstrap()
            log('bootstrap report: %s' % json.dumps(cls.report, ensure_ascii=False))

        @classmethod
        def _teardown(cls):
            log('compose down -v')
            try:
                cls.stack.down()
            except Exception as exc:
                log('compose down failed: %r' % exc)
            # Failure paths may reach teardown before keys were masked; the
            # CI diagnostics step prints container logs right after this.
            cls.stack.mask_credentials()
            shutil.rmtree(cls.stack.root, ignore_errors=True)

        # -- helpers -------------------------------------------------------
        @classmethod
        def arr(cls, name):
            return cls.bs.Arr(name, 'http://127.0.0.1:%d' % PORTS[name],
                              cls.stack.api_key(name))

        @classmethod
        def field_map(cls, entry):
            return {f.get('name'): f.get('value') for f in entry.get('fields') or []}

        @classmethod
        def profile_resolutions(cls, profile, acc=None):
            acc = [] if acc is None else acc
            for item in profile.get('items') or []:
                if item.get('quality'):
                    acc.append(item['quality'].get('resolution'))
                cls.profile_resolutions(item, acc)
            return acc

        @classmethod
        def format_scores(cls, profile):
            return {fi['name']: fi.get('score') for fi in profile.get('formatItems') or []}

        # -- the real bootstrap ---------------------------------------------
        @classmethod
        def do_bootstrap(cls):
            return cls.bs.bootstrap(cls.dep, cls.studios, cls.veto, cls.feed_url)

        def test_01_bootstrap_report_and_live_apis(self):
            report = self.report  # real bootstrap ran once in setUpClass
            self.assertIn('sonarr', report)
            # quality profiles: exactly 1080/2160 allowed, RU veto wired
            sonarr = self.arr('sonarr')
            profiles = {p['name']: p for p in sonarr.call('qualityprofile')}
            for name in ('Dubs RU 2160p', 'Dubs RU 1080p'):
                self.assertIn(name, profiles)
                self.assertEqual(profiles[name]['id'], report['profiles'][name])
            resolutions = sorted(set(self.profile_resolutions(profiles['Dubs RU 2160p'])))
            self.assertIn(1080, resolutions)
            self.assertIn(2160, resolutions)
            for r in resolutions:
                self.assertIn(r, (1080, 2160),
                              'profile allows foreign resolution %s' % r)
            resolutions1080 = sorted(set(
                self.profile_resolutions(profiles['Dubs RU 1080p'])))
            self.assertEqual(resolutions1080, [1080])
            scores = self.format_scores(profiles['Dubs RU 2160p'])
            self.assertEqual(scores[self.veto], -10000000)  # hard RU veto
            self.assertGreaterEqual(scores.get('Озвучка 01 — %s' % self.studios[0]['name'], 0), 100)
            veto_formats = [cf for cf in sonarr.call('customformat')
                            if cf['name'] == self.veto]
            self.assertEqual(len(veto_formats), 1)
            specs = veto_formats[0]['specifications']
            self.assertTrue(all(s.get('required') and s.get('negate') for s in specs),
                            'veto must be required AND negated (no RU anywhere)')
            # Transmission client with the ACTUAL generated daemon credentials
            clients = [c for c in sonarr.call('downloadclient')
                       if c['name'] == 'Dubs Transmission']
            self.assertEqual(len(clients), 1)
            fields = self.field_map(clients[0])
            self.assertEqual(fields['host'], 'transmission')
            self.assertEqual(fields['tvCategory'], 'dubs-tv')
            self.assertEqual(fields['username'], self.stack.transmission_user)
            self.assertTrue(fields['password'])  # seeded, never empty
            # remote path mapping transmission -> sonarr
            mappings = [m for m in sonarr.call('remotepathmapping')
                        if m['host'] == 'transmission']
            self.assertEqual(len(mappings), 1)
            self.assertEqual(mappings[0]['remotePath'], '/downloads/')
            self.assertEqual(mappings[0]['localPath'], '/torrents/')
            # root folder /tv exists
            self.assertIn('/tv', [r['path'] for r in sonarr.call('rootfolder')])
            # radarr + lidarr wired too
            self.assertEqual(report.get('radarr'), 'wired')
            self.assertEqual(report.get('lidarr'), 'wired')
            self.assertIn('/movies', [r['path'] for r in self.arr('radarr').call('rootfolder')])
            self.assertIn('/music', [r['path'] for r in self.arr('lidarr').call('rootfolder')])

        def test_02_prowlarr_applications_internal_urls_and_profile(self):
            prowlarr = self.arr('prowlarr')
            apps = {a['name']: a for a in prowlarr.call('applications')}
            for impl in ('Sonarr', 'Radarr', 'Lidarr'):
                self.assertIn('%s (Dubs)' % impl, apps)
                fields = self.field_map(apps['%s (Dubs)' % impl])
                self.assertEqual(fields['prowlarrUrl'], 'http://prowlarr:9696')
                self.assertEqual(fields['baseUrl'], 'http://%s:%d' % (
                    impl.lower(), PORTS[impl.lower()]))
                self.assertTrue(fields['apiKey'])
            profiles = [p for p in prowlarr.call('indexerprofile')
                        if p['name'] == 'Dubs']
            self.assertEqual(len(profiles), 1)
            self.assertEqual(profiles[0]['minimumSeeders'], 3)

        def test_03_custom_import_list_future_only(self):
            sonarr = self.arr('sonarr')
            lists = [l for l in sonarr.call('importlist')
                     if l['name'] == 'MyShows watching (Dubs feed)']
            self.assertEqual(len(lists), 1)
            entry = lists[0]
            self.assertEqual(self.field_map(entry).get('baseUrl'), self.feed_url)
            self.assertEqual(entry.get('shouldMonitor'), 'future')
            self.assertEqual(entry.get('monitorNewItems'), 'all')
            self.assertFalse(entry.get('searchForMissingEpisodes'))
            self.assertEqual(entry.get('rootFolderPath'), '/tv')
            self.assertEqual(entry.get('qualityProfileId'),
                             self.report['profiles']['Dubs RU 2160p'])

        def test_04_transmission_daemon_accepts_generated_credentials(self):
            url = 'http://127.0.0.1:9091/transmission/rpc'
            s = self.requests.Session()
            s.auth = (self.stack.transmission_user, self.stack.transmission_pass)
            r1 = s.post(url, json={}, timeout=10)
            session = r1.headers.get('X-Transmission-Session-Id')
            self.assertTrue(session, 'no session id issued (409 handshake)')
            r2 = s.post(url, json={'method': 'session-get'}, timeout=10,
                        headers={'X-Transmission-Session-Id': session})
            self.assertEqual(r2.status_code, 200)
            self.assertEqual(r2.json()['result'], 'success')
            bad = self.requests.post(url, json={}, timeout=10,
                                     auth=(self.stack.transmission_user, 'wrong'))
            self.assertEqual(bad.status_code, 401)

        def test_05_second_bootstrap_is_idempotent(self):
            before_profiles = {p['name']: p['id']
                               for p in self.arr('sonarr').call('qualityprofile')}
            before_formats = len(self.arr('sonarr').call('customformat'))
            before_clients = len([c for c in self.arr('sonarr').call('downloadclient')
                                  if c['name'] == 'Dubs Transmission'])
            before_maps = len([m for m in self.arr('sonarr').call('remotepathmapping')
                               if m['host'] == 'transmission'])
            before_apps = len([a for a in self.arr('prowlarr').call('applications')
                               if a['name'].endswith('(Dubs)')])
            before_lists = len([l for l in self.arr('sonarr').call('importlist')
                                if l['name'] == 'MyShows watching (Dubs feed)'])
            report2 = self.do_bootstrap()
            after_profiles = {p['name']: p['id']
                              for p in self.arr('sonarr').call('qualityprofile')}
            self.assertEqual(report2['profiles'], self.report['profiles'])
            self.assertEqual(before_profiles, after_profiles)
            self.assertEqual(before_formats, len(self.arr('sonarr').call('customformat')))
            self.assertEqual(before_clients, 1)
            self.assertEqual(before_maps, 1)
            self.assertEqual(before_apps, 3)
            self.assertEqual(before_lists, 1)
            self.assertEqual(report2['import_list'], self.report['import_list'])

        def test_06_discovery_sees_managed_stack_ready(self):
            from installer import discovery
            # compose pins container_name: dubs-<service>
            docker = {svc: {'name': 'dubs-%s' % svc} for svc in SERVICES}
            for svc in ('sonarr', 'prowlarr', 'transmission'):
                cfg = json.loads(
                    (self.stack.cfg / (svc + '.json')).read_text())
                r = discovery.detect(svc, {svc: cfg}, docker=docker)
                self.assertEqual(r['class'], 'ready', r)
                self.assertTrue(r['managed'], 'dubs-* container must be flagged managed')


else:
    if __name__ == '__main__':
        print('integration_smoke is flag-gated: run with '
              'DUBS_SMOKE=1 on a Docker host (see module docstring)')
        sys.exit(2)

def expected_names(module):
    cls = getattr(module, 'IntegrationSmoke', None)
    return sorted(n for n in dir(cls)
                  if n.startswith('test') and callable(getattr(cls, n))) if cls else []

def suite_for(module):
    return unittest.TestLoader().loadTestsFromModule(module)

def _cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _cases(item)
        else:
            yield item

def smoke_main(module=None):
    """Run the REAL smoke; refuse fake success when nothing is selected.

    unittest.main(module=None, argv=[...]) used to load ZERO tests and
    exit 0 ("Ran 0 tests" green CI); the runner below fails closed.
    """
    target = module if module is not None else sys.modules['__main__']
    if not SMOKE:
        print('integration_smoke is flag-gated: run with '
              'DUBS_SMOKE=1 on a Docker host (see module docstring)')
        return 2
    expected = expected_names(target)
    suite = suite_for(target)
    selected = sorted({case.id().rsplit('.', 1)[-1] for case in _cases(suite)})
    if len(expected) != 6 or suite.countTestCases() != len(expected) or selected != expected:
        print('smoke runner refuses to start: selected %d of %d expected '
              'test methods (%s)' % (len(selected), len(expected),
                                     ', '.join(expected) or 'none'))
        return 2
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.testsRun != len(expected):
        print('smoke runner refuses success: no tests ran')
        return 2
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__" and SMOKE:
    sys.exit(smoke_main())
