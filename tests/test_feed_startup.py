"""MyShows feed offline tests: valid EMPTY startup cache (Custom List
validation succeeds before the first refresh), never-overwrite semantics,
privacy of feed binds, and HTTP surface (querystring stripping, 404/503).

No network: the MyShows RPC worker is never started; only the local
ThreadingHTTPServer on an ephemeral port serves cached files.
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import unittest.mock
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _dubs_env  # noqa: F401,E402  (runtime roots -> temp dirs)
import myshows_feed  # noqa: E402


def get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, r.read()


class FeedStartupTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(tempfile.mkdtemp(prefix='dubs-feed-'))
        self._orig = myshows_feed.STATE
        myshows_feed.STATE = self.state
        self.addCleanup(setattr, myshows_feed, 'STATE', self._orig)

    def test_creates_valid_empty_feed(self):
        myshows_feed.ensure_startup_cache()
        self.assertEqual(json.loads((self.state / 'series.json').read_text()), [])
        status = json.loads((self.state / 'status.json').read_text())
        self.assertIsNone(status['updated'])
        self.assertEqual(status['state'], 'waiting for first MyShows refresh')
        # Custom List consumers treat this as "empty list", not "broken URL".

    def test_never_overwrites_existing_good_cache(self):
        (self.state / 'series.json').write_text(json.dumps(
            [{'TvdbId': 79175, 'Title': 'Danny, the Champion of the World'}]))
        (self.state / 'status.json').write_text(json.dumps(
            {'updated': '2026-01-01T00:00:00', 'state': 'ok', 'mapped': 1}))
        myshows_feed.ensure_startup_cache()
        series = json.loads((self.state / 'series.json').read_text())
        status = json.loads((self.state / 'status.json').read_text())
        self.assertEqual(series[0]['TvdbId'], 79175)
        self.assertEqual(status['state'], 'ok')

    def test_feed_binds_default_loopback(self):
        saved = os.environ.pop('DUBS_FEED_BIND', None)
        try:
            self.assertEqual(myshows_feed.feed_binds(), ['127.0.0.1'])
        finally:
            if saved is not None:
                os.environ['DUBS_FEED_BIND'] = saved

    def test_feed_binds_explicit_list(self):
        with unittest.mock.patch.dict(os.environ,
                                      {'DUBS_FEED_BIND': '127.0.0.1, 172.18.0.1'}):
            self.assertEqual(myshows_feed.feed_binds(), ['127.0.0.1', '172.18.0.1'])

    def test_wildcard_bind_refused(self):
        for wild in ('0.0.0.0', '::', '*', '127.0.0.1,0.0.0.0'):
            with unittest.mock.patch.dict(os.environ, {'DUBS_FEED_BIND': wild}):
                with self.assertRaises(SystemExit):
                    myshows_feed.feed_binds()


class FeedServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = Path(tempfile.mkdtemp(prefix='dubs-feedsrv-'))
        (cls.state / 'series.json').write_text('[{"TvdbId": 1}]')
        (cls.state / 'status.json').write_text('{"state": "ok"}')
        cls._orig = myshows_feed.STATE
        myshows_feed.STATE = cls.state
        cls.srv = ThreadingHTTPServer(('127.0.0.1', 0), myshows_feed.Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        myshows_feed.STATE = cls._orig

    def base(self):
        return 'http://127.0.0.1:%d' % self.srv.server_port

    def test_series_and_sonarr_style_url_with_querystring(self):
        for path in ('/series', '/api/v3/series?apikey=stub'):
            status, body = get(self.base() + path)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), [{'TvdbId': 1}])

    def test_health_serves_status(self):
        status, body = get(self.base() + '/health')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['state'], 'ok')

    def test_unknown_path_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            get(self.base() + '/nope')
        self.assertEqual(ctx.exception.code, 404)

    def test_missing_cache_file_is_503_not_200(self):
        (self.state / 'series.json').unlink()
        try:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                get(self.base() + '/series')
            self.assertEqual(ctx.exception.code, 503)
        finally:
            (self.state / 'series.json').write_text('[{"TvdbId": 1}]')


if __name__ == '__main__':
    unittest.main()
