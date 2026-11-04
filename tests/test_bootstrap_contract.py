"""Offline contract tests for installer/bootstrap.py.

Payload shapes come from the credential-free REAL schema captures
(/tmp/dubs-real-schema-examples.json, /tmp/dubs-lidarr-schema.json); all HTTP
is served by an in-memory fake, so no live Arr service is ever contacted.
"""
import copy, json, re, sys, unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from installer import bootstrap as bs  # noqa: E402

SONARR_FIXTURES = Path('/tmp/dubs-real-schema-examples.json')
LIDARR_FIXTURE = Path('/tmp/dubs-lidarr-schema.json')
STUDIOS = json.loads((REPO / 'studios.json').read_text())
VETO = 'ОБЯЗАТЕЛЬНО RU — запрет без русского'
FEED = 'http://panel.test'
FIX_OK = SONARR_FIXTURES.exists() and LIDARR_FIXTURE.exists()

BASES = {'sonarr': 'http://sonarr.test', 'radarr': 'http://radarr.test',
         'lidarr': 'http://lidarr.test', 'prowlarr': 'http://prowlarr.test'}
API_PATHS = {'sonarr': '/api/v3/', 'radarr': '/api/v3/',
             'lidarr': '/api/v1/', 'prowlarr': '/api/v1/'}


class FakeResponse:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.content = b'{}' if payload is not None else b''
        self.text = json.dumps(payload) if payload is not None else ''

    def json(self):
        return self._payload


class FakeStack:
    """In-memory Arr APIs; schema responses come straight from the fixtures."""

    def __init__(self):
        f = json.loads(SONARR_FIXTURES.read_text())
        l = json.loads(LIDARR_FIXTURE.read_text())
        self.schemas = {
            'sonarr': {'qualityprofile/schema': f['sonarr']['qualityprofile'],
                       'downloadclient/schema': [f['sonarr']['downloadclient']],
                       'importlist/schema': f['sonarr']['importlist_schema']},
            'radarr': {'qualityprofile/schema': f['radarr']['qualityprofile'],
                       'downloadclient/schema': [f['radarr']['downloadclient']]},
            'lidarr': {'qualityprofile/schema': l['qualityprofile/schema'],
                       'metadataprofile/schema': l['metadataprofile/schema'],
                       'downloadclient/schema': [l['downloadclient/schema']]},
            'prowlarr': {'applications/schema': f['prowlarr']['applications']},
        }
        self.state = {app: {} for app in BASES}
        self.calls = []  # (app, method, path, data)
        # Captured quality objects carry the same Quality resource shape as
        # GET qualitydefinition (id is the QUALITY id, not definition row id).
        def leaves(item):
            if item.get('quality'):
                yield item['quality']
            for child in item.get('items') or []:
                yield from leaves(child)
        for app in ('sonarr', 'radarr'):
            for quality in leaves(f[app]['qualityprofile']):
                self.add(app, 'qualitydefinition', {'quality': quality})
        # Fresh-install seeds: Lidarr/Prowlarr ship default objects.
        self.add('lidarr', 'qualityprofile', {'name': 'Any'})
        self.add('lidarr', 'metadataprofile', {'name': 'Standard'})
        self.add('prowlarr', 'appprofile',
                 {'name': 'Standard', 'enableRss': True,
                  'enableAutomaticSearch': False, 'enableInteractiveSearch': False,
                  'minimumSeeders': 1})

    def add(self, app, collection, obj):
        obj = copy.deepcopy(obj)
        items = self.state[app].setdefault(collection, [])
        obj['id'] = max([x.get('id', 0) for x in items] + [0]) + 1
        items.append(obj)
        return obj['id']

    def request(self, method, url, json=None, params=None, headers=None,
                timeout=None):
        m = re.match(r'(https?://[^/]+?)/api/v[13]/(.+)', url)
        if m is None or m.group(1) not in BASES.values():
            raise AssertionError('call to unexpected non-Arr endpoint: %s' % url)
        app = next(a for a, base in BASES.items() if base == m.group(1))
        return FakeResponse(*self.handle(app, method, m.group(2), json))

    def handle(self, app, method, path, data):
        self.calls.append((app, method, path, copy.deepcopy(data)))
        if path.endswith('/schema'):
            schema = self.schemas[app].get(path)
            if schema is None:
                return 404, {'error': 'no schema for %s' % path}
            return 200, copy.deepcopy(schema)
        collection, _, rest = path.partition('/')
        if app == 'prowlarr':
            # Official API v1: AppProfileResource and IndexerResource.appProfileId.
            if collection not in ('appprofile', 'applications', 'indexer'):
                return 404, {}
            if collection == 'appprofile' and method in ('POST', 'PUT'):
                expected = {'name', 'enableRss', 'enableAutomaticSearch',
                            'enableInteractiveSearch', 'minimumSeeders'}
                if rest:
                    expected.add('id')
                if set(data) != expected:
                    return 400, {'error': 'invalid AppProfileResource fields'}
        if collection == 'downloadclient' and method in ('POST', 'PUT'):
            fields = {f['name']: f.get('value') for f in data.get('fields', [])}
            for prefix in ('tv', 'movie', 'music'):
                if fields.get(prefix + 'Category') and fields.get(prefix + 'Directory'):
                    return 400, [{'propertyName': prefix.title() + 'Category',
                                  'errorMessage': 'Cannot use Category and Directory'}]
        if rest:
            obj_id = int(rest)
            items = self.state[app].get(collection, [])
            obj = next((x for x in items if x.get('id') == obj_id), None)
            if obj is None:
                return 404, {}
            if method == 'PUT':
                items[items.index(obj)] = copy.deepcopy(data)
                return 200, copy.deepcopy(data)
            return 200, copy.deepcopy(obj)
        items = self.state[app].setdefault(collection, [])
        if method == 'GET':
            return 200, copy.deepcopy(items)
        if method == 'POST':
            obj = copy.deepcopy(data)
            obj['id'] = max([x.get('id', 0) for x in items] + [0]) + 1
            items.append(obj)
            return 201, copy.deepcopy(obj)
        return 405, {}

    # helpers ---------------------------------------------------------------
    def posted(self, app, path):
        return [d for a, m, p, d in self.calls
                if a == app and m == 'POST' and p == path]

    def puts(self, app, path):
        return [d for a, m, p, d in self.calls
                if a == app and m == 'PUT' and p == path]

    def objects(self, app, collection):
        return copy.deepcopy(self.state[app].get(collection, []))


