"""Offline tests for the Jackett -> Prowlarr sync profile selection.

The sync must use the ACTUAL id of the managed 'Dubs' indexer (sync)
profile (bootstrap-created, minimumSeeders=3) instead of a hardcoded
appProfileId=1, create/repair only that profile, never touch the
operator's 'Standard' profile, and never log credential values. All
Prowlarr/Jackett interactions are fake objects; no tracker is contacted.
"""
import io
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout

import _dubs_env
from _dubs_env import load_script

s = load_script('jpsync', _dubs_env.AUTOMATIONS / 'jackett_prowlarr_sync.py')

STANDARD = {'id': 1, 'name': 'Standard', 'enableRss': True,
            'enableAutomaticSearch': True, 'enableInteractiveSearch': True,
            'minimumSeeders': 1}
DUBS_ID7 = {'id': 7, 'name': 'Dubs', 'enableRss': True,
            'enableAutomaticSearch': True, 'enableInteractiveSearch': True,
            'minimumSeeders': 3}
SCHEMA = {'implementation': 'Torznab', 'fields': [
    {'name': 'baseUrl', 'value': None},
    {'name': 'apiPath', 'value': '/api'},
    {'name': 'apiKey', 'value': None},
    {'name': 'torrentBaseSettings.appMinimumSeeders', 'value': None}]}


class FakeResp:
    def __init__(self, payload=None, status=200, text='', content=b''):
        self._payload = payload
        self.status_code = status
        self.text = text
        self.content = content

    def json(self):
        if self._payload is None:
            raise ValueError('no json body')
        return self._payload

    @property
    def ok(self):
        return self.status_code < 400

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError('HTTP %d' % self.status_code)


class FakeProwlarr:
    def __init__(self, profiles):
        self.profiles = [dict(p) for p in profiles]
        self.indexers = []
        self.calls = []
        self.headers = {}

    def get(self, url, timeout=None, params=None):
        self.calls.append(('GET', url, params))
        if url.endswith('indexerprofile'):
            return FakeResp(payload=[dict(p) for p in self.profiles])
        if url.endswith('indexer/schema'):
            return FakeResp(payload=[dict(SCHEMA)])
        if url.endswith('/indexer'):
            return FakeResp(payload=list(self.indexers))
        return FakeResp(status=404)

    def post(self, url, json=None, timeout=None):
        self.calls.append(('POST', url, json))
        if url.endswith('indexerprofile'):
            entry = dict(json, id=7)
            self.profiles.append(entry)
            return FakeResp(payload=entry)
        if url.endswith('indexer/test'):
            return FakeResp(payload={})
        if url.endswith('/indexer'):
            self.indexers.append(json)
            return FakeResp(payload=json)
        if url.endswith('command'):
            return FakeResp(payload={'id': 1})
        return FakeResp(status=500, text='internal')

    def put(self, url, json=None, timeout=None):
        self.calls.append(('PUT', url, json))
        if '/indexerprofile/' in url:
            pid = int(url.rsplit('/', 1)[1])
            for p in self.profiles:
                if p['id'] == pid:
                    p.update(json)
                    return FakeResp(payload=p)
        return FakeResp(status=404)


class FakeJackett:
    def __init__(self, xml, status=200):
        self.xml = xml
        self.status = status

    def get(self, url, timeout=None, params=None):
        return FakeResp(status=self.status, content=self.xml.encode())


def jackett_xml():
    return ('<?xml version="1.0"?><indexers>'
            '<indexer id="tp" configured="true"><title>TrackProvider</title></indexer>'
            '<indexer id="off" configured="false"><title>Disabled</title></indexer>'
            '</indexers>')


class EnsureSyncProfileTests(unittest.TestCase):
    def test_existing_dubs_profile_id_is_reused(self):
        p = FakeProwlarr([STANDARD, DUBS_ID7])
        self.assertEqual(s.ensure_sync_profile(p, 'http://x/api/v1/'), 7)
        muts = [c for c in p.calls if c[0] in ('POST', 'PUT')]
        self.assertEqual(muts, [], 'valid managed profile must be left as-is')

    def test_missing_dubs_created_with_minimum_seeders(self):
        p = FakeProwlarr([dict(STANDARD)])
        pid = s.ensure_sync_profile(p, 'http://x/api/v1/')
        self.assertEqual(pid, 7)
        post = [c for c in p.calls if c[0] == 'POST' and
                c[1].endswith('indexerprofile')]
        self.assertEqual(len(post), 1)
        self.assertEqual(post[0][2]['minimumSeeders'], 3)
        self.assertEqual(post[0][2]['name'], 'Dubs')
        self.assertEqual(p.profiles[0]['name'], 'Standard')
        self.assertEqual(p.profiles[0]['minimumSeeders'], 1)

    def test_drifted_dubs_repaired_in_place_standard_untouched(self):
        drifted = dict(DUBS_ID7, minimumSeeders=10)
        p = FakeProwlarr([dict(STANDARD), drifted])
        pid = s.ensure_sync_profile(p, 'http://x/api/v1/')
        self.assertEqual(pid, 7)
        put = [c for c in p.calls if c[0] == 'PUT']
        self.assertEqual(len(put), 1)
        self.assertTrue(put[0][1].endswith('indexerprofile/7'))
        self.assertEqual(put[0][2]['minimumSeeders'], 3)
        self.assertEqual(p.profiles[0], STANDARD, 'Standard must not change')
        self.assertEqual(p.profiles[1]['minimumSeeders'], 3)


