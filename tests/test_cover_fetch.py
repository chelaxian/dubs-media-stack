"""Offline tests for the /cover cache + on-demand Sonarr fetch fallback.

Fresh installs have an EMPTY cover cache (no Sonarr MediaCover bind), so the
handler must fetch posters from the configured Sonarr API on demand while
keeping every existing guard: session/owner gate, path safety (no symlink
following, no traversal), size bound, JPEG-only, no redirects, no external
hosts, atomic cache writes. All HTTP here is a local stub; the real Sonarr
is never contacted.
"""
import io
import shutil
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import _dubs_env
from _dubs_env import load_script

m = load_script('ctrl_cover', _dubs_env.SRC / 'control.py')

JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 64  # sniffable minimal JPEG


class SonarrStub(BaseHTTPRequestHandler):
    """Fake /api/v3/mediacover endpoint; behavior driven by class attrs."""
    hits = 0
    evil_hits = 0
    mode = 'ok'

    def log_message(self, *args):
        pass

    def do_GET(self):
        type(self).hits += 1
        if self.path == '/api/v3/mediacover/43/poster.jpg':
            mode = type(self).mode
            if mode == 'ok':
                self._serve(200, 'image/jpeg', JPEG)
            elif mode == 'html':  # /MediaCover capital-path style body
                self._serve(200, 'text/html', b'<html>Object moved</html>')
            elif mode == 'png':
                self._serve(200, 'image/png', b'\x89PNG\r\n\x1a\n' + b'0' * 32)
            elif mode == 'oversize':
                self._serve(200, 'image/jpeg', JPEG + b'0' * (5 * 1024 * 1024))
            elif mode == 'notfound':
                self.send_error(404)
            elif mode == 'redirect':
                self.send_response(302)
                self.send_header('Location', 'http://example.org/evil.jpg')
                self.end_headers()
            else:
                self.send_error(500)
        else:
            type(self).evil_hits += 1  # redirect target: must never be hit
            self.send_error(404)

    def _serve(self, code, ctype, body):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(_NoRedirect)


class UrllibShim:
    """requests-like shim over urllib; refuses redirects, records calls."""

    def __init__(self):
        self.calls = []

    def get(self, url, headers=None, timeout=None, allow_redirects=False):
        self.calls.append({'url': url, 'headers': dict(headers or {}),
                           'timeout': timeout,
                           'allow_redirects': allow_redirects})
        req = urllib.request.Request(url, headers=headers or {})
        try:
            with OPENER.open(req, timeout=timeout) as r:
                return SimpleNamespace(status_code=r.status,
                                       headers=dict(r.headers),
                                       content=r.read())
        except urllib.error.HTTPError as e:
            return SimpleNamespace(status_code=e.code,
                                   headers=dict(e.headers or {}),
                                   content=e.read())


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


