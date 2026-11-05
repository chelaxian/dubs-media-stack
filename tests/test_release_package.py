import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools import package_release as release


class ReleasePackage(unittest.TestCase):
    def test_immutable_repeatable_archive_excludes_private_and_build_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'source';root.mkdir()
            def git(*args):
                return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.DEVNULL)
            git('init')
            for name in ['install.sh','installer/wizard.py','src/control.py','src/signing.key',
                         'compose/.env','compose/.env.example','mobile/build/secret.txt']:
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('fixture\n')
            git('add','.')
            git('-c','user.name=Release test','-c','user.email=test@example.org','commit','-m','fixture')
            with patch.object(release,'ROOT',root):
                a=release.package('HEAD',Path(directory)/'one')
                b=release.package('HEAD',Path(directory)/'two')
            self.assertEqual(a.read_bytes(),b.read_bytes())
            self.assertEqual(a.with_suffix('.gz.sha256').read_text().split()[0],hashlib.sha256(a.read_bytes()).hexdigest())
            with tarfile.open(a) as tar:
                names=tar.getnames()
                self.assertIn('dubs-media-stack/installer/wizard.py',names)
                self.assertIn('dubs-media-stack/compose/.env.example',names)
                self.assertIn('dubs-media-stack/SOURCE_COMMIT.txt',names)
                self.assertNotIn('dubs-media-stack/compose/.env',names)
                self.assertNotIn('dubs-media-stack/src/signing.key',names)
                self.assertFalse(any('/build/' in n for n in names))

    def test_known_runtime_artifacts_are_never_source(self):
        for name in ['src/.secret','src/session.sqlite3','mobile/test.ipa','compose/.env',
                     'src/token.pem','mobile/node_modules/lib/index.js']:
            self.assertFalse(release.allowed(name),name)


if __name__=='__main__':unittest.main()