class SyncIndexersTests(unittest.TestCase):
    def test_new_indexer_uses_actual_profile_id_and_seeders(self):
        p = FakeProwlarr([STANDARD, DUBS_ID7])
        out = io.StringIO()
        with redirect_stdout(out):
            added = s.sync_indexers([{'id': 'tp', 'name': 'TrackProvider'}], p,
                                    'http://x/api/v1/', 'http://jackett:9117',
                                    'jackett-secret-key', SCHEMA, 7,
                                    {'Other (Jackett)'})
        self.assertEqual(added, ['TrackProvider (Jackett)'])
        saved = p.indexers[0]
        self.assertEqual(saved['appProfileId'], 7, 'not the hardcoded 1')
        fields = {f['name']: f['value'] for f in saved['fields']}
        self.assertEqual(fields['torrentBaseSettings.appMinimumSeeders'], 3)
        self.assertEqual(fields['baseUrl'],
                         'http://jackett:9117/api/v2.0/indexers/tp/results/torznab')
        self.assertEqual(fields['apiKey'], 'jackett-secret-key')
        self.assertTrue(saved['enable'])
        self.assertEqual(saved['tags'], [])

    def test_existing_entry_skipped_without_calls(self):
        p = FakeProwlarr([STANDARD, DUBS_ID7])
        added = s.sync_indexers([{'id': 'tp', 'name': 'TrackProvider'}], p,
                                'http://x/api/v1/', 'http://jackett:9117',
                                'jackett-secret-key', SCHEMA, 7,
                                {'TrackProvider (Jackett)'})
        self.assertEqual(added, [])
        self.assertEqual(p.calls, [])

    def test_failed_save_logs_no_credentials(self):
        p = FakeProwlarr([STANDARD, DUBS_ID7])

        def failing_post(url, json=None, timeout=None):
            FakeProwlarr.post(p, url, json=json, timeout=timeout)
            if url.endswith('/indexer'):
                return FakeResp(status=500, text='apikey: jackett-secret-key')
            return FakeResp(payload={})

        p.post = failing_post
        out = io.StringIO()
        with redirect_stdout(out):
            added = s.sync_indexers([{'id': 'tp', 'name': 'TrackProvider'}], p,
                                    'http://x/api/v1/', 'http://jackett:9117',
                                    'jackett-secret-key', SCHEMA, 7, set())
        self.assertEqual(added, [])
        self.assertNotIn('jackett-secret-key', out.getvalue())

    def test_catalogue_parses_configured_only(self):
        j = FakeJackett(jackett_xml())
        indexers = s.jackett_indexers(j, 'http://jackett:9117', 'k')
        self.assertEqual(indexers, [{'id': 'tp', 'name': 'TrackProvider'}])

    def test_catalogue_http_error_mentions_no_key(self):
        j = FakeJackett(jackett_xml(), status=403)
        with self.assertRaises(RuntimeError) as ctx:
            s.jackett_indexers(j, 'http://jackett:9117', 'jackett-secret-key')
        self.assertNotIn('jackett-secret-key', str(ctx.exception))


class SafeMainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='dubs-jpsync-')
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_safe_main_end_to_end_with_profile_7(self):
        p = FakeProwlarr([STANDARD, DUBS_ID7])
        j = FakeJackett(jackett_xml())
        conns = {
            'jackett': {'base_url': 'http://jackett:9117/', 'key': 'jackett-secret-key'},
            'prowlarr': {'base_url': 'http://prowlarr:9696', 'key': 'prowlarr-key'},
        }
        out = io.StringIO()
        with redirect_stdout(out), \
                _Stacked(s, self.tmp, conns, [j, p]):
            s.safe_main()
        self.assertEqual(p.indexers[0]['appProfileId'], 7)
        self.assertIn('new connections: 1', out.getvalue())

    def test_cli_entrypoint_preserved(self):
        with open(s.__file__) as f:
            source = f.read()
        self.assertIn("if __name__=='__main__':", source)
        self.assertTrue(callable(getattr(s, 'safe_main', None)))

    def test_second_instance_lock_exits_cleanly(self):
        import fcntl
        lockpath = self.tmp + '/jackett-prowlarr-sync.lock'
        held = open(lockpath, 'w')
        fcntl.flock(held, fcntl.LOCK_EX)
        conns = {
            'jackett': {'base_url': 'http://jackett:9117', 'key': 'k'},
            'prowlarr': {'base_url': 'http://prowlarr:9696', 'key': 'k'},
        }
        with _Stacked(s, self.tmp, conns, []):
            with self.assertRaises(SystemExit) as ctx:
                s.safe_main()
        self.assertEqual(ctx.exception.code, 0)
        fcntl.flock(held, fcntl.LOCK_UN)
        held.close()


class _Stacked:
    """Context manager stacking the patches safe_main needs."""

    def __init__(self, script, state_parent, conns, sessions):
        import os
        from unittest.mock import patch
        self._cms = [
            patch.dict(os.environ, {'DUBS_AUTOMATION_STATE': state_parent}),
            patch.object(script.rt, 'connection', side_effect=lambda n: conns[n]),
            patch.object(script.requests, 'Session',
                         side_effect=self._session),
        ]
        self._sessions = sessions
        self._i = 0

    def _session(self):
        idx = self._i
        self._i += 1
        if idx >= len(self._sessions):
            raise SystemExit(0)  # no session needed anymore / lock-held path
        return self._sessions[idx]

    def __enter__(self):
        for cm in self._cms:
            cm.__enter__()
        return self

    def __exit__(self, *exc):
        for cm in reversed(self._cms):
            cm.__exit__(*exc)


if __name__ == '__main__':
    unittest.main()