class Dep:
    def __init__(self, conn):
        self._conn = conn
        self.internal = {app: base.replace('.test', ':8989') for app, base in BASES.items()}
        self.published = {}

    def connections(self):
        return self._conn


def default_conn(stack, lidarr_extra=None, with_radarr=True):
    def svc(app, key):
        return {'base_url': BASES[app], 'key': key}
    conn = {'sonarr': svc('sonarr', 'k-sonarr'),
            'prowlarr': svc('prowlarr', 'k-prowlarr'),
            'transmission': {'base_url': 'http://transmission.test'},
            'lidarr': dict(svc('lidarr', 'k-lidarr'), **(lidarr_extra or {}))}
    if with_radarr:
        conn['radarr'] = svc('radarr', 'k-radarr')
    return conn


def run_bootstrap(stack, conn=None, feed_url=FEED):
    with patch.object(bs.requests, 'request', side_effect=stack.request):
        return bs.bootstrap(Dep(conn or default_conn(stack)),
                            copy.deepcopy(STUDIOS), VETO, feed_url)


def field(client_payload, name):
    return next(f for f in client_payload['fields'] if f['name'] == name)['value']


def allowed_resolutions(profile):
    out = set()
    for item in profile['items']:
        if not item['allowed']:
            continue
        if item.get('quality'):
            out.add(bs._resolution(item['quality'].get('name', '')))
        else:
            out |= {bs._resolution((leaf.get('quality') or {}).get('name', ''))
                    for leaf in item.get('items') or []}
    return out


