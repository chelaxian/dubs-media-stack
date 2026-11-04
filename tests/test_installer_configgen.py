"""installer/configgen offline tests: full service coverage, no-overwrite,
owner gate fields, readonly Jellyfin identity, seeded credentials/paths."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'installer'))
import _dubs_env  # noqa: F401,E402  (isolate runtime roots)
from installer import configgen  # noqa: E402


class ConfiggenTests(unittest.TestCase):
 def setUp(self):
  self.root = Path(tempfile.mkdtemp(prefix='dubs-cfg-'))

 def test_generate_all_services_with_empty_keys(self):
  created, skipped = configgen.generate(self.root)
  names = {p.name for p in created}
  for svc in ('sonarr', 'radarr', 'lidarr', 'jellyfin', 'seerr',
              'transmission', 'prowlarr', 'jackett'):
   self.assertIn(svc + '.json', names)
  self.assertIn('config.json', names)
  self.assertIn('security.json', names)
  self.assertIn('studios.json', names)
  # no credentials in generated skeletons
  for svc in ('sonarr', 'prowlarr'):
   data = json.loads((self.root / (svc + '.json')).read_text())
   self.assertEqual(data['key'], '')
   self.assertEqual(data['base_url'], 'http://127.0.0.1:%d' %
                    {'sonarr': 8989, 'prowlarr': 9696}[svc])

 def test_jellyfin_is_readonly_identity_shape(self):
  configgen.generate(self.root)
  data = json.loads((self.root / 'jellyfin.json').read_text())
  self.assertEqual(set(data), {'base_url', 'token', 'userId'})
  self.assertEqual(data['token'], '')

 def test_security_skeleton_has_owner_gate_fields(self):
  configgen.generate(self.root)
  sec = json.loads((self.root / 'security.json').read_text())
  self.assertIn('owner_names', sec)
  self.assertIn('owner_ids', sec)
  self.assertEqual(sec['owner_names'], [])
  self.assertEqual(sec['owner_ids'], [])
  self.assertTrue(sec['signing_key'])
  self.assertTrue(sec['allowed_origins'] == [])

 def test_transmission_credentials_seeded_from_env_not_stdout(self):
  configgen.generate(self.root, transmission_user='dubs-abc',
                     transmission_pass='s3cret')
  data = json.loads((self.root / 'transmission.json').read_text())
  self.assertEqual(data['user'], 'dubs-abc')
  self.assertEqual(data['password'], 's3cret')

 def test_paths_seeded_from_media_layout(self):
  configgen.generate(self.root, torrent_host='/mnt/x/torrents',
                     shows_host='/mnt/x/shows', media_root='/mnt/x')
  cfg = json.loads((self.root / 'config.json').read_text())
  self.assertEqual(cfg['paths']['torrent_host'], '/mnt/x/torrents')
  self.assertEqual(cfg['paths']['shows_host'], '/mnt/x/shows')
  self.assertEqual(cfg['paths']['movies_host'], '/mnt/x/movies')

 def test_rerun_never_overwrites(self):
  configgen.generate(self.root)
  p = self.root / 'sonarr.json'
  p.write_text('{"base_url": "http://127.0.0.1:9999", "key": "operator-key"}')
  created, skipped = configgen.generate(self.root)
  self.assertEqual(created, [])
  self.assertTrue(skipped)
  data = json.loads(p.read_text())
  self.assertEqual(data['key'], 'operator-key')  # operator data preserved

 def test_force_overwrites_managed_skeleton(self):
  configgen.generate(self.root)
  created, _ = configgen.generate(self.root, force=True)
  self.assertTrue(created)

 def test_validate_loads_through_runtime(self):
  configgen.generate(self.root)
  self.assertTrue(configgen.validate(self.root))

 def test_private_file_modes(self):
  configgen.generate(self.root)
  mode = (self.root / 'security.json').stat().st_mode & 0o777
  self.assertEqual(mode, 0o640)
  root_mode = self.root.stat().st_mode & 0o777
  self.assertEqual(root_mode, 0o750)


if __name__ == '__main__':
 unittest.main()
