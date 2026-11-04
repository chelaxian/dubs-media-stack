"""Indexer endpoint allowlist offline tests (episodes.validate_endpoint).

Only exact scheme/host/port/path-prefix matches configured in config.json
(plus loopback Arr defaults) may be used as torrent-download endpoints.
Credentials, dot-segment escapes, foreign hosts and prefix escapes must all
fail closed. No network is used: validation is pure.
"""
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _dubs_env  # noqa: F401,E402
import episodes  # noqa: E402


def allowed_map():
    return {
        '127.0.0.1': {'ports': {9696, 8989, 9117},
                      'prefixes': {'/api/v1/', '/api/v3/', '/api/v2.0/'},
                      'tls': {('http', 9696): [True]}},
        'tracker.example.org': {'ports': {443},
                                'prefixes': {'/download/'},
                                'tls': {('https', 443): [False]}},
    }


class ValidateEndpointTests(unittest.TestCase):
    def validate(self, url):
        with patch.object(episodes.rt, 'indexer_endpoints', allowed_map):
            return episodes.validate_endpoint(url)

    def test_loopback_prowlarr_default_is_allowed(self):
        u = self.validate('http://127.0.0.1:9696/api/v1/search?query=x')
        self.assertEqual(u.hostname, '127.0.0.1')

    def test_unknown_host_rejected(self):
        with self.assertRaises(episodes.UserError):
            self.validate('http://198.51.100.7:9696/api/v1/search')

    def test_lookalike_host_rejected(self):
        with self.assertRaises(episodes.UserError):
            self.validate('http://127.0.0.1.evil.example:9696/api/v1/search')

    def test_wrong_port_rejected(self):
        with self.assertRaises(episodes.UserError):
            self.validate('http://127.0.0.1:8080/api/v1/search')

    def test_prefix_escape_rejected(self):
        with self.assertRaises(episodes.UserError):
            self.validate('http://127.0.0.1:9696/admin/settings')

    def test_dot_segment_escape_rejected(self):
        with self.assertRaises(episodes.UserError):
            self.validate('http://127.0.0.1:9696/api/v1/../../admin')

    def test_credentials_rejected(self):
        with self.assertRaises(episodes.UserError):
            self.validate('http://user:pass@127.0.0.1:9696/api/v1/search')

    def test_non_http_scheme_rejected(self):
        with self.assertRaises(episodes.UserError):
            self.validate('ftp://127.0.0.1:9696/api/v1/search')

    def test_implicit_https_port_443_with_configured_tls_verify(self):
        u = self.validate('https://tracker.example.org/download/file.torrent')
        self.assertEqual(u.scheme, 'https')
        self.assertIsNone(u.port)  # implicit 443 matched the configured port
        with patch.object(episodes.rt, 'indexer_endpoints', allowed_map):
            self.assertFalse(episodes._tls_verify(
                'https://tracker.example.org/download/file.torrent'))

    def test_implicit_http_port_80_not_granted(self):
        # implicit defaults only apply when that port was configured for the host
        with self.assertRaises(episodes.UserError):
            self.validate('http://tracker.example.org/download/file.torrent')

    def test_case_insensitive_host(self):
        u = self.validate('https://TRACKER.Example.Org/download/file.torrent')
        self.assertEqual(u.hostname, 'tracker.example.org')

    def test_unknown_scheme_port_pair_defaults_to_verify(self):
        with patch.object(episodes.rt, 'indexer_endpoints', allowed_map):
            self.assertTrue(episodes._tls_verify('http://127.0.0.1:8989/api/v3/x'))

    def test_loopback_install_defaults_present_without_extra_config(self):
        # The real runtime must always trust local Arr/Prowlarr/Jackett ports.
        eps = episodes.rt.indexer_endpoints()
        self.assertIn('127.0.0.1', eps)
        self.assertTrue({9696, 8989, 9117} <= eps['127.0.0.1']['ports'])


if __name__ == '__main__':
    unittest.main()
