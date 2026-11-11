import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import episodes


class SharedStatePermissionTests(unittest.TestCase):
    def test_worker_json_never_reverts_to_owner_only(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            target = root / "managed-downloads.json"
            with mock.patch.object(episodes.rt, "worker_root", return_value=root):
                episodes.atomic(target, {"ok": True}, 0o600)
            self.assertEqual(os.stat(target).st_mode & 0o777, 0o640)
            self.assertEqual(target.read_text(), '{"ok": true}')

    def test_private_files_outside_worker_state_remain_private(self):
        with tempfile.TemporaryDirectory() as name:
            target = Path(name) / "backup.json"
            with mock.patch.object(episodes.rt, "worker_root", return_value=Path(name) / "worker"):
                episodes.atomic(target, {"private": True}, 0o600)
            self.assertEqual(os.stat(target).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
