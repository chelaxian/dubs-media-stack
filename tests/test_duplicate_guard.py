import contextlib,json,os,sys,tempfile,types,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import _dubs_env
import episodes as ep
import duplicate_guard as guard
class Tests(unittest.TestCase):
 def env(self,d):
  root=Path(d);(root/'managed-downloads.json').unlink(missing_ok=True)
  m=types.SimpleNamespace(WORKER_STATE=root,trpc=Mock(),safe_path=lambda p,_root=None:Path(p),fingerprint=lambda p:[1,2,p.stat().st_size],disable_source=Mock())
  def unlink(path,expected):
   self.assertEqual(expected,[1,2,path.stat().st_size]);path.unlink()
  m.unlink_media=unlink
  return m
 def torrent(self,d,h='a'*40):
  base=Path(d)/'torrent'/'complete'/'tv'/'Panel'/'job';base.mkdir(parents=True,exist_ok=True)
  a=base/'Show.S01E01.mkv';b=base/'Show.S01E02.mkv';a.write_bytes(b'1');b.write_bytes(b'22')
  return {'id':7,'name':'Show','hashString':h,'downloadDir':'/downloads/complete/tv/Panel/job','files':[{'name':'Show.S01E01.mkv'},{'name':'Show.S01E02.mkv'}],'fileStats':[{'wanted':True},{'wanted':True}]},a,b
 def test_source_copy_for_imported_episode_is_removed(self):
  with tempfile.TemporaryDirectory() as d:
   m=self.env(d);t,a,b=self.torrent(d);h=t['hashString']
   ep.atomic(m.WORKER_STATE/'managed-downloads.json',{h:{'wanted':[0,1],'episodes':[{'job':'b'*32,'state':'done','video':0},{'job':'c'*32,'state':'downloading','video':1}]}},0o600)
   calls=[]
   def rpc(method,args):
    calls.append((method,args))
    if method=='torrent-get':return {'torrents':[t]}
    return {}
   m.trpc.side_effect=rpc;paths={'shows_host':str(Path(d)/'shows'),'torrent_host':str(Path(d)/'torrent'),'downloads':'/downloads'}
   with patch.object(guard.rt,'media_paths',return_value=paths):
    result=guard.run(m,ep)
   self.assertFalse(a.exists());self.assertTrue(b.exists());self.assertIn(('torrent-set',{'ids':[7],'files-unwanted':[0]}),calls)
   self.assertEqual(result['removed'],1);state=ep.read(m.WORKER_STATE/'managed-downloads.json',{});self.assertIn('sourceCleanedAt',state[h]['episodes'][0]);self.assertNotIn('sourceCleanedAt',state[h]['episodes'][1])
 def test_finished_shared_torrent_stops_after_all_sources_removed(self):
  with tempfile.TemporaryDirectory() as d:
   m=self.env(d);t,a,b=self.torrent(d);h=t['hashString']
   ep.atomic(m.WORKER_STATE/'managed-downloads.json',{h:{'wanted':[0,1],'episodes':[{'job':'b'*32,'state':'done','video':0},{'job':'c'*32,'state':'done','video':1}]}},0o600)
   calls=[]
   def rpc(method,args):
    calls.append((method,args))
    if method=='torrent-get':return {'torrents':[t]}
    return {}
   m.trpc.side_effect=rpc;paths={'shows_host':str(Path(d)/'shows'),'torrent_host':str(Path(d)/'torrent'),'downloads':'/downloads'}
   with patch.object(guard.rt,'media_paths',return_value=paths):result=guard.run(m,ep)
   self.assertFalse(a.exists());self.assertFalse(b.exists());self.assertEqual(result['removed'],2);self.assertIn(('torrent-stop',{'ids':[7]}),calls)
 def test_orphaned_finished_job_directory_is_removed(self):
  with tempfile.TemporaryDirectory() as d:
   m=self.env(d);root=Path(d)/'torrent'/'complete'/'tv'/'Panel'/('b'*32);root.mkdir(parents=True);video=root/'Show.S01E01.mkv';video.write_bytes(b'123')
   h='c'*40;ep.atomic(m.WORKER_STATE/'managed-downloads.json',{h:{'wanted':[0],'episodes':[{'job':'b'*32,'state':'done','video':0,'episode':{'season':1,'number':1}}]}},0o600)
   m.trpc.return_value={'torrents':[]};paths={'shows_host':str(Path(d)/'shows'),'torrent_host':str(Path(d)/'torrent'),'downloads':'/downloads'}
   with patch.object(guard.rt,'media_paths',return_value=paths):result=guard.run(m,ep)
   self.assertFalse(root.exists());self.assertEqual(result['removed'],1);state=ep.read(m.WORKER_STATE/'managed-downloads.json',{});self.assertIn('sourceOrphanCleanedAt',state[h])
 def test_library_duplicate_keeps_sonarr_registered_file(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)/'shows';season=root/'Show (2026)'/'Season 01';season.mkdir(parents=True);keep=season/'Show.S01E01.keep.mkv';old=season/'Show.S01E01.old.mkv';keep.write_bytes(b'keep');old.write_bytes(b'old')
   m=self.env(d)
   @contextlib.contextmanager
   def parent(path,_root):
    fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:yield fd,path.name
    finally:os.close(fd)
   m.parent_fd=parent
   def api(path):
    if path=='series':return [{'id':1,'title':'Show','path':'/tv/Show (2026)'}]
    if path=='episode?seriesId=1':return [{'seasonNumber':1,'episodeNumber':1,'hasFile':True,'episodeFileId':9}]
    if path=='episodefile/9':return {'path':'/tv/Show (2026)/Season 01/Show.S01E01.keep.mkv'}
    raise AssertionError(path)
   m.api=Mock(side_effect=api);paths={'shows_host':str(root),'torrent_host':str(Path(d)/'torrent'),'sonarr_tv':'/tv','downloads':'/downloads'}
   with patch.object(guard.rt,'media_paths',return_value=paths):result=guard.run_library(m,ep)
   self.assertTrue(keep.exists());self.assertFalse(old.exists());self.assertEqual(result['groups'],1);self.assertEqual(result['removed'],1)
if __name__=='__main__':unittest.main()