@unittest.skipUnless(FIX_OK, 'real schema fixtures missing in /tmp')
class BootstrapContractTests(unittest.TestCase):

    # 1. Sonarr CustomImport: baseUrl + future/all monitoring ---------------

    def test_custom_import_matches_real_schema_and_future_monitoring(self):
        fixture = json.loads(SONARR_FIXTURES.read_text())
        custom = next(s for s in fixture['sonarr']['importlist_schema']
                      if s.get('implementation') == 'CustomImport')
        self.assertIn('baseUrl', [f['name'] for f in custom['fields']])
        self.assertNotIn('url', [f['name'] for f in custom['fields']])

        stack = FakeStack()
        report = run_bootstrap(stack)
        payload = stack.posted('sonarr', 'importlist')[0]
        self.assertEqual(field(payload, 'baseUrl'), FEED + '/series')
        self.assertEqual(payload['shouldMonitor'], 'future')
        self.assertEqual(payload['monitorNewItems'], 'all')
        self.assertFalse(payload['searchForMissingEpisodes'])
        self.assertTrue(payload['enableAutomaticAdd'])
        self.assertTrue(payload['seasonFolder'])
        self.assertEqual(payload['rootFolderPath'], '/tv')
        self.assertEqual(payload['qualityProfileId'],
                         report['profiles']['Dubs RU 2160p'])
        self.assertEqual(payload['implementation'], 'CustomImport')
        # Idempotent update keeps the same contract.
        run_bootstrap(stack)
        managed = next(l for l in stack.objects('sonarr', 'importlist')
                       if l['name'] == 'MyShows watching (Dubs feed)')
        upd = stack.puts('sonarr', 'importlist/%d' % managed['id'])[0]
        self.assertEqual(field(upd, 'baseUrl'), FEED + '/series')
        self.assertEqual(upd['shouldMonitor'], 'future')
        self.assertEqual(upd['monitorNewItems'], 'all')
        self.assertEqual(upd['rootFolderPath'], '/tv')

    def test_custom_import_url_fetches_real_feed_on_create_and_update(self):
        # Sonarr CustomImportProxy GETs BaseUrl.TrimEnd('/') directly; it
        # does not append the /api/v3/series route used by SonarrImport.
        import tempfile
        import urllib.error
        import urllib.request
        from types import SimpleNamespace
        import _dubs_env  # noqa: F401
        import myshows_feed
        from integration_smoke import feed_server

        with tempfile.TemporaryDirectory(prefix='dubs-list-contract-') as tmp:
            with patch.object(myshows_feed, 'STATE', Path(tmp)):
                srv, base = feed_server(SimpleNamespace(feed_state=Path(tmp) / 'feed'),
                                        '127.0.0.1')
                self.addCleanup(srv.server_close)
                self.addCleanup(srv.shutdown)
                with urllib.request.urlopen(base + '/health', timeout=5) as response:
                    self.assertIsInstance(json.load(response), dict)
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(base, timeout=5)
                self.assertEqual(ctx.exception.code, 404)

                stack = FakeStack()
                handle = stack.handle

                def validate_list(app, method, path, data):
                    if app == 'sonarr' and method in ('POST', 'PUT') and (
                            path == 'importlist' or path.startswith('importlist/')):
                        # Perform the real HTTP request Sonarr uses to validate
                        # the list; no test-connection bypass or fake root route.
                        with urllib.request.urlopen(field(data, 'baseUrl').rstrip('/'),
                                                    timeout=5) as response:
                            self.assertEqual(json.load(response), [])
                    return handle(app, method, path, data)

                with patch.object(stack, 'handle', side_effect=validate_list):
                    report = run_bootstrap(stack, feed_url=base)
                    report2 = run_bootstrap(stack, feed_url=base + '/')
                self.assertEqual(report2['import_list'], report['import_list'])
                self.assertEqual(len(stack.posted('sonarr', 'importlist')), 1)

    def test_quality_definitions_override_profile_names_and_missing_resolution(self):
        stack = FakeStack()
        schema = stack.schemas['sonarr']['qualityprofile/schema']
        schema['items'] = [
            {'quality': {'id': 3, 'name': 'misleading-720p'}, 'allowed': False},
            {'quality': {'id': 999, 'name': 'invented-2160p'}, 'allowed': True},
            {'id': 1234, 'items': [
                {'quality': {'id': 3, 'name': 'WEBDL-1080p'}},
                {'quality': {'id': 0, 'name': 'Unknown'}}], 'allowed': True},
            {'quality': {'id': 10, 'name': 'Raw-HD'}, 'allowed': True},
        ]
        run_bootstrap(stack)
        profile = next(p for p in stack.objects('sonarr', 'qualityprofile')
                       if p['name'] == 'Dubs RU 2160p')
        self.assertEqual([i['allowed'] for i in profile['items']],
                         [True, False, False, False])
        self.assertEqual(profile['cutoff'], 3)

    def test_quality_profile_fails_closed_without_quality_definitions(self):
        stack = FakeStack()
        stack.state['sonarr']['qualitydefinition'] = []
        with self.assertRaisesRegex(bs.ArrError, 'no qualities'):
            run_bootstrap(stack)
        self.assertFalse(stack.posted('sonarr', 'qualityprofile'))

    # 2. Radarr: same 14-studio base family + identical scores --------------

    def test_radarr_gets_full_studio_family_with_base_scores(self):
        stack = FakeStack()
        run_bootstrap(stack)
        radarr_cf = {c['name'] for c in stack.objects('radarr', 'customformat')}
        sonarr_cf = {c['name'] for c in stack.objects('sonarr', 'customformat')}
        self.assertEqual(radarr_cf, sonarr_cf)  # same family, not RU-only
        self.assertEqual(len([n for n in radarr_cf if n.startswith('Озвучка ')]),
                         len(STUDIOS))  # 13 named + fallback
        profile = next(p for p in stack.objects('radarr', 'qualityprofile')
                       if p['name'] == 'Dubs RU 2160p')
        scores = {fi['name']: fi['score'] for fi in profile['formatItems']}
        expected = {'Озвучка %02d — %s' % (i + 1, s['name']):
                    2 ** (12 - i) * 100 for i, s in enumerate(STUDIOS[:-1])}
        expected[STUDIOS[-1]['name'] and
                 'Озвучка %02d — %s' % (len(STUDIOS), STUDIOS[-1]['name'])] = 0
        expected[bs.RU_EVIDENCE_TITLE] = 100
        expected[bs.RU_EVIDENCE_LANGUAGE] = 100
        expected[VETO] = -10000000
        for name, score in expected.items():
            self.assertEqual(scores[name], score, name)
        # required dominance: RHS > sum of all lower named studios
        lower = sorted(expected[n] for n in expected
                       if n.startswith('Озвучка ') and n != 'Озвучка 01 — %s'
                       % STUDIOS[0]['name'])
        self.assertEqual(scores['Озвучка 01 — %s' % STUDIOS[0]['name']], 409600)
        self.assertGreater(409600, sum(lower))
        self.assertEqual(scores['Озвучка 02 — %s' % STUDIOS[1]['name']], 204800)
        self.assertEqual(scores['Озвучка 13 — %s' % STUDIOS[12]['name']], 100)
        # Выбор сериала stays ZERO in base; bonus is applied only by the panel.
        for name, score in scores.items():
            if name.startswith(bs.SERIES_FORMAT_PREFIX):
                self.assertEqual(score, 0, name)
        self.assertEqual(profile['minFormatScore'], 100)
        self.assertEqual(profile['cutoffFormatScore'], 100)
        self.assertFalse(profile['upgradeAllowed'])
        self.assertEqual(allowed_resolutions(profile), {1080, 2160})
        p1080 = next(p for p in stack.objects('radarr', 'qualityprofile')
                     if p['name'] == 'Dubs RU 1080p')
        self.assertEqual(allowed_resolutions(p1080), {1080})
        # cutoff = LAST allowed top-level item (leaf quality id or group id)
        def last_allowed_id(profile):
            last = [i for i in profile['items'] if i['allowed']][-1]
            return (last.get('quality') or {}).get('id') or last['id']
        self.assertEqual(profile['cutoff'], last_allowed_id(profile))
        self.assertEqual(profile['cutoff'], 31)   # radarr Remux-2160p
        self.assertEqual(p1080['cutoff'], 30)     # radarr Remux-1080p
        self.assertEqual(last_allowed_id(p1080), 30)
        # digit-less 1080-tier radarr qualities (BR-DISK/Raw-HD) stay out
        self.assertFalse(profile['items'][-1]['allowed'])
        self.assertFalse(profile['items'][-2]['allowed'])
        # sonarr profile: same contract on its own schema
        s2160 = next(p for p in stack.objects('sonarr', 'qualityprofile')
                     if p['name'] == 'Dubs RU 2160p')
        self.assertEqual(s2160['cutoff'], 21)     # sonarr Bluray-2160p Remux

    # 3. Lidarr: defaults from EXISTING profiles, no invented schema.id -----

    def test_lidarr_fixture_schemas_carry_no_id(self):
        l = json.loads(LIDARR_FIXTURE.read_text())
        self.assertNotIn('id', l['qualityprofile/schema'])
        self.assertNotIn('id', l['metadataprofile/schema'])
        for key in ('name', 'path', 'defaultMetadataProfileId',
                    'defaultQualityProfileId', 'defaultMonitorOption',
                    'defaultNewItemMonitorOption', 'defaultTags'):
            self.assertIn(key, l['rootfolder_keys'])

    def test_lidarr_defaults_from_existing_profiles(self):
        stack = FakeStack()
        run_bootstrap(stack)
        folder = stack.posted('lidarr', 'rootfolder')[0]
        self.assertEqual(folder['path'], '/music')
        self.assertEqual(folder['name'], 'Dubs Music')
        self.assertEqual(folder['defaultQualityProfileId'], 1)
        self.assertEqual(folder['defaultMetadataProfileId'], 1)
        # no invented ids / no profile creation when profiles exist
        self.assertFalse(stack.posted('lidarr', 'qualityprofile'))
        self.assertFalse(stack.posted('lidarr', 'metadataprofile'))
        # music: no TV voice/language restriction anywhere on Lidarr
        lidarr_writes = [c for c in stack.calls if c[0] == 'lidarr'
                         and c[1] in ('POST', 'PUT')]
        self.assertFalse([c for c in lidarr_writes if 'customformat' in c[2]])
        client = stack.posted('lidarr', 'downloadclient')[0]
        self.assertEqual(client['implementation'], 'Transmission')
        self.assertEqual(field(client, 'musicCategory'), '')
        self.assertEqual(field(client, 'musicDirectory'), '/downloads/complete/music')

    def test_lidarr_creates_default_from_schema_only_when_empty(self):
        stack = FakeStack()
        stack.state['lidarr']['qualityprofile'] = []
        stack.state['lidarr']['metadataprofile'] = []
        run_bootstrap(stack)
        qp = stack.posted('lidarr', 'qualityprofile')[0]
        mp = stack.posted('lidarr', 'metadataprofile')[0]
        self.assertNotIn('id', qp)   # schema has none; none invented
        self.assertNotIn('id', mp)
        self.assertEqual(qp['name'], 'Dubs Music (default)')
        self.assertIn('items', qp)   # built from the actual quality schema
        self.assertIn('primaryAlbumTypes', mp)
        stored_qp = {p['name']: p['id'] for p in stack.objects('lidarr', 'qualityprofile')}
        stored_mp = {p['name']: p['id'] for p in stack.objects('lidarr', 'metadataprofile')}
        folder = stack.posted('lidarr', 'rootfolder')[0]
        self.assertEqual(folder['defaultQualityProfileId'],
                         stored_qp['Dubs Music (default)'])
        self.assertEqual(folder['defaultMetadataProfileId'],
                         stored_mp['Dubs Music (default)'])

    def test_lidarr_operator_ids_win_and_unknown_id_rejected(self):
        stack = FakeStack()
        run_bootstrap(stack, default_conn(
            stack, lidarr_extra={'quality_profile_id': 1, 'metadata_profile_id': 1}))
        folder = stack.posted('lidarr', 'rootfolder')[0]
        self.assertEqual(folder['defaultQualityProfileId'], 1)
        self.assertEqual(folder['defaultMetadataProfileId'], 1)
        stack2 = FakeStack()
        with self.assertRaises(bs.ArrError) as ctx:
            run_bootstrap(stack2, default_conn(stack2,
                                               lidarr_extra={'quality_profile_id': 42}))
        self.assertIn('42', str(ctx.exception))
        self.assertFalse(stack2.posted('lidarr', 'rootfolder'))

    def test_lidarr_existing_folder_gets_only_missing_defaults(self):
        stack = FakeStack()
        stack.add('lidarr', 'rootfolder',
                  {'path': '/music', 'name': 'Оператор задал',
                   'defaultQualityProfileId': 1, 'defaultTags': ['mine']})
        run_bootstrap(stack, default_conn(
            stack, lidarr_extra={'default_monitor_option': 'none'}))
        self.assertFalse(stack.posted('lidarr', 'rootfolder'))
        puts = stack.puts('lidarr', 'rootfolder/1')
        self.assertEqual(len(puts), 1)
        self.assertNotIn('defaultQualityProfileId', puts[0])  # operator value kept
        self.assertNotIn('defaultTags', puts[0])
        self.assertEqual(puts[0].get('defaultMetadataProfileId'), 1)  # was unset
        self.assertEqual(puts[0].get('defaultMonitorOption'), 'none')  # "as needed"

    # 4. Unique explicit download directories ----------------------------------------------------

    def test_unique_directories_and_managed_client_name(self):
        stack = FakeStack()
        run_bootstrap(stack)
        categories = {}
        for app, cfield, dfield in (('sonarr', 'tvCategory', 'tvDirectory'),
                                    ('radarr', 'movieCategory', 'movieDirectory'),
                                    ('lidarr', 'musicCategory', 'musicDirectory')):
            client = stack.posted(app, 'downloadclient')[0]
            self.assertEqual(client['name'], 'Dubs Transmission')
            categories[app] = field(client, cfield)
            self.assertEqual(field(client, dfield),
                             '/downloads/complete/%s'
                             % {'sonarr': 'tv', 'radarr': 'movies',
                                'lidarr': 'music'}[app])
        self.assertEqual(categories, dict.fromkeys(('sonarr', 'radarr', 'lidarr'), ''))

    # 5. Prowlarr managed indexer profile (minimumSeeders lives here) ---------

    def test_prowlarr_rejects_nonexistent_profile_route(self):
        stack = FakeStack()
        with patch.object(bs.requests, 'request', side_effect=stack.request):
            with self.assertRaisesRegex(bs.ArrError, 'HTTP 404'):
                bs.Arr('prowlarr', BASES['prowlarr'], 'key').call('indexerprofile')

    def test_prowlarr_managed_indexer_profile_minimum_seeders(self):
        stack = FakeStack()
        user_id = stack.add('prowlarr', 'appprofile', {
            'name': 'My profile', 'enableRss': False,
            'enableAutomaticSearch': True, 'enableInteractiveSearch': False,
            'minimumSeeders': 17})
        stack.add('prowlarr', 'indexer', {'name': 'My tracker',
                                        'appProfileId': user_id, 'enable': True})
        before_profiles = stack.objects('prowlarr', 'appprofile')
        before_indexers = stack.objects('prowlarr', 'indexer')
        report = run_bootstrap(stack)
        created = stack.posted('prowlarr', 'appprofile')[0]
        self.assertEqual(created['name'], 'Dubs')
        self.assertEqual(created['minimumSeeders'], 3)
        self.assertTrue(created['enableRss'])
        self.assertTrue(created['enableAutomaticSearch'])
        self.assertTrue(created['enableInteractiveSearch'])
        managed = next(p for p in stack.objects('prowlarr', 'appprofile')
                       if p['name'] == 'Dubs')
        self.assertEqual(report['prowlarr_indexer_profile'],
                         {'name': 'Dubs', 'id': managed['id'],
                          'minimumSeeders': 3})
        self.assertNotIn('qualityprofile', stack.state['prowlarr'])
        # Re-run repairs a stale managed profile without changing user objects.
        stored = next(p for p in stack.state['prowlarr']['appprofile']
                      if p['id'] == managed['id'])
        stored.update(minimumSeeders=99, enableRss=False)
        report2 = run_bootstrap(stack)
        self.assertEqual(report2['prowlarr_indexer_profile'],
                         report['prowlarr_indexer_profile'])
        self.assertEqual(stack.objects('prowlarr', 'appprofile')[:-1], before_profiles)
        self.assertEqual(stack.objects('prowlarr', 'indexer'), before_indexers)
        self.assertEqual(len(stack.posted('prowlarr', 'appprofile')), 1)
        repaired = stack.objects('prowlarr', 'appprofile')[-1]
        self.assertTrue(repaired['enableRss'])
        self.assertEqual(repaired['minimumSeeders'], 3)
        self.assertFalse(stack.puts('prowlarr', 'appprofile/1'))
        self.assertEqual(stack.puts('prowlarr', 'appprofile/%d'
                                    % managed['id'])[0]['minimumSeeders'], 3)
        standard = stack.objects('prowlarr', 'appprofile')[0]
        self.assertEqual(standard['minimumSeeders'], 1)

    # 6. No silent overwrites; managed clone refresh --------------------------

    def test_conflicts_are_never_silently_overwritten(self):
        stack = FakeStack()
        stack.add('sonarr', 'remotepathmapping',
                  {'host': 'transmission', 'remotePath': '/other/',
                   'localPath': '/x/'})
        with self.assertRaises(bs.ArrError) as ctx:
            run_bootstrap(stack)
        self.assertIn('will not overwrite', str(ctx.exception))
        self.assertFalse(stack.posted('sonarr', 'remotepathmapping'))

        stack = FakeStack()
        stack.add('sonarr', 'downloadclient',
                  {'name': 'Dubs Transmission', 'implementation': 'qBittorrent',
                   'enable': True})
        with self.assertRaises(bs.ArrError) as ctx:
            run_bootstrap(stack)
        self.assertIn('qBittorrent', str(ctx.exception))
        self.assertFalse(stack.posted('sonarr', 'downloadclient'))

    def test_user_objects_stay_untouched(self):
        stack = FakeStack()
        stack.add('sonarr', 'qualityprofile', {'name': 'My Profile',
                                               'upgradeAllowed': True})
        stack.add('sonarr', 'customformat', {'name': 'User CF',
                                             'specifications': []})
        before = copy.deepcopy(stack.state)
        run_bootstrap(stack)
        self.assertEqual(stack.objects('sonarr', 'qualityprofile')[0],
                         before['sonarr']['qualityprofile'][0])
        self.assertEqual(stack.objects('sonarr', 'customformat')[0],
                         before['sonarr']['customformat'][0])
        # exactly one Dubs profile per name, existing client updated not duplicated
        names = [p['name'] for p in stack.objects('sonarr', 'qualityprofile')]
        self.assertEqual(names.count('Dubs RU 2160p'), 1)

    def test_clone_refresh_preserves_chosen_bonus_and_mode(self):
        stack = FakeStack()
        report = run_bootstrap(stack)          # run 1: create base profiles
        base_id = report['profiles']['Dubs RU 2160p']
        base = next(p for p in stack.objects('sonarr', 'qualityprofile')
                    if p['id'] == base_id)
        chosen_cf = next(c for c in stack.objects('sonarr', 'customformat')
                         if c['name'] == bs.SERIES_FORMAT_PREFIX + STUDIOS[1]['name'])
        items = copy.deepcopy(base['formatItems'])
        for fi in items:                        # stale base scores + user format
            fi['score'] = 5 if fi['name'].startswith('Озвучка ') else fi['score']
        for fi in items:
            if fi['name'].startswith(bs.SERIES_FORMAT_PREFIX):
                fi['score'] = bs.CLONE_BONUS if fi['format'] == chosen_cf['id'] else 0
        items.append({'format': 9999, 'name': 'User CF', 'score': -5})
        stale = stack.add('sonarr', 'qualityprofile', {
            'name': 'Сериал / база %d / %s / только' % (base_id, STUDIOS[1]['name']),
            'cutoff': base['cutoff'], 'upgradeAllowed': False,
            'minFormatScore': 1000001, 'formatItems': items})
        foreign = stack.add('sonarr', 'qualityprofile', {
            'name': 'Сериал / база 999 / Прочее / приоритет', 'cutoff': 1,
            'upgradeAllowed': True, 'minFormatScore': 0,
            'formatItems': [{'format': 1, 'name': 'X', 'score': 7}]})
        report2 = run_bootstrap(stack)         # run 2: refresh managed clones
        clone = next(p for p in stack.objects('sonarr', 'qualityprofile')
                     if p['id'] == stale)
        scores = {fi['name']: fi['score'] for fi in clone['formatItems']}
        self.assertEqual(scores['Озвучка 01 — %s' % STUDIOS[0]['name']], 409600)
        self.assertEqual(scores[bs.RU_EVIDENCE_TITLE], 100)
        self.assertEqual(scores[VETO], -10000000)
        # explicit chosen studio bonus + strict mode preserved
        self.assertEqual(scores[bs.SERIES_FORMAT_PREFIX + STUDIOS[1]['name']],
                         bs.CLONE_BONUS)
        self.assertEqual(clone['minFormatScore'], 1000001)
        self.assertEqual(clone['cutoff'], base['cutoff'])
        user = next(fi for fi in clone['formatItems'] if fi['format'] == 9999)
        self.assertEqual(user['score'], -5)    # user formats untouched
        other = next(fi for fi in clone['formatItems']
                     if fi['name'] == bs.SERIES_FORMAT_PREFIX + STUDIOS[0]['name'])
        self.assertEqual(other['score'], 0)
        # clone of a non-Dubs base id is left alone
        foreign_after = next(p for p in stack.objects('sonarr', 'qualityprofile')
                             if p['id'] == foreign)
        self.assertEqual(foreign_after['formatItems'][0]['score'], 7)
        self.assertIn(clone['name'], report2['refreshed_studio_clones'])

    def test_remote_mapping_idempotent_when_matching(self):
        stack = FakeStack()
        run_bootstrap(stack)
        mapping = stack.posted('sonarr', 'remotepathmapping')[0]
        self.assertEqual((mapping['host'], mapping['remotePath'],
                          mapping['localPath']),
                         ('transmission', '/downloads/', '/torrents/'))
        run_bootstrap(stack)
        self.assertEqual(len(stack.posted('sonarr', 'remotepathmapping')), 1)


