"""Offline integration shapes: real Sonarr pages and selected season-pack files."""
import json,tempfile,unittest,sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import _dubs_env
import episodes as ep
import notifications as n

class ObserverTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  self.item={'id':77,'sonarrId':700,'seriesId':4,'showId':8,'season':1,'number':2,'seriesTitle':'Show','title':'Episode'}
  ep.atomic(self.root/'myshows-episodes.json',{'episodes':{'77':self.item}})
  self.queue=[];self.fid=0;self.done=0;self.fail=False
  self.m=SimpleNamespace(WORKER_STATE=self.root,api=self.api,trpc=self.trpc)
  self.db=patch.object(n,'DB',self.root/'events.db');self.db.start()
 def tearDown(self):self.db.stop();self.tmp.cleanup()
 def api(self,path):
  if self.fail:raise RuntimeError('private source failure')
  if path.startswith('queue?'):return {'page':1,'pageSize':200,'records':self.queue,'totalRecords':len(self.queue)}
  if path=='episode/700':return {'id':700,'episodeFileId':self.fid}
  if path.startswith('episodefile/'):return {'id':self.fid,'size':100,'seriesId':4}
  raise AssertionError(path)
 def trpc(self,method,args):
  self.assertEqual(method,'torrent-get')
  return {'torrents':[{'files':[{'name':'Show.S01E01.mkv','length':100},{'name':'Show.S01E02.mkv','length':100}],
                        'fileStats':[{'bytesCompleted':100},{'bytesCompleted':self.done}]}]}
 def add(self):self.queue=[{'episode':{'id':700},'downloadId':'a'*40,'size':200,'sizeLeft':0}]
 def kinds(self):return [e['type'] for e in n.events_after(0,100)]
 def test_real_page_selected_files_pending_import_restart_and_same_hash_regrab(self):
  n.observe_sonarr(self.m) # empty initial baseline
  self.add();n.observe_sonarr(self.m)
  self.assertEqual(self.kinds(),[]) # entire pack queue complete != selected episode complete
  self.done=5;n.observe_sonarr(self.m);self.assertEqual(self.kinds(),['download_started'])
  n.observe_sonarr(self.m);self.assertEqual(len(self.kinds()),1) # persisted restart state
  self.done=100;n.observe_sonarr(self.m);self.assertEqual(self.kinds()[-1],'torrent_complete')
  self.queue=[];n.observe_sonarr(self.m);self.assertNotIn('library_ready',self.kinds())
  self.fid=9;n.observe_sonarr(self.m);self.assertEqual(self.kinds()[-1],'library_ready')
  self.add();self.done=4;n.observe_sonarr(self.m)
  self.assertEqual(self.kinds().count('download_started'),2)
  # Old registered file must not establish readiness for the new generation.
  self.assertEqual(self.kinds().count('library_ready'),1)
 def test_initial_ongoing_baseline_stays_silent(self):
  self.add();self.done=20;n.observe_sonarr(self.m)
  self.done=100;n.observe_sonarr(self.m);self.queue=[];self.fid=10;n.observe_sonarr(self.m)
  self.assertEqual(self.kinds(),[])
 def test_source_errors_preserve_queue_baseline_and_pending(self):
  n.observe_sonarr(self.m);self.add();self.done=5;n.observe_sonarr(self.m)
  before=(self.root/n.OBSERVER_STATE).read_text()
  self.fail=True;n.observe_sonarr(self.m)
  self.assertEqual((self.root/n.OBSERVER_STATE).read_text(),before)
 def test_panel_and_observer_single_owner(self):
  n.observe_sonarr(self.m);self.add();self.done=100
  ep.atomic(self.root/'managed-downloads.json',{'a'*40:{'hash':'a'*40,'episodes':[{'episode':self.item,'state':'downloading'}]}})
  n.emit_episode('download_started',self.item,'panel-job');n.observe_sonarr(self.m)
  self.assertEqual(self.kinds(),['download_started'])
 def test_new_show_first_batch_silent_regardless_order(self):
  items=[dict(self.item,id=i,airDate='2026-10-%02dT00:00:00Z'%i) for i in (1,3,2)]
  n.new_episode_pass(self.m,items);self.assertEqual(self.kinds(),[])
  n.new_episode_pass(self.m,items+[dict(self.item,id=4,airDate='2026-11-01T00:00:00Z')])
  self.assertEqual(self.kinds(),['new_episode'])
 def test_known_future_episode_releases_without_historical_baseline_spam(self):
  item=dict(self.item,airDate='2026-11-10T00:00:00Z')
  with patch.object(n.time,'time',return_value=1794000000):n.new_episode_pass(self.m,[item])
  self.assertEqual(self.kinds(),[])
  with patch.object(n.time,'time',return_value=1795000000):n.new_episode_pass(self.m,[item])
  self.assertEqual(self.kinds(),['new_episode'])
 def test_panel_zero_progress_keeps_start_candidate_until_selected_bytes(self):
  self.done=0
  managed={'a'*40:{'hash':'a'*40,'episodes':[{'episode':self.item,'job':'job','state':'downloading','announceStart':True,'wanted':[1],'video':1}]}}
  ep.atomic(self.root/'managed-downloads.json',managed)
  def torrents(method,args):
   result=self.trpc(method,args);result['torrents'][0]['hashString']='a'*40
   return result
  self.m.trpc=torrents
  with patch.object(ep,'report'):
   ep.monitor(self.m)
   self.assertTrue(ep.read(self.root/'managed-downloads.json',{})['a'*40]['episodes'][0]['announceStart'])
   self.assertEqual(self.kinds(),[])
   self.done=3;ep.monitor(self.m)
   self.assertEqual(self.kinds(),['download_started'])
   ep.monitor(self.m);self.assertEqual(self.kinds(),['download_started'])
 def test_formatting_failure_cannot_escape(self):
  with patch.object(n,'episode_label',side_effect=RuntimeError('private')):
   self.assertFalse(n.emit_episode('download_started',self.item,'job'))
 def test_reset_cursor_does_not_wait_for_old_tail(self):
  n.journal_init();self.assertEqual(n.events_page(999,10)['cursor'],0)
  self.assertTrue(n.events_page(999,10)['reset'])

