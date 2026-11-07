import os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import _dubs_env
import recovery_settings as settings

class RecoverySettingsTests(unittest.TestCase):
 def test_defaults_persistence_and_bounds(self):
  with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'SERIES_CONTROL_STATE_ROOT':directory}):
   self.assertEqual(settings.current(),settings.defaults())
   value=dict(settings.defaults(),search_retry_min=20,cleanup_min=45)
   self.assertEqual(settings.update(value),value)
   self.assertEqual(settings.current(),value)
   for key,(_,low,high) in settings.FIELDS.items():
    for bad in (low-1,high+1,True):
     with self.assertRaises(ValueError):settings.update(dict(value,**{key:bad}))
   self.assertEqual(settings.current(),value)
