"""installer/deployment offline tests: env parsing, least-privilege defaults,
internal URL overrides for adopted services, feed bind privacy."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'installer'))
import _dubs_env  # noqa: F401,E402
from installer.deployment import Deployment, ConfigError  # noqa: E402


def env_file(**kw):
    tmp = tempfile.mkdtemp(prefix='dubs-env-')
    p = Path(tmp) / '.env'
    p.write_text('\n'.join('%s=%s' % kv for kv in kw.items()) + '\n')
    return str(p)


class DeploymentTests(unittest.TestCase):
    def test_least_privilege_defaults(self):
        d = Deployment.load(env_file=env_file(MEDIA_ROOT='/mnt/media', DUBS_DATA_ROOT='/mnt/data'))
        self.assertEqual(d.user, 'dubs')
        self.assertEqual(d.group, 'dubs')
        self.assertNotEqual(d.user, 'root')
        self.assertEqual(d.install_root, Path('/opt/dubs'))
        self.assertEqual(d.repo, Path('/opt/dubs'))

    def test_media_layout_derives_torrent_and_shows(self):
        d = Deployment.load(env_file=env_file(MEDIA_ROOT='/mnt/media', DUBS_DATA_ROOT='/mnt/data'))
        self.assertEqual(d.torrent_host, Path('/mnt/media/torrents'))
        self.assertEqual(d.shows_host, Path('/mnt/media/shows'))
        self.assertEqual(d.media_root, Path('/mnt/media'))

    def test_explicit_hosts_win_over_derivation(self):
        d = Deployment.load(env_file=env_file(MEDIA_ROOT='/mnt/media', DUBS_DATA_ROOT='/mnt/data'),
                            TORRENT_HOST='/srv/t', SHOWS_HOST='/srv/s')
        self.assertEqual(d.torrent_host, Path('/srv/t'))
        self.assertEqual(d.shows_host, Path('/srv/s'))

    def test_relative_media_root_refused(self):
        with self.assertRaises(ConfigError):
            Deployment.load(env_file=env_file(MEDIA_ROOT='media/relative', DUBS_DATA_ROOT='/mnt/data'))

    def test_missing_media_root_is_none_not_crash(self):
        d = Deployment.load(env_file=env_file(PUID='1001'))
        self.assertIsNone(d.media_root)
        self.assertEqual(d.puid, '1001')

    def test_wildcard_feed_bind_refused(self):
        with self.assertRaises(ConfigError):
            Deployment.load(env_file=env_file(MEDIA_ROOT='/mnt/media', DUBS_DATA_ROOT='/mnt/data',
                                              DUBS_FEED_BIND='0.0.0.0'))

    def test_feed_bind_list_and_port(self):
        d = Deployment.load(env_file=env_file(MEDIA_ROOT='/mnt/media', DUBS_DATA_ROOT='/mnt/data',
                                              DUBS_FEED_BIND='127.0.0.1,172.18.0.1', DUBS_FEED_PORT='9291'))
        self.assertEqual(d.feed_bind, ('127.0.0.1', '172.18.0.1'))
        self.assertEqual(d.feed_port, 9291)

    def test_internal_url_override_for_adopted_services(self):
        d = Deployment.load(env_file=env_file(MEDIA_ROOT='/mnt/media', DUBS_DATA_ROOT='/mnt/data',
                                              DUBS_INTERNAL_SONARR='http://192.0.2.50:8989',
                                              DUBS_INTERNAL_TRANSMISSION='https://nas.example.org:9091'))
        self.assertEqual(d.internal['sonarr'], 'http://192.0.2.50:8989')
        self.assertEqual(d.internal['transmission'], 'https://nas.example.org:9091')
        self.assertEqual(d.internal['prowlarr'], 'http://prowlarr:9696')  # default kept

    def test_internal_override_must_be_http_url(self):
        with self.assertRaises(ConfigError):
            Deployment.load(env_file=env_file(MEDIA_ROOT='/mnt/media', DUBS_DATA_ROOT='/mnt/data',
                                              DUBS_INTERNAL_SONARR='ftp://nope'))

    def test_env_block_points_at_repo_src(self):
        d = Deployment.load(env_file=env_file(MEDIA_ROOT='/mnt/media', DUBS_DATA_ROOT='/mnt/data'))
        block = d.env_block()
        self.assertTrue(any(line.startswith('SERIES_CONTROL_CONFIG_ROOT=') for line in block))
        self.assertTrue(any(line == 'PYTHONPATH=%s/src' % d.repo for line in block))

    def test_custom_install_root(self):
        d = Deployment.load(env_file=env_file(MEDIA_ROOT='/mnt/media', DUBS_DATA_ROOT='/mnt/data',
                                              DUBS_INSTALL_ROOT='/srv/dubs'))
        self.assertEqual(d.repo, Path('/srv/dubs'))
        self.assertEqual(d.compose_dir, Path('/srv/dubs/compose'))


if __name__ == '__main__':
    unittest.main()