class RefreshIntegrationTests(unittest.TestCase):
 def test_actual_library_wrapper_survives_refresh_and_event_failure(self):
  from unittest.mock import Mock
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory)
   media={'status':'verified','quality':{'label':'1080p'},'audioTracks':[],'verifiedAt':100,'source':'jellyfin'}
   wrapper={'99:1:2':{'jellyfinUrl':'https://media.example/details?id=public','localMedia':media}}
   m=SimpleNamespace(WORKER_STATE=root,verify_token=Mock(),load=Mock(return_value={'token':'offline'}),
     watching=Mock(return_value=[{'myshowsId':8,'title':'Show','series':{'id':4,'tvdbId':99}}]),
     api=Mock(return_value=[{'id':700,'seasonNumber':1,'episodeNumber':2,'hasFile':True}]))
   def rpc(m,method,args):
    if method=='lists.EpisodesUnwatched':return [{'show':{'id':8},'episodes':[{'id':77,'seasonNumber':1,'episodeNumber':2,'airDate':'2026-10-01T00:00:00Z'}]}]
    return {'days':[]}
   with patch.object(ep,'v3',side_effect=rpc),patch.object(ep.jellyfin_links,'get_library',return_value=wrapper),patch.object(n,'_new_episodes',side_effect=RuntimeError('private')):
    ep.refresh(m)
   payload=ep.read(root/'myshows-episodes.json',{})
   self.assertIsNone(payload['error'])
   self.assertEqual(payload['released'][0]['episodes'][0]['localMedia'],media)
   self.assertEqual(payload['episodes']['77']['jellyfinUrl'],wrapper['99:1:2']['jellyfinUrl'])

if __name__=='__main__':unittest.main()
