"""compose/docker-compose.yml structural checks (pure YAML, no daemon).

The offline suite has no Docker daemon: this validates the file the same way
`docker compose config` would interpolate it, minus daemon-dependent checks
(those run in the CI integration job).
"""
import json
import re
import sys
import unittest
from pathlib import Path
import yaml

REPO = Path(__file__).resolve().parents[1]
COMPOSE = REPO / 'compose' / 'docker-compose.yml'
IGPU = REPO / 'compose' / 'docker.igpu.yml'


def load():
    return yaml.safe_load(COMPOSE.read_text())


class ComposeConfigTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.doc = load()
  cls.services = cls.doc['services']

 def test_core_services_present(self):
  for svc in ('storage-sentinel', 'jellyfin', 'seerr', 'sonarr', 'radarr',
              'lidarr', 'transmission', 'prowlarr'):
   self.assertIn(svc, self.services)

 def test_extras_profile_opt_in(self):
  for svc in ('jackett', 'flaresolverr'):
   self.assertIn('extras', self.services[svc].get('profiles', []))

 def test_core_services_do_not_have_extras_profile(self):
  for svc in ('jellyfin', 'seerr', 'sonarr', 'radarr', 'lidarr',
              'transmission', 'prowlarr'):
   self.assertNotIn('extras', self.services[svc].get('profiles', []),
                    '%s must start with the core profile' % svc)

 def test_sentinel_gates_every_media_service(self):
  for svc in ('jellyfin', 'seerr', 'sonarr', 'radarr', 'lidarr',
              'transmission', 'prowlarr', 'jackett'):
   dep = self.services[svc]['depends_on']['storage-sentinel']
   self.assertEqual(dep['condition'], 'service_completed_successfully', svc)

 def test_required_vars_fail_closed(self):
  text = COMPOSE.read_text()
  self.assertIn('${MEDIA_ROOT:?', text)
  self.assertIn('${DUBS_DATA_ROOT:?', text)

 def test_all_host_binds_use_create_host_path_false(self):
  for svc, conf in self.services.items():
   for vol in conf.get('volumes', []):
    if isinstance(vol, dict) and vol.get('type') == 'bind':
     self.assertFalse(vol['bind']['create_host_path'],
                      '%s: bind %s must not auto-create host dirs' % (svc, vol['source']))

 def test_http_ports_loopback_by_default(self):
  text = COMPOSE.read_text()
  for m in re.finditer(r'"?\$\{BIND_IP:-127\.0\.0\.1\}:(\d+):\1"', text):
   pass  # loopback-mapped same-port entries
  self.assertNotIn('"8096:8096', text)  # never wildcard by accident
  self.assertNotIn('- "9091:9091', text)
  self.assertGreaterEqual(len(re.findall(r'BIND_IP:-127\.0\.0\.1', text)), 7)

 def test_seerr_official_image_and_config_path(self):
  seerr = self.services['seerr']
  self.assertEqual(seerr['image'], '${SEERR_IMAGE:-ghcr.io/seerr-team/seerr:latest}')
  targets = [v['target'] for v in seerr['volumes']]
  self.assertIn('/app/config', targets)
  self.assertIn('no-new-privileges', seerr.get('security_opt', []))
  self.assertTrue(seerr.get('init'))

 def test_containers_run_as_puid_pgid(self):
  for svc in ('jellyfin', 'seerr'):
   self.assertEqual(self.services[svc]['user'], '${PUID:-1000}:${PGID:-1000}')
  text = COMPOSE.read_text()
  self.assertIn('PUID: ${PUID:-1000}', text)
  self.assertIn('PGID: ${PGID:-1000}', text)

 def test_transmission_rpc_credentials_env(self):
  env = self.services['transmission']['environment']
  self.assertEqual(env['USER'],
                   '${TRANSMISSION_USER:?Transmission RPC username is required}')
  self.assertEqual(env['PASS'],
                   '${TRANSMISSION_PASS:?Transmission RPC password is required}')
  self.assertNotIn('TRANSMISSION_USER', env)
  self.assertNotIn('TRANSMISSION_PASS', env)
  self.assertEqual(env['PUID'], '${PUID:-1000}')
  self.assertEqual(env['PGID'], '${PGID:-1000}')
  for name, service in self.services.items():
   if name != 'transmission':
    self.assertNotIn('PASS', service.get('environment', {}))

 def test_generated_credentials_match_config_json_and_stay_private(self):
  import contextlib
  import io
  import tempfile
  import _dubs_env  # noqa: F401
  from installer import cli, configgen
  with tempfile.TemporaryDirectory(prefix='dubs-compose-credentials-') as tmp:
   root = Path(tmp)
   env_path = root / '.env'
   output = io.StringIO()
   with contextlib.redirect_stdout(output):
    cli._ensure_transmission_credentials(env_path)
    env = cli.read_env(env_path)
    configgen.generate(root / 'config',
                       transmission_user=env['TRANSMISSION_USER'],
                       transmission_pass=env['TRANSMISSION_PASS'])
   config_path = root / 'config' / 'transmission.json'
   config = json.loads(config_path.read_text())
   for input_key, json_key in (('TRANSMISSION_USER', 'user'),
                                ('TRANSMISSION_PASS', 'password')):
    self.assertTrue(bool(env[input_key]), 'credential must be nonempty')
    self.assertTrue(config[json_key] == env[input_key], 'credential parity')
    self.assertNotIn(env[input_key], output.getvalue())
   self.assertEqual(env_path.stat().st_mode & 0o777, 0o600)
   self.assertEqual(config_path.stat().st_mode & 0o777, 0o640)

 def test_no_host_network_and_no_docker_sock(self):
  text = COMPOSE.read_text()
  self.assertNotIn('network_mode: host', text)
  self.assertNotIn('/var/run/docker.sock', text)

 def test_media_library_readonly_for_jellyfin(self):
  jelly = self.services['jellyfin']
  for vol in jelly['volumes']:
   if vol['target'].startswith('/media/'):
    self.assertTrue(vol.get('read_only'), vol['target'])

 def test_igpu_overlay_structure(self):
  if not IGPU.exists():
   self.skipTest('no igpu overlay')
  doc = yaml.safe_load(IGPU.read_text())
  jelly = doc['services']['jellyfin']
  devices = json.dumps(jelly.get('devices', []))
  self.assertIn('/dev/dri', devices)

 def test_sentinel_script_wording_is_honest(self):
  script = (REPO / 'compose' / 'storage-sentinel.sh').read_text()
  self.assertIn('cannot detect every possible mount mistake', script)


if __name__ == '__main__':
 unittest.main()
