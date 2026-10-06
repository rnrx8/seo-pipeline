import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from pipeline.serve import prepare_volume_user

class RuntimeVolumeTests(unittest.TestCase):
    def test_volume_initialization_drops_root_without_rewriting_ledger(self):
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root)/'budget';folder.mkdir();ledger=folder/'history.json';ledger.write_text('{"spent":12}')
            with patch.dict(os.environ,{'RAILWAY_VOLUME_MOUNT_PATH':root,'QUALITY_BUDGET_DIR':str(folder)}), patch('os.geteuid',return_value=0), patch('pwd.getpwnam',return_value=SimpleNamespace(pw_uid=1000,pw_gid=1000)), patch('os.chown') as chown, patch('os.setgroups') as groups, patch('os.setgid') as gid, patch('os.setuid') as uid:
                prepare_volume_user()
            chown.assert_called_once_with(folder.resolve(),1000,1000)
            groups.assert_called_once_with([]);gid.assert_called_once_with(1000);uid.assert_called_once_with(1000)
            self.assertEqual(ledger.read_text(),'{"spent":12}')
    def test_rejects_directory_outside_volume(self):
        with patch.dict(os.environ,{'RAILWAY_VOLUME_MOUNT_PATH':'/data','QUALITY_BUDGET_DIR':'/tmp/budget'}), patch('os.geteuid',return_value=0), patch('pwd.getpwnam',return_value=SimpleNamespace(pw_uid=1000,pw_gid=1000)), patch('os.chown') as chown:
            with self.assertRaises(RuntimeError):prepare_volume_user()
            chown.assert_not_called()
