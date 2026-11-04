import unittest,sys,json,datetime,tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import _dubs_env  # noqa: F401
import upgrades as u
class Tests(unittest.TestCase):
 def setUp(self):
  self.studios=json.loads((_dubs_env.REPO/'studios.json').read_text())
 def model(self,preference=None):return SimpleNamespace(STUDIOS=self.studios,load=lambda *a:({'1':preference} if preference else {}))
 def test_default(self):
  m=self.model();self.assertEqual(u.voice_rank(m,1,'Red Head Sound'),0);self.assertEqual(u.voice_rank(m,1,'LostFilm'),1);self.assertEqual(u.voice_rank(m,1,'Кураж-Бамбей'),11);self.assertEqual(u.voice_rank(m,1,'sndk'),12)
 def test_fallback(self):
  m=self.model({'studio':11,'mode':'fallback'});self.assertEqual(u.voice_rank(m,1,'Кураж-Бамбей'),0);self.assertEqual(u.voice_rank(m,1,'RHS'),1);self.assertEqual(u.voice_rank(m,1,'LostFilm'),2)
 def test_strict(self):
  m=self.model({'studio':11,'mode':'strict'});self.assertIsNone(u.voice_rank(m,1,'RHS'));self.assertEqual(u.voice_rank(m,1,'Кураж-Бамбей'),0)
 def test_other_selected(self):
  m=self.model({'studio':13,'mode':'strict'});self.assertEqual(u.voice_rank(m,1,'Some Russian release'),0);self.assertIsNone(u.voice_rank(m,1,'sndk'))
 def test_no_regression(self):
  self.assertTrue(u.better((2160,0),(1080,8)));self.assertTrue(u.better((1080,0),(1080,8)));self.assertTrue(u.better((2160,8),(1080,8)))
  self.assertFalse(u.better((2160,8),(1080,0)));self.assertFalse(u.better((1080,0),(2160,8)));self.assertFalse(u.better((2160,0),(2160,0)))
 def test_daily18(self):
  self.assertFalse(u.due({},datetime.datetime(2026,11,4,17,59,tzinfo=u.TZ)));self.assertTrue(u.due({},datetime.datetime(2026,11,4,18,tzinfo=u.TZ)));self.assertFalse(u.due({'day':'2026-11-04'},datetime.datetime(2026,11,4,19,tzinfo=u.TZ)));self.assertTrue(u.due({'day':'2026-11-04'},datetime.datetime(2026,11,5,18,tzinfo=u.TZ)))
 def test_changed_preference_cancels_inflight_replacement(self):
  m=self.model({'studio':11,'mode':'strict'})
  with patch.object(u,'current_rank',return_value=(1080,0)):
   self.assertFalse(u.replacement_allowed(m,{'seriesId':1,'_upgradeFileId':3},{'episodeFileId':3},'RHS',{'quality':{'resolution':2160}}))
   self.assertTrue(u.replacement_allowed(m,{'seriesId':1,'_upgradeFileId':3},{'episodeFileId':3},'Кураж-Бамбей',{'quality':{'resolution':2160}}))
 def test_changed_existing_file_cancels_replacement(self):
  self.assertFalse(u.replacement_allowed(self.model(),{'_upgradeFileId':2},{'episodeFileId':3},'RHS',{}))
 def test_maximum_skips_search(self):
  m=SimpleNamespace(api=lambda *a:{'hasFile':True,'episodeFileId':3})
  with patch.object(u,'current_rank',return_value=(2160,0)),patch.object(u,'still_unwatched') as watched:
   self.assertEqual(u.upgrade_one(m,None,{'sonarrId':1}),'maximum');watched.assert_not_called()
 def test_watched_skips_search(self):
  m=SimpleNamespace(api=lambda *a:{'hasFile':True,'episodeFileId':3})
  with patch.object(u,'current_rank',return_value=(1080,8)),patch.object(u,'still_unwatched',return_value=False):self.assertEqual(u.upgrade_one(m,None,{'sonarrId':1}),'watched')
 def test_missing_skips_search(self):self.assertEqual(u.upgrade_one(SimpleNamespace(api=lambda *a:{'hasFile':False}),None,{'sonarrId':1}),'missing')
 def test_daily_upgrade_only_selects_non_regressing_candidate(self):
  import episodes as ep
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'release-cache').mkdir();(root/'jobs').mkdir()
   m=self.model();m.WORKER_STATE=root;m.api=lambda *a:{'hasFile':True,'episodeFileId':12}
   e={'id':4,'sonarrId':5,'seriesId':1}
   def item(title,res):return {'row':{'title':title,'quality':{'quality':{'resolution':res}}},'display':{'title':title,'allowed':True,'seeders':10}}
   candidates=[item('DMS',2160),item('RHS',1080)]
   def search(m,key,e):ep.atomic(root/'release-cache'/(key+'.json'),{'items':candidates})
   with patch.object(u,'current_rank',return_value=(1080,1)),patch.object(u,'still_unwatched',return_value=True),patch.object(ep,'search',side_effect=search),patch.object(ep,'request_download',return_value={'state':'downloading','message':'test'}) as download:
    self.assertEqual(u.upgrade_one(m,ep,e),'downloading');self.assertEqual(download.call_args.args[3]['row']['title'],'RHS');self.assertEqual(download.call_args.args[2]['_upgradeFileId'],12)
 def test_no_better_release_keeps_existing_file(self):
  import episodes as ep
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'release-cache').mkdir();(root/'jobs').mkdir()
   m=self.model();m.WORKER_STATE=root;m.api=lambda *a:{'hasFile':True,'episodeFileId':12}
   with patch.object(u,'current_rank',return_value=(1080,1)),patch.object(u,'still_unwatched',return_value=True),patch.object(ep,'search'),patch.object(ep,'request_download') as download:
    self.assertEqual(u.upgrade_one(m,ep,{'id':4,'sonarrId':5,'seriesId':1}),'no_better_release');download.assert_not_called()
 def test_multivoice_uses_best(self):self.assertEqual(u.voice_rank(self.model(),1,'LostFilm RHS DMS'),0)
 def test_stored_format_identity(self):self.assertEqual(u.voice_rank(self.model(),1,'unlabeled.mkv',[{'name':'Озвучка 02 — LostFilm'}]),1)
if __name__=='__main__':unittest.main()
