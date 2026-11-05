import os,tempfile,unittest,datetime,json,time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import _dubs_env
import schedule_settings as settings
import upgrades as u
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.env=patch.dict(os.environ,{'SERIES_CONTROL_STATE_ROOT':str(self.root)});self.env.start()
 def tearDown(self):self.env.stop();self.tmp.cleanup()
 def test_defaults_and_validation(self):
  self.assertEqual(settings.current()['sync_minutes'],30)
  for field,value in [('sync_minutes',0),('sync_minutes',True),('upgrade_time','25:00'),('upgrade_interval_hours',169)]:
   with self.assertRaises(ValueError):settings.update(dict(settings.defaults(),**{field:value}))
 def test_time_frequency_and_persistence(self):
  cfg={'sync_minutes':15,'upgrade_time':'19:30','upgrade_interval_hours':24};settings.update(cfg);self.assertEqual(settings.current(),cfg)
  now=datetime.datetime(2026,11,5,19,29,tzinfo=u.TZ);self.assertFalse(u.due({},now))
  now=now.replace(minute=30);self.assertTrue(u.due({},now));state={'scheduledSlot':u.schedule_slot(now)}
  self.assertFalse(u.due(state,now+datetime.timedelta(hours=23)));self.assertTrue(u.due(state,now+datetime.timedelta(hours=24)))
  settings.update(dict(cfg,upgrade_interval_hours=6));state={'scheduledSlot':u.schedule_slot(now)}
  self.assertFalse(u.due(state,now+datetime.timedelta(hours=5)));self.assertTrue(u.due(state,now+datetime.timedelta(hours=6)))
 def test_actual_audio_overrides_misleading_release(self):
  studios=json.loads((_dubs_env.REPO/'studios.json').read_text());cache={'entries':{'1:9:3':{'status':'verified','verifiedAt':time.time(),'identity':{'id':61},'audioTracks':[{'language':'rus','title':'MVO TVShows'}]}}}
  (self.root/'local-media.json').write_text(json.dumps(cache));m=SimpleNamespace(WORKER_STATE=self.root,STUDIOS=studios,load=lambda *a:{'21':{'studio':'Сыендук / Syenduk / sndk'}},api=lambda *a:{'quality':{'quality':{'resolution':1080}},'sceneName':'Сыендук'})
  rank=u.current_rank(m,{'seriesId':21},{'episodeFileId':61});self.assertGreater(rank[1],0)
  cache['entries']['1:9:3']['audioTracks'][0]['title']='AVO Сыуендук';(self.root/'local-media.json').write_text(json.dumps(cache))
  self.assertEqual(u.current_rank(m,{'seriesId':21},{'episodeFileId':61})[1],0)
