import json,os,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
import _dubs_env
import watch_settings as ws
import runtime as rt

class WatchSettingsTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  self.env=patch.dict(os.environ,{'SERIES_CONTROL_STATE_ROOT':str(self.root)});self.env.start()
 def tearDown(self):self.env.stop();self.tmp.cleanup()
 def test_defaults_follow_legacy_and_invalid_fail_closed(self):
  with patch.object(rt,'settings',return_value={'cleanup_enable':True}):self.assertTrue(ws.current()['delete_watched'])
  (self.root/ws.NAME).write_text('{bad')
  self.assertEqual(ws.current(),{'confirm_watch':True,'delete_watched':False})
 def test_both_preferences_persist_and_guard_disables_deletion(self):
  value={'confirm_watch':True,'delete_watched':False}
  self.assertEqual(ws.update(value),value)
  with ws.deletion_guard() as allowed:self.assertFalse(allowed)
  self.assertEqual((self.root/ws.NAME).stat().st_mode&0o777,0o640)
 def test_strict_types_and_keys(self):
  for value in ({}, {'confirm_watch':1,'delete_watched':False},{'confirm_watch':True,'delete_watched':'false'},{'confirm_watch':True,'delete_watched':False,'x':True}):
   with self.assertRaises(ValueError):ws.update(value)
 def test_disable_waits_for_inflight_deletion_then_blocks_next(self):
  ws.update({'confirm_watch':False,'delete_watched':True});done=threading.Event()
  with ws.deletion_guard() as allowed:
   self.assertTrue(allowed)
   t=threading.Thread(target=lambda:(ws.update({'confirm_watch':False,'delete_watched':False}),done.set()));t.start()
   self.assertFalse(done.wait(.1))
  t.join(2);self.assertTrue(done.is_set())
  with ws.deletion_guard() as allowed:self.assertFalse(allowed)
 def test_disabled_cleanup_never_calls_backend(self):
  m=_dubs_env.load_script('watch_cleanup_test',_dubs_env.SRC/'control.py')
  ws.update({'confirm_watch':False,'delete_watched':False})
  with patch.object(m,'ROLE','worker'),patch.object(m,'api') as api,patch.object(m,'unlink_media') as unlink,patch.object(m,'save'):
   m.cleanup();api.assert_not_called();unlink.assert_not_called()
