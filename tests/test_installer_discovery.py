"""Discovery tests against REAL response shapes on local stub servers.

Stubs serve credential-free shapes matching the production products
(arr /api/v3 system/status with appName, Transmission 409 session handshake,
Jellyfin system/info/public, Seerr settings/public). Everything binds an
ephemeral 127.0.0.1 port; nothing here touches a real service.
"""
import json
import socket
import sys
import threading
import unittest
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from installer import discovery  # noqa: E402


def serve(handler):
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class ArrStub(BaseHTTPRequestHandler):
    appName = 'Sonarr'
    apiKey = 'stub-key-123'
    prefix = '/api/v3'

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith(self.prefix + '/system/status'):
            if self.headers.get('X-Api-Key') != self.apiKey:
                self.send_response(401)
                self.end_headers()
                return
            body = json.dumps({'appName': self.appName, 'version': '4.0.0'}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()


class DiscoveryTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
   cls.sonarr = serve(type('S', (ArrStub,), {}))
   cls.radarr = serve(type('R', (ArrStub,), {'appName': 'Radarr'}))
   cls.sonarr_url = 'http://127.0.0.1:%d' % cls.sonarr.server_port
   cls.radarr_url = 'http://127.0.0.1:%d' % cls.radarr.server_port

 @classmethod
 def tearDownClass(cls):
   for s in (cls.sonarr, cls.radarr):
    s.shutdown(); s.server_close()

 def detect(self, name, cfg):
   return discovery.detect(name, {name: cfg} if cfg else {}, docker={})

 def test_arr_ready_with_configured_key_and_appname(self):
   r = self.detect('sonarr', {'base_url': self.sonarr_url, 'key': 'stub-key-123'})
   self.assertEqual(r['class'], 'ready')
   self.assertFalse(r['external'])

 def test_arr_wrong_key_is_needs_setup_never_install(self):
   r = self.detect('sonarr', {'base_url': self.sonarr_url, 'key': 'wrong'})
   self.assertEqual(r['class'], 'needs-setup')

 def test_arr_custom_port_and_base_path_are_used(self):
   r = self.detect('sonarr', {'base_url': self.sonarr_url + '/arr', 'key': 'stub-key-123'})
   # stub has no /arr prefix -> not recognized; must NOT claim ready or install
   self.assertIn(r['class'], ('port-occupied', 'unavailable'))

 def test_wrong_product_fails_closed(self):
   r = self.detect('sonarr', {'base_url': self.radarr_url, 'key': 'stub-key-123'})
   self.assertEqual(r['class'], 'wrong-service')
   r2 = self.detect('radarr', {'base_url': self.radarr_url, 'key': 'stub-key-123'})
   self.assertEqual(r2['class'], 'ready')

 def test_unauthenticated_401_is_challenge_not_proof(self):
   r = self.detect('sonarr', {'base_url': self.sonarr_url})  # no key configured
   self.assertEqual(r['class'], 'needs-setup')
   self.assertIn('API key missing', r['detail'])

 def test_declared_external_never_installs(self):
   cfg = {'base_url': 'http://192.0.2.10:8989', 'key': 'x'}
   r = self.detect('sonarr', cfg)
   self.assertTrue(r['external'])
   self.assertNotEqual(r['class'], 'ready')
   self.assertIn('NOT be installed locally', r['detail'])

 def test_transmission_409_handshake_with_basic_auth(self):
   class Tr(BaseHTTPRequestHandler):
    ok = True
    session = 'sess-1'
    def log_message(self, *a): pass
    def do_POST(self):
     if self.path != '/transmission/rpc':
      self.send_response(404); self.end_headers(); return
     if self.headers.get('X-Transmission-Session-Id') != self.session:
      self.send_response(409)
      self.send_header('X-Transmission-Session-Id', self.session)
      self.end_headers(); return
     auth = self.headers.get('Authorization') or ''
     want = 'Basic ' + base64.b64encode(b'dubs:pw').decode()
     if self.ok and auth == want:
      body = json.dumps({'result': 'success', 'arguments': {}}).encode()
      self.send_response(200); self.send_header('Content-Type', 'application/json')
      self.end_headers(); self.wfile.write(body); return
     self.send_response(401); self.end_headers()
   srv = serve(Tr)
   try:
    url = 'http://127.0.0.1:%d' % srv.server_port
    r = self.detect('transmission', {'base_url': url, 'user': 'dubs', 'password': 'pw'})
    self.assertEqual(r['class'], 'ready')
    bad = self.detect('transmission', {'base_url': url, 'user': 'dubs', 'password': 'nope'})
    self.assertEqual(bad['class'], 'needs-setup')
    self.assertIn('401', bad['detail'])
   finally:
    srv.shutdown(); srv.server_close()

 def test_jellyfin_readonly_token_and_wizard(self):
   class Jf(BaseHTTPRequestHandler):
    token = 'readonly-token'
    def log_message(self, *a): pass
    def do_GET(self):
     if self.path == '/system/info/public':
      body = json.dumps({'ProductName': 'Jellyfin Server', 'StartupWizardCompleted': True}).encode()
      self.send_response(200); self.end_headers(); self.wfile.write(body); return
     if self.path.startswith('/Users/'):
      if self.headers.get('Authorization') == 'MediaBrowser Token="%s"' % self.token:
       self.send_response(200); self.end_headers(); self.wfile.write(b'{}'); return
      self.send_response(401); self.end_headers(); return
     self.send_response(404); self.end_headers()
   srv = serve(Jf)
   try:
    url = 'http://127.0.0.1:%d' % srv.server_port
    ready = self.detect('jellyfin', {'base_url': url, 'token': 'readonly-token', 'userId': 'u1'})
    self.assertEqual(ready['class'], 'ready')
    self.assertIn('readonly', ready['detail'])
    nokey = self.detect('jellyfin', {'base_url': url, 'token': '', 'userId': ''})
    self.assertEqual(nokey['class'], 'needs-setup')
   finally:
    srv.shutdown(); srv.server_close()

 def test_jellyfin_wizard_open_is_needs_setup(self):
   class Jf2(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
     body = json.dumps({'ProductName': 'Jellyfin Server', 'StartupWizardCompleted': False}).encode()
     self.send_response(200); self.end_headers(); self.wfile.write(body)
   srv = serve(Jf2)
   try:
    r = self.detect('jellyfin', {'base_url': 'http://127.0.0.1:%d' % srv.server_port,
                                 'token': 't', 'userId': 'u'})
    self.assertEqual(r['class'], 'needs-setup')
    self.assertIn('wizard', r['detail'].lower())
   finally:
    srv.shutdown(); srv.server_close()

 def test_seerr_public_settings_shape(self):
   class Se(BaseHTTPRequestHandler):
    initialized = True
    def log_message(self, *a): pass
    def do_GET(self):
     if self.path == '/api/v1/settings/public':
      body = json.dumps({'initialized': self.initialized, 'applicationTitle': 'Seerr'}).encode()
      self.send_response(200); self.end_headers(); self.wfile.write(body); return
     self.send_response(404); self.end_headers()
   srv = serve(Se)
   try:
    url = 'http://127.0.0.1:%d' % srv.server_port
    self.assertEqual(self.detect('seerr', {'base_url': url})['class'], 'ready')
    Se.initialized = False
    r = self.detect('seerr', {'base_url': url})
    self.assertEqual(r['class'], 'needs-setup')
   finally:
    srv.shutdown(); srv.server_close()

 def test_occupied_non_http_socket_is_blocked(self):
   # A listener that accepts nothing real (never speaks HTTP): probes must
   # classify it as occupied, never as something to install.
   blocker = socket.socket()
   blocker.bind(('127.0.0.1', 0))
   blocker.listen(16)
   port = blocker.getsockname()[1]
   draining = threading.Event()

   def drain():
    while not draining.is_set():
     try:
      conn, _ = blocker.accept()
     except OSError:
      return
     threading.Thread(target=lambda c=conn: (c.settimeout(30), c.recv(1), c.close()),
                      daemon=True).start()
   threading.Thread(target=drain, daemon=True).start()
   try:
    for name in ('sonarr', 'jellyfin', 'seerr', 'transmission', 'jackett'):
     r = self.detect(name, {'base_url': 'http://127.0.0.1:%d' % port})
     self.assertEqual(r['class'], 'port-occupied', name)
     self.assertIn('not installing', r['detail'])
   finally:
    draining.set(); blocker.close()

 def test_docker_labels_mark_managed(self):
   r = discovery.detect('sonarr', {'sonarr': {'base_url': self.sonarr_url, 'key': 'stub-key-123'}},
                        docker={'sonarr': {'name': 'dubs-sonarr'}})
   self.assertTrue(r['managed'])
   self.assertIn('dubs-sonarr', r['detail'])

 def test_plan_probes_are_stdlib_only(self):
   # plan/doctor must run before python3-requests is installed
   import inspect
   from installer import system
   self.assertNotIn('import requests', inspect.getsource(system.http_probe))
   self.assertNotIn('from requests', inspect.getsource(system.http_probe))
