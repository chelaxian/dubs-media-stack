"""plan/doctor offline tests: they must run WITHOUT python3-requests (stdlib
only), never mutate anything (no config skeletons, no storage sentinel, no
units, no /opt/dubs), and degrade honestly when Docker is absent.
"""
import contextlib
import importlib.abc
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / 'installer'))
import _dubs_env  # noqa: F401,E402
from installer import cli  # noqa: E402


class _BlockRequests(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'requests' or fullname.startswith('requests.'):
            raise ImportError('requests is blocked: plan/doctor must be stdlib-only')
        return None


WATCH_PATHS = (
    Path('/etc/systemd/system'),
    Path('/usr/local/bin'),
    Path('/opt'),
)


def snapshot():
    out = {}
    for base in WATCH_PATHS:
        entries = set()
        try:
            entries = {p.name for p in base.iterdir()}
        except OSError:
            pass
        out[base] = {n for n in entries if 'dubs' in n.lower()}
    return out


class PlanDoctorTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='dubs-plan-'))
        self.media = self.root / 'media'
        (self.media / 'torrents').mkdir(parents=True)
        (self.root / 'data').mkdir()
        self.env_file = self.root / '.env'
        self.env_file.write_text(
            'MEDIA_ROOT=%s\nDUBS_DATA_ROOT=%s\nDUBS_INSTALL_ROOT=%s\n'
            % (self.media, self.root / 'data', self.root / 'install'))
        self.cfg_root = self.root / 'config'
        self.cfg_root.mkdir()
        # a security.json without signing_key: doctor must REPORT it, not fix it
        (self.cfg_root / 'security.json').write_text(json.dumps({
            'signing_key': '', 'owner_names': [], 'owner_ids': [],
            'allowed_origins': [], 'allowed_networks': ['127.0.0.1/32']}))
        os.chmod(self.cfg_root / 'security.json', 0o600)
        self.before = snapshot()

    def run_cli(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(argv)
        return code, out.getvalue()

    def common_argv(self, cmd):
        return ['--env-file', str(self.env_file),
                '--config-root', str(self.cfg_root),
                '--state-root', str(self.root / 'state'), cmd]

    def assert_no_mutation(self):
        self.assertEqual(snapshot(), self.before)
        self.assertFalse((self.media / '.dubs-storage-ok').exists(),
                         'plan/doctor must not arm the storage sentinel')
        self.assertFalse((self.cfg_root / 'config.json').exists(),
                         'plan/doctor must not write config skeletons')
        self.assertFalse((self.cfg_root / 'sonarr.json').exists())

    def test_plan_is_read_only_and_reports_intents(self):
        code, out = self.run_cli(self.common_argv('plan'))
        self.assertEqual(code, 0)
        self.assertIn('read-only', out)
        self.assertIn('storage', out.lower())
        self.assert_no_mutation()

    def test_doctor_reports_without_mutating(self):
        code, out = self.run_cli(self.common_argv('doctor'))
        self.assertIn(code, (0, 1))  # problems allowed; crash/mutation not
        self.assertIn('DOCTOR', out)
        self.assert_no_mutation()

    def test_plan_works_with_requests_blocked(self):
        saved = sys.modules.pop('requests', None)
        blocker = _BlockRequests()
        sys.meta_path.insert(0, blocker)
        try:
            code, out = self.run_cli(self.common_argv('plan'))
            self.assertEqual(code, 0)
            self.assertIn('PLAN', out)
        finally:
            sys.meta_path.remove(blocker)
            if saved is not None:
                sys.modules['requests'] = saved
        self.assert_no_mutation()

    def test_doctor_flags_empty_signing_key_without_fixing_it(self):
        code, out = self.run_cli(self.common_argv('doctor'))
        self.assertIn('signing_key', out)
        data = json.loads((self.cfg_root / 'security.json').read_text())
        self.assertEqual(data['signing_key'], '')  # still broken: doctor only reports

    def test_plan_mentions_unconfigured_services_honestly(self):
        code, out = self.run_cli(self.common_argv('plan'))
        self.assertIn('configure', out)  # points operator at the configure step


if __name__ == '__main__':
    unittest.main()