class TransmissionValidationTests(unittest.TestCase):
    """Always run, including CI without the optional /tmp schema captures."""

    def stack(self, app, prefix):
        stack = object.__new__(FakeStack)
        stack.calls = []
        stack.state = {app: {}}
        stack.schemas = {app: {'downloadclient/schema': [{
            'implementation': 'Transmission', 'configContract': 'TransmissionSettings',
            'fields': [{'name': name, 'value': value} for name, value in (
                ('host', 'localhost'), ('port', 9091), ('username', ''),
                ('password', ''), (prefix + 'Category', 'schema-default'),
                (prefix + 'Directory', ''))]}]}}
        return stack

    def test_api_validator_rejects_category_and_directory_on_create_and_update(self):
        for app, prefix in (('sonarr', 'tv'), ('radarr', 'movie'), ('lidarr', 'music')):
            with self.subTest(app=app):
                stack = self.stack(app, prefix)
                payload = {'implementation': 'Transmission', 'fields': [
                    {'name': prefix + 'Category', 'value': 'dubs-media'},
                    {'name': prefix + 'Directory', 'value': '/downloads/complete/tv'}]}
                for method, path in (('POST', 'downloadclient'), ('PUT', 'downloadclient/1')):
                    status, errors = stack.handle(app, method, path, payload)
                    self.assertEqual(status, 400)
                    self.assertEqual(errors[0]['errorMessage'], 'Cannot use Category and Directory')
                self.assertFalse(stack.objects(app, 'downloadclient'))

    def test_directory_mode_clears_schema_category_and_updates_only_managed_client(self):
        for app, prefix, folder in (('sonarr', 'tv', 'tv'), ('radarr', 'movie', 'movies'),
                                    ('lidarr', 'music', 'music')):
            with self.subTest(app=app):
                stack = self.stack(app, prefix)
                stack.add(app, 'downloadclient', {'name': 'My Transmission',
                          'implementation': 'Transmission', 'fields': [
                              {'name': prefix + 'Category', 'value': 'user-media'}]})
                before = stack.objects(app, 'downloadclient')[0]
                arr = bs.Arr(app, BASES[app], 'offline-key')
                directory = '/downloads/complete/' + folder
                with patch.object(bs.requests, 'request', side_effect=stack.request):
                    first = arr.ensure_transmission_client('Dubs Transmission',
                        prefix + 'Category', prefix + 'Directory', '', directory,
                        'daemon-user', 'daemon-password')
                    # Reproduce a previously stored draft payload; migrate only
                    # our managed client away from contradictory settings.
                    managed = stack.state[app]['downloadclient'][1]
                    next(f for f in managed['fields']
                         if f['name'] == prefix + 'Category')['value'] = 'dubs-media'
                    second = arr.ensure_transmission_client('Dubs Transmission',
                        prefix + 'Category', prefix + 'Directory', '', directory,
                        'daemon-user', 'daemon-password')
                self.assertEqual(first, second)
                clients = stack.objects(app, 'downloadclient')
                self.assertEqual(clients[0], before)
                self.assertEqual(len(clients), 2)
                self.assertEqual(field(clients[1], prefix + 'Category'), '')
                self.assertEqual(field(clients[1], prefix + 'Directory'), directory)
                self.assertEqual(field(clients[1], 'username'), 'daemon-user')
                self.assertEqual(field(clients[1], 'password'), 'daemon-password')

    def test_bootstrap_rejects_contradictory_routing_before_api_call(self):
        arr = bs.Arr('sonarr', BASES['sonarr'], 'offline-key')
        with patch.object(arr, 'call') as call:
            with self.assertRaises(bs.ArrError):
                arr.ensure_transmission_client('Dubs Transmission', 'tvCategory',
                    'tvDirectory', 'dubs-tv', '/downloads/complete/tv')
            call.assert_not_called()


