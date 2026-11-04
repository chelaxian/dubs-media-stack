"""Offline tests for the real-services smoke helpers (no Docker, no network).

Covers the CI-boot regression class: right after `compose up` the Arr APIs
are still booting, so http() must report transient transport failures as
(None, b'') instead of raising and aborting the whole job. Also covers the
host uid/PUID coupling and credential hygiene of the smoke environment.
"""
import io
import os
import shutil
import stat
import unittest
import urllib.error
from unittest import mock
from contextlib import redirect_stdout

import integration_smoke


class HttpTransportTests(unittest.TestCase):
    """http() must never raise on transient transport errors."""

    def test_connection_refused_returns_retry_signal(self):
        err = urllib.error.URLError(ConnectionRefusedError(111))
        with mock.patch('urllib.request.urlopen', side_effect=err):
            self.assertEqual(
                integration_smoke.http('http://127.0.0.1:1/ping'), (None, b''))

    def test_timeout_returns_retry_signal(self):
        with mock.patch('urllib.request.urlopen', side_effect=TimeoutError()):
            self.assertEqual(
                integration_smoke.http('http://127.0.0.1:1/ping'), (None, b''))

    def test_generic_oserror_returns_retry_signal(self):
        with mock.patch('urllib.request.urlopen',
                        side_effect=OSError('reset by peer')):
            self.assertEqual(
                integration_smoke.http('http://127.0.0.1:1/ping'), (None, b''))

    def test_http_error_still_reported(self):
        err = urllib.error.HTTPError('http://127.0.0.1:1/ping', 503,
                                     'Service Unavailable', None,
                                     io.BytesIO(b'booting'))
        with mock.patch('urllib.request.urlopen', side_effect=err):
            self.assertEqual(
                integration_smoke.http('http://127.0.0.1:1/ping'),
                (503, b'booting'))

    def test_success_passthrough(self):
        class Resp:
            status = 200

            def read(self):
                return b'ok'

        import contextlib

        @contextlib.contextmanager
        def ok(req, timeout=None):
            yield Resp()

        with mock.patch('urllib.request.urlopen', ok):
            self.assertEqual(
                integration_smoke.http('http://127.0.0.1:1/ping'), (200, b'ok'))


class WaitApiTests(unittest.TestCase):
    def test_wait_api_retries_then_succeeds(self):
        responses = [(None, b''), (503, b'booting'), (200, b'ready')]
        with mock.patch.object(integration_smoke, 'http',
                               side_effect=responses), \
                mock.patch.object(integration_smoke.time, 'sleep',
                                  lambda s: None), \
                mock.patch.object(integration_smoke, 'API_TIMEOUT', 5):
            stack = object.__new__(integration_smoke.SmokeStack)
            stack.wait_api('sonarr')  # None/503 retried, 200 accepted

    def test_wait_api_times_out_when_never_up(self):
        with mock.patch.object(integration_smoke, 'http',
                               return_value=(None, b'')), \
                mock.patch.object(integration_smoke.time, 'sleep',
                                  lambda s: None), \
                mock.patch.object(integration_smoke, 'API_TIMEOUT', 0.05):
            stack = object.__new__(integration_smoke.SmokeStack)
            with self.assertRaises(RuntimeError):
                stack.wait_api('sonarr')


class SmokeEnvTests(unittest.TestCase):
    def setUp(self):
        self.stack = integration_smoke.SmokeStack()
        self.addCleanup(shutil.rmtree, self.stack.root, ignore_errors=True)

    def test_env_matches_host_ids_and_is_0600(self):
        self.stack.create_storage()
        self.stack.write_env()
        puid, pgid = self.stack.container_ids()
        text = self.stack.env_path.read_text()
        self.assertIn('PUID=%d' % puid, text)
        self.assertIn('PGID=%d' % pgid, text)
        mode = stat.S_IMODE(self.stack.env_path.stat().st_mode)
        self.assertEqual(mode, 0o600, 'compose.env carries credentials')
        # Host media/data dirs are owned by the very uid the containers use.
        for tree in (self.stack.media, self.stack.data):
            for entry in (tree, *tree.rglob('*')):
                self.assertEqual(entry.stat().st_uid, puid, str(entry))

    def test_container_ids_follow_runner_when_non_root(self):
        uid = os.getuid()
        puid, pgid = integration_smoke.SmokeStack.container_ids()
        if uid == 0:
            self.assertEqual((puid, pgid), (1000, 1000))  # root picks + chowns
        else:
            self.assertEqual(puid, uid)
            self.assertEqual(pgid, os.getgid())

    def test_mask_credentials_masks_pass_and_generated_keys(self):
        self.stack.create_storage()
        cfg = self.stack.data / 'sonarr'
        cfg.mkdir(parents=True, exist_ok=True)
        key = 'generated-key-abc123'
        (cfg / 'config.xml').write_text(
            '<Config><ApiKey>%s</ApiKey></Config>' % key)
        out = io.StringIO()
        with redirect_stdout(out):
            self.stack.mask_credentials()
        printed = out.getvalue()
        self.assertIn('::add-mask::%s' % self.stack.transmission_pass, printed)
        self.assertIn('::add-mask::%s' % key, printed)
        self.assertNotIn('<ApiKey>', printed)  # keys themselves never echoed

    def test_mask_credentials_survives_missing_configs(self):
        self.stack.create_storage()  # no config.xml anywhere
        out = io.StringIO()
        with redirect_stdout(out):
            self.stack.mask_credentials()  # must not raise
        self.assertIn('::add-mask::%s' % self.stack.transmission_pass,
                      out.getvalue())

    @unittest.skipIf(integration_smoke.SMOKE, 'real-services mode')
    def test_offline_import_exposes_no_test_cases(self):
        cases = unittest.defaultTestLoader.loadTestsFromModule(
            integration_smoke)
        self.assertEqual(cases.countTestCases(), 0)


if __name__ == '__main__':
    unittest.main()