class CoverFunctionTests(unittest.TestCase):
    """fetch_cover / store_cover / cover_cache_image / cover_image units."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='dubs-cover-'))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.shim = UrllibShim()
        self.m = SimpleNamespace(BASE=self.sonarr_base + '/api/v3',
                                 HEAD={'X-Api-Key': 'stub-key'},
                                 requests=self.shim)
        SonarrStub.hits = 0
        SonarrStub.evil_hits = 0
        SonarrStub.mode = 'ok'
        self.srv = ThreadingHTTPServer(('127.0.0.1', self.port), SonarrStub)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.sonarr_base = 'http://127.0.0.1:%d' % cls.port

    # -- fresh install: empty cache -> real HTTP fetch --------------------
    def test_empty_cache_fetch_then_cache_hit_without_http(self):
        self.assertIsNone(m.cover_cache_image(self.root, '43'))
        first = m.cover_image(self.m, self.root, '43')
        self.assertEqual(first, JPEG)
        self.assertEqual(SonarrStub.hits, 1)
        self.assertTrue((self.root / '43' / 'poster.jpg').is_file())
        second = m.cover_image(self.m, self.root, '43')
        self.assertEqual(second, JPEG)
        self.assertEqual(SonarrStub.hits, 1, 'second serve must hit the cache')
        self.assertEqual(len(self.shim.calls), 1)

    def test_fetch_uses_headers_only_correct_endpoint(self):
        m.fetch_cover(self.m, '43')
        call = self.shim.calls[0]
        self.assertEqual(call['url'],
                         self.m.BASE + '/mediacover/43/poster.jpg')
        self.assertEqual(call['headers']['X-Api-Key'], 'stub-key')
        self.assertNotIn('apikey', call['url'].lower())
        self.assertFalse(call['allow_redirects'])
        self.assertLessEqual(call['timeout'], 5)

    # -- rejections --------------------------------------------------------
    def test_non_image_200_rejected(self):
        SonarrStub.mode = 'html'
        self.assertIsNone(m.fetch_cover(self.m, '43'))
        SonarrStub.mode = 'png'
        self.assertIsNone(m.fetch_cover(self.m, '43'))

    def test_oversized_image_rejected(self):
        SonarrStub.mode = 'oversize'
        self.assertIsNone(m.fetch_cover(self.m, '43'))

    def test_redirect_and_foreign_host_rejected(self):
        SonarrStub.mode = 'redirect'
        self.assertIsNone(m.fetch_cover(self.m, '43'))
        self.assertEqual(len(self.shim.calls), 1, 'redirect not followed')
        self.assertEqual(SonarrStub.evil_hits, 0)

    def test_unreachable_sonarr_returns_none(self):
        port = free_port()  # nothing listening
        dead = SimpleNamespace(BASE='http://127.0.0.1:%d/api/v3' % port,
                               HEAD={'X-Api-Key': 'k'}, requests=UrllibShim())
        self.assertIsNone(m.fetch_cover(dead, '43'))

    def test_sonarr_404_returns_none(self):
        SonarrStub.mode = 'notfound'
        self.assertIsNone(m.fetch_cover(self.m, '43'))

    def test_fetch_failure_means_404_not_crash(self):
        SonarrStub.mode = 'notfound'
        self.assertIsNone(m.cover_image(self.m, self.root, '43'))

    # -- cache guards -------------------------------------------------------
    def test_store_does_not_follow_symlinked_series_dir(self):
        outside = self.root.parent / ('outside-' + self.root.name)
        outside.mkdir()
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        (self.root / '43').symlink_to(outside)
        self.assertFalse(m.store_cover(self.root, '43', JPEG))
        self.assertEqual(list(outside.iterdir()), [])

    def test_store_replaces_symlinked_poster_atomically(self):
        outside = self.root.parent / ('outside2-' + self.root.name)
        outside.mkdir()
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        victim = outside / 'secret.txt'
        victim.write_text('do not touch')
        (self.root / '43').mkdir()
        (self.root / '43' / 'poster.jpg').symlink_to(victim)
        self.assertTrue(m.store_cover(self.root, '43', JPEG))
        self.assertEqual(victim.read_text(), 'do not touch')
        self.assertTrue((self.root / '43' / 'poster.jpg').is_file())

    def test_traversal_rejected_everywhere(self):
        bad = '../escaped'
        self.assertIsNone(m.cover_cache_image(self.root, bad))
        self.assertIsNone(m.fetch_cover(self.m, bad))
        self.assertFalse(m.store_cover(self.root, bad, JPEG))
        self.assertFalse((self.root.parent / 'escaped').exists())

    def test_cache_read_skips_symlink_and_oversize(self):
        (self.root / '43').mkdir()
        target = self.root.parent / ('t-' + self.root.name + '.jpg')
        target.write_bytes(JPEG)
        self.addCleanup(target.unlink, missing_ok=True)
        (self.root / '43' / 'poster.jpg').symlink_to(target)
        self.assertIsNone(m.cover_cache_image(self.root, '43'))
        (self.root / '43' / 'poster.jpg').unlink()
        (self.root / '43' / 'poster.jpg').write_bytes(b'J' * (5 * 1024 * 1024 + 1))
        self.assertIsNone(m.cover_cache_image(self.root, '43'))
        (self.root / '43' / 'poster.jpg').unlink()
        (self.root / '43' / 'poster.jpg').write_bytes(JPEG)
        self.assertEqual(m.cover_cache_image(self.root, '43'), JPEG)

    def test_candidate_order_fanart_then_poster(self):
        (self.root / '44').mkdir()
        (self.root / '44' / 'poster.jpg').write_bytes(JPEG + b'poster')
        (self.root / '44' / 'fanart.jpg').write_bytes(JPEG + b'fanart')
        image = m.cover_cache_image(self.root, '44')
        self.assertTrue(image.endswith(b'fanart'))

    def test_store_failure_still_serves_image(self):
        SonarrStub.mode = 'ok'
        readonly = self.root / 'locked'
        readonly.mkdir()
        cache = SimpleNamespace(covers=readonly)
        with patch.object(m, 'store_cover', return_value=False) as store:
            image = m.cover_image(self.m, readonly, '43')
        self.assertEqual(image, JPEG)
        store.assert_called_once()


class CoverHandlerTests(unittest.TestCase):
    """Owner/session gate and end-to-end handler flow without sockets."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='dubs-cover-h-'))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.shim = UrllibShim()
        SonarrStub.hits = 0
        SonarrStub.mode = 'ok'
        self.port = free_port()
        self.srv = ThreadingHTTPServer(('127.0.0.1', self.port), SonarrStub)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def handler(self, path, session):
        h = m.Handler.__new__(m.Handler)
        h.security_context = {'secure': True}
        h.path = path
        h.wfile = io.BytesIO()
        h.sent_headers = []
        h.status = None
        h.send_response = lambda code, *a: h.sent_headers.append(('status', code))
        h.send_header = lambda k, v: h.sent_headers.append((k, v))
        h.end_headers = lambda: None
        h.send_error = lambda code, *a, **k: h.sent_headers.append(('error', code))
        return h, session

    def run_get(self, path, session):
        h, s = self.handler(path, session)
        with patch.object(m.Handler, 'session', return_value=s), \
                patch.object(m.rt, 'covers_root', return_value=self.root), \
                patch.object(m, 'BASE', 'http://127.0.0.1:%d/api/v3' % self.port), \
                patch.object(m, 'HEAD', {'X-Api-Key': 'stub-key'}), \
                patch.object(m, 'requests', self.shim):
            h.get()
        return h

    def test_no_session_is_401(self):
        h = self.run_get('/cover/43.jpg', None)
        self.assertIn(('error', 401), h.sent_headers)
        self.assertEqual(SonarrStub.hits, 0, 'gate before any fetch')

    def test_bad_shape_is_404_without_fetch(self):
        h = self.run_get('/cover/../../../etc.jpg', {'user': 'u'})
        self.assertIn(('error', 404), h.sent_headers)
        self.assertEqual(len(self.shim.calls), 0)

    def test_full_flow_fetch_serve_persist_then_cache_hit(self):
        h1 = self.run_get('/cover/43.jpg', {'user': 'u'})
        self.assertIn(('status', 200), h1.sent_headers)
        ctype = [v for k, v in h1.sent_headers
                 if k == 'Content-Type']
        self.assertEqual(ctype, ['image/jpeg'])
        self.assertEqual(h1.wfile.getvalue(), JPEG)
        self.assertEqual(SonarrStub.hits, 1)
        self.assertTrue((self.root / '43' / 'poster.jpg').is_file())
        h2 = self.run_get('/cover/43.jpg', {'user': 'u'})
        self.assertIn(('status', 200), h2.sent_headers)
        self.assertEqual(h2.wfile.getvalue(), JPEG)
        self.assertEqual(SonarrStub.hits, 1, 'second request: cache hit')
        self.assertEqual(len(self.shim.calls), 1, 'no second HTTP call')

    def test_fetch_miss_is_404(self):
        SonarrStub.mode = 'notfound'
        h = self.run_get('/cover/43.jpg', {'user': 'u'})
        self.assertIn(('error', 404), h.sent_headers)


if __name__ == '__main__':
    unittest.main()