class SmokeQualityAuthContractTests(unittest.TestCase):
    def test_selected_groups_check_every_leaf_using_definition_quality_ids(self):
        from integration_smoke import profile_resolutions
        definitions = {3: {'id': 3, 'name': 'WEBDL-1080p', 'resolution': 1080},
                       0: {'id': 0, 'name': 'Unknown', 'resolution': 0},
                       10: {'id': 10, 'name': 'Raw-HD', 'resolution': 1080},
                       4: {'id': 4, 'name': 'HDTV-720p', 'resolution': 720}}
        profile = {'items': [
            {'quality': {'id': 0}, 'allowed': False},
            {'quality': {'id': 10}, 'allowed': False},
            {'allowed': True, 'items': [
                {'quality': {'id': 3}, 'allowed': False}]}]}
        self.assertEqual(profile_resolutions(profile, definitions), [1080])
        # Enabled Unknown, Raw-HD and unmapped IDs remain visible as failures,
        # including foreign leaves within an enabled group.
        for quality_id in (0, 10, 999):
            profile['items'][-1]['items'].append({'quality': {'id': quality_id}})
            self.assertIn(0, profile_resolutions(profile, definitions))
            profile['items'][-1]['items'].pop()
        profile['items'][-1]['items'].append({'quality': {'id': 4}})
        self.assertIn(720, profile_resolutions(profile, definitions))

    def test_transmission_bad_auth_requires_valid_csrf_and_401(self):
        from test_smoke_entrypoint import load_flagged
        from types import SimpleNamespace
        from unittest.mock import Mock
        cls = load_flagged().IntegrationSmoke
        case = cls('test_04_transmission_daemon_accepts_generated_credentials')
        case.stack = SimpleNamespace(transmission_user='test-user',
                                     transmission_pass='test-password')
        session = Mock()
        session.post.side_effect = [
            Mock(status_code=409, headers={'X-Transmission-Session-Id': 'valid-token'}),
            Mock(status_code=200, json=lambda: {'result': 'success'})]
        case.requests = Mock()
        case.requests.Session.return_value = session
        case.requests.post.return_value = Mock(status_code=401)
        case.test_04_transmission_daemon_accepts_generated_credentials()
        self.assertEqual(case.requests.post.call_args.kwargs['headers'],
                         {'X-Transmission-Session-Id': 'valid-token'})
        self.assertEqual(case.requests.post.call_args.kwargs['auth'],
                         ('test-user', 'wrong'))
        unauthenticated = case.requests.post.call_args_list[0].kwargs
        self.assertNotIn('auth', unauthenticated)
        self.assertEqual(unauthenticated['headers'],
                         {'X-Transmission-Session-Id': 'valid-token'})
        # Neither a CSRF challenge nor an unauthenticated success proves auth.
        for rejected_status in (409, 200):
            session.post.side_effect = [
                Mock(status_code=409, headers={'X-Transmission-Session-Id': 'valid-token'}),
                Mock(status_code=200, json=lambda: {'result': 'success'})]
            case.requests.post.side_effect = [Mock(status_code=401),
                                              Mock(status_code=rejected_status)]
            with self.assertRaises(AssertionError):
                case.test_04_transmission_daemon_accepts_generated_credentials()


if __name__ == '__main__':
    unittest.main(verbosity=2)
