import unittest,tempfile,json,datetime
from pathlib import Path
from unittest.mock import patch
import _dubs_env
from _dubs_env import load_script
m=load_script('ctrl',_dubs_env.SRC/'control.py')
class Tests(unittest.TestCase):
 def setUp(self):self.oldrole=m.ROLE;m.ROLE="worker"
 def tearDown(self):m.ROLE=self.oldrole
 def test_only_watched_dates(self):
  detail={'episodes':[{'id':1,'seasonNumber':1,'episodeNumber':1},{'id':2,'seasonNumber':1,'episodeNumber':2}]};eps=[{'id':11,'seasonNumber':1,'episodeNumber':1},{'id':12,'seasonNumber':1,'episodeNumber':2}]
  for date,n in [(None,0),('',0),('invalid',0),('0000-00-00',0),('2999-01-01',0),('2026-01-01',1)]:
   with self.subTest(date=date):self.assertEqual(len(m.match_watched(detail,[{'id':1,'watchDate':date}],eps)),n)
 def test_ambiguous_special_numbering(self):
  detail={'episodes':[{'id':1,'seasonNumber':1,'episodeNumber':1},{'id':2,'seasonNumber':1,'episodeNumber':1},{'id':3,'seasonNumber':0,'episodeNumber':1}]}
  self.assertEqual(m.match_watched(detail,[{'id':i,'watchDate':'2026-01-01'} for i in [1,2,3]],[{'id':10,'seasonNumber':1,'episodeNumber':1},{'id':11,'seasonNumber':0,'episodeNumber':1}]),[])
 def test_path_boundaries(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)/'root';root.mkdir();outside=Path(d)/'outside';outside.write_text('x');(root/'link').symlink_to(outside)
   for bad in [str(root),str(root/'../outside'),str(root/'link'),'relative']:
    with self.subTest(bad=bad),self.assertRaises(ValueError):m.safe_path(bad,root)
   self.assertEqual(m.safe_path(str(root/'video'),root),root/'video')
 def test_no_auth_no_api(self):
  with patch.object(m,'load',return_value={}),patch.object(m,'save'),patch.object(m,'api') as api,patch.object(m.rt,'cleanup_enabled',return_value=True),patch.object(m.rt,'storage_ready',return_value=(True,'fixture')):
   with patch.object(m.rt,'feed_state_root',return_value=Path('/nonexistent-feed')):m.cleanup();api.assert_not_called()
 def test_bad_token_no_deletions(self):
  with patch.object(m,'load',return_value={'token':'fake'}),patch.object(m,'verify_token',side_effect=ValueError('bad')),patch.object(m,'api') as api,patch.object(m.rt,'cleanup_enabled',return_value=True),patch.object(m.rt,'storage_ready',return_value=(True,'fixture')):
   with self.assertRaises(ValueError):m.cleanup()
   api.assert_not_called()
 def test_different_account(self):
  with patch.object(m,'myrpc',return_value={'user':{'login':'someoneelse'}}):
   with self.assertRaises(ValueError):m.verify_token('fake')
 def test_token_header_injection(self):
  with self.assertRaises(ValueError):m.verify_token('x\nInjected: a')
 def test_unrelated_missing_subtitle_does_not_block_source(self):
  source=Path('/mnt/torrent/complete/tv/Dexter/episode.mkv')
  torrents=[{'id':1,'downloadDir':'/downloads/old','files':[{'name':'SRT/missing.srt'}]},{'id':2,'downloadDir':'/downloads/complete/tv/Dexter','files':[{'name':'episode.mkv'},{'name':'other.mkv'}]}]
  with patch.object(m,'trpc',return_value={'torrents':torrents}) as rpc,patch.object(m,'safe_path',return_value=source) as safe:
   m.disable_source(source)
   safe.assert_called_once_with(str(source),'/mnt/torrent')
   self.assertEqual(rpc.call_args_list[-1].args,('torrent-set',{'ids':[2],'files-unwanted':[0]}))
   self.assertEqual(rpc.call_count,2)
 def test_last_selected_source_stops_before_unselect(self):
  source=Path('/mnt/torrent/Dexter/episode.mkv')
  torrents=[{'id':2,'downloadDir':'/downloads/Dexter','files':[{'name':'episode.mkv'},{'name':'old.mkv'}],'fileStats':[{'wanted':True},{'wanted':False}]}]
  with patch.object(m,'trpc',return_value={'torrents':torrents}) as rpc,patch.object(m,'safe_path',return_value=source):
   m.disable_source(source)
   self.assertEqual([c.args[0] for c in rpc.call_args_list],['torrent-get','torrent-stop','torrent-set'])
 def test_shared_season_keeps_other_selected_episode_running(self):
  source=Path('/mnt/torrent/Dexter/episode.mkv')
  torrents=[{'id':2,'downloadDir':'/downloads/Dexter','files':[{'name':'episode.mkv'},{'name':'next.mkv'}],'fileStats':[{'wanted':True},{'wanted':True}]}]
  with patch.object(m,'trpc',return_value={'torrents':torrents}) as rpc,patch.object(m,'safe_path',return_value=source):
   m.disable_source(source)
   self.assertEqual([c.args[0] for c in rpc.call_args_list],['torrent-get','torrent-set'])
 def test_no_matching_source_does_not_walk_other_torrents(self):
  with patch.object(m,'trpc',return_value={'torrents':[{'id':1,'downloadDir':'/downloads/old','files':[{'name':'SRT/missing.srt'}]}]}) as rpc,patch.object(m,'safe_path') as safe:
   m.disable_source(Path('/mnt/torrent/Dexter/episode.mkv'));safe.assert_not_called();self.assertEqual(rpc.call_count,1)
 def test_matching_source_still_requires_safe_path(self):
  with patch.object(m,'trpc',return_value={'torrents':[{'id':1,'downloadDir':'/downloads/Dexter','files':[{'name':'episode.mkv'}]}]}) as rpc,patch.object(m,'safe_path',side_effect=ValueError('unsafe symlink')):
   with self.assertRaises(ValueError):m.disable_source(Path('/mnt/torrent/Dexter/episode.mkv'))
   self.assertEqual(rpc.call_count,1)
 def cleanup_fixture(self,all_watched=True,dry=False,bad_identity=False):
  with tempfile.TemporaryDirectory() as d:
   state=Path(d);(state/'myshows-auth.json').write_text('{"token":"fake"}');p=state/'file.mkv';p.write_bytes(b'test')
   mapping=state/'mapping.json';mapping.write_text(json.dumps({'440':{'TvdbId':123,'ImdbId':'tt0000123'}}))
   eps=[{'id':11,'seasonNumber':1,'episodeNumber':1,'episodeFileId':9,'monitored':True},{'id':12,'seasonNumber':1,'episodeNumber':2,'episodeFileId':9,'monitored':True}]
   def api(path,method='GET',data=None):
    if path=='series':return [{'id':1,'tvdbId':123,'seriesType':'standard','title':'Test'}]
    if path.startswith('episode?'):return eps
    if path=='episodefile/9' and method=='GET':return {'path':'/tv/file.mkv'}
   detail={'imdbId':999 if bad_identity else 123,'episodes':[{'id':1,'seasonNumber':1,'episodeNumber':1},{'id':2,'seasonNumber':1,'episodeNumber':2}]}
   def rpc(method,params,token):return ([{'id':i,'watchDate':'2026-01-01'} for i in ([1,2] if all_watched else [1])] if method=='profile.Episodes' else detail)
   original=m.Path
   def path(raw):return mapping if str(raw)==str(mapping) else original(raw)
   with patch.object(m,'STATE',state),patch.object(m,'WORKER_STATE',state),patch.object(m,'Path',side_effect=path),patch.object(m,'api',side_effect=api) as calls,patch.object(m,'verify_token'),patch.object(m,'myrpc',side_effect=rpc),patch.object(m,'safe_path',return_value=p),patch.object(m,'source_copy',return_value=None),patch.object(m.time,'sleep'),patch.object(m.rt,'storage_ready',return_value=(True,'fixture')),patch.object(m.rt,'cleanup_enabled',return_value=True),patch.object(m.rt,'feed_state_root',return_value=state):
    m.cleanup(dry=dry);return [(c.args[0],c.args[1] if len(c.args)>1 else 'GET') for c in calls.call_args_list]
 def test_shared_file_not_all_watched(self):self.assertNotIn(('episodefile/9','DELETE'),self.cleanup_fixture(False))
 def test_all_watched_deletes(self):
  calls=self.cleanup_fixture();self.assertIn(('episodefile/9','DELETE'),calls);self.assertLess(calls.index(('episode/monitor','PUT')),calls.index(('episodefile/9','DELETE')))
 def test_dry_run_no_mutations(self):self.assertTrue(all(meth=='GET' for _,meth in self.cleanup_fixture(dry=True)))
 def test_wrong_identity_no_mutations(self):self.assertTrue(all(meth=='GET' for _,meth in self.cleanup_fixture(bad_identity=True)))
if __name__=='__main__':unittest.main()
