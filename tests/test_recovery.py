import unittest,copy,tempfile
from pathlib import Path
from unittest.mock import patch
import _dubs_env  # noqa: F401
import download_recovery as r
class RecoveryTests(unittest.TestCase):
 def t(self,**kw):
  x=dict(status=4,downloadedEver=0,rateDownload=0,leftUntilDone=100,error=0);x.update(kw);return x
 def test_first_observation_not_failed(self):self.assertEqual(r.stalled_action(self.t(),None,100)[1],'observe')
 def test_manual_pause_resets(self):self.assertEqual(r.stalled_action(self.t(status=0),dict(bytes=0,checked=0,idle=5000),300)[0]['idle'],0)
 def test_poweroff_does_not_count(self):self.assertEqual(r.stalled_action(self.t(),dict(bytes=0,checked=0,idle=0),90000)[0]['idle'],360)
 def test_progress_resets(self):self.assertEqual(r.stalled_action(self.t(downloadedEver=50),dict(bytes=0,checked=0,idle=5000),300)[0]['idle'],0)
 def test_reannounce_then_replace(self):
  p,a=r.stalled_action(self.t(),dict(bytes=0,checked=0,idle=3300),300);self.assertEqual(a,'announce')
  p['checked']=0;p['idle']=4200;self.assertEqual(r.stalled_action(self.t(),p,300)[1],'replace')
 def test_storage_error_never_replaces(self):self.assertEqual(r.stalled_action(self.t(error=3),dict(bytes=0,checked=0,idle=5000),300)[1],'observe')
 def test_complete_never_replaces(self):self.assertEqual(r.stalled_action(self.t(leftUntilDone=0),dict(bytes=0,checked=0,idle=5000),300)[1],'observe')
 def test_scope_protects_single_episode(self):
  scope=dict(app='sonarr',media=30,season=13,numbers=[13],full=False)
  rel=dict(mappedSeriesId=30,mappedSeasonNumber=13,fullSeason=True,rejected=False)
  self.assertFalse(r.fits(rel,scope));rel.update(fullSeason=False,mappedEpisodeNumbers=[12,13]);self.assertFalse(r.fits(rel,scope));rel['mappedEpisodeNumbers']=[13];self.assertTrue(r.fits(rel,scope))
 def test_rejections_quality_and_seeders_preserved(self):
  scope=dict(app='sonarr',media=1,season=2,numbers=[1],full=False);rel=dict(mappedSeriesId=1,mappedSeasonNumber=2,episodeNumbers=[1],rejected=True,rejections=['Not enough seeders'])
  self.assertFalse(r.fits(rel,scope));rel['rejections']=['Episode is already being downloaded'];self.assertTrue(r.fits(rel,scope))
 def test_exhaustion_stops_and_unmonitors_no_data_delete(self):
  from unittest.mock import Mock
  a=Mock(app='sonarr');a.call.side_effect=[[],[],None,None]
  tr=Mock();scope=dict(app='sonarr',media=1,season=2,numbers=[1],ids=[7],full=False,key='test')
  state={'scopes':{}};t=dict(id=3,hashString='abc');rows=[dict(id=99)]
  with patch.object(r,'save'):r.replace(a,tr,t,rows,scope,state)
  self.assertEqual(state['scopes']['test']['status'],'exhausted');self.assertEqual(tr.call.call_args_list[0].args,('torrent-stop',{'ids':[3]}));self.assertFalse(any(c.args[0]=='torrent-remove' for c in tr.call.call_args_list))
  self.assertTrue(any(c.args[:2]==('episode/monitor','PUT') for c in a.call.call_args_list))
 def test_indexer_failure_not_exhaustion(self):
  from unittest.mock import Mock
  a=Mock(app='sonarr');a.call.side_effect=[[],[dict(source='IndexerStatusCheck')]];tr=Mock();state={'scopes':{}}
  r.replace(a,tr,dict(id=3,hashString='abc'),[dict(id=99)],dict(app='sonarr',media=1,ids=[7],full=False,key='test'),state)
  self.assertEqual(state['scopes']['test']['status'],'waiting_for_indexers');tr.call.assert_not_called()
 def test_duplicate_grab_does_not_remove_current(self):
  from unittest.mock import Mock
  a=Mock(app='sonarr');rel=dict(guid='release',indexerId=1,title='test',mappedSeriesId=1,mappedSeasonNumber=2,episodeNumbers=[1],rejected=False)
  a.call.side_effect=[[rel],None,[] ,None,None]
  tr=Mock();tr.call.side_effect=[{'torrents':[{'hashString':'abc'}]},{'torrents':[{'hashString':'abc'}]},{} ,{}]
  state={'scopes':{}};scope=dict(app='sonarr',media=1,ids=[7],numbers=[1],season=2,full=False,key='test')
  with patch.object(r,'save'):r.replace(a,tr,dict(id=3,hashString='abc'),[dict(id=99)],scope,state)
  self.assertFalse(any(c.args[0]=='torrent-remove' for c in tr.call.call_args_list))
 def test_network_error_keeps_old_torrent(self):
  from unittest.mock import Mock
  a=Mock(app='sonarr');rel=dict(guid='release',indexerId=1,title='test',mappedSeriesId=1,mappedSeasonNumber=2,episodeNumbers=[1],rejected=False)
  a.call.side_effect=[[rel],r.requests.Timeout()];tr=Mock();tr.call.return_value={'torrents':[{'hashString':'abc'}]}
  state={'scopes':{}};scope=dict(app='sonarr',media=1,ids=[7],numbers=[1],season=2,full=False,key='test')
  with patch.object(r,'save'),self.assertRaises(r.requests.Timeout):r.replace(a,tr,dict(id=3,hashString='abc'),[dict(id=99)],scope,state)
  self.assertEqual(state['scopes']['test']['attempted'],[])
  self.assertFalse(any(c.args[0] in ['torrent-stop','torrent-remove'] for c in tr.call.call_args_list))
if __name__=='__main__':unittest.main()
