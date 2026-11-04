"""installer/system pure-function tests (no system mutation)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from installer import system  # noqa: E402


class ComposeVersionTests(unittest.TestCase):
 def test_missing_compose_is_false(self):
  ok, msg = system.compose_v2_check(None, None)
  self.assertFalse(ok)
  self.assertIn('not found', msg)

 def test_compose_v1_rejected(self):
  ok, msg = system.compose_v2_check(['docker-compose'], '1.29.2')
  self.assertFalse(ok)
  self.assertIn('v1', msg)

 def test_compose_v2_accepted(self):
  ok, _ = system.compose_v2_check(['docker', 'compose'], '2.39.2')
  self.assertTrue(ok)

 def test_compose_v2_with_v_prefix_accepted(self):
  ok, _ = system.compose_v2_check(['docker', 'compose'], 'v2.39.2')
  self.assertTrue(ok)

 def test_standalone_compose_v5_7_accepted(self):
  ok, _ = system.compose_v2_check(['docker', 'compose'], '5.7.0')
  self.assertTrue(ok)
  ok, _ = system.compose_v2_check(['docker', 'compose'], 'v5.7.0')
  self.assertTrue(ok)

 def test_garbage_version_is_false(self):
  ok, _ = system.compose_v2_check(['docker', 'compose'], 'not-a-version')
  self.assertFalse(ok)


class HttpProbeTests(unittest.TestCase):
 def test_probe_never_raises_on_refused(self):
  # nothing listens on TEST-NET loopback-ish high port in the sandbox;
  # even if it did, the probe must return without raising
  status, body, headers = system.http_probe('http://127.0.0.1:1/', timeout=1)
  self.assertTrue(status is None or isinstance(status, int))

 def test_probe_reads_http_error_body(self):
  from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
  import threading

  class H(BaseHTTPRequestHandler):
   def log_message(self, *a): pass
   def do_GET(self):
    self.send_response(401)
    self.end_headers()
    self.wfile.write(b'{"error":"no key"}')
  srv = ThreadingHTTPServer(('127.0.0.1', 0), H)
  threading.Thread(target=srv.serve_forever, daemon=True).start()
  try:
   code, body, _ = system.http_probe('http://127.0.0.1:%d/' % srv.server_port, timeout=3)
   self.assertEqual(code, 401)
   self.assertIn(b'no key', body)
  finally:
   srv.shutdown(); srv.server_close()


if __name__ == '__main__':
 unittest.main()
