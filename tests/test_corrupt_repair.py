import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import _dubs_env
from _dubs_env import load_script
m=load_script('ctrl',_dubs_env.SRC/'control.py')

def torrent(err='Please Verify Local Data! Piece #1166 is corrupt.'):
 return {'id':7,'name':'12 Monkeys (Season 3)','errorString':err,'downloadDir':'/downloads',
         'files':[{'name':'12 Monkeys (Season 3)/e01.mkv','length':100},{'name':'12 Monkeys (Season 3)/e02.mkv','length':200}],
         'fileStats':[{'wanted':True,'bytesCompleted':100},{'wanted':True,'bytesCompleted':200}]}

class CorruptRepairTests(unittest.TestCase):
 def env(self,torrents,on_disk=(1,)):
  root=Path(tempfile.mkdtemp());paths=[]
  folder=root/'12 Monkeys (Season 3)';folder.mkdir()
  for i in range(2):
   p=folder/f'e0{i+1}.mkv';paths.append(p)
   if i in on_disk:p.write_bytes(b'x'*(100 if i==0 else 200))
  mp={'torrent_host':str(root),'downloads':'/downloads'}
  calls=[]
  def rpc(method,args=None):
   calls.append((method,args))
   return {'torrents':torrents} if method=='torrent-get' else {}
  return mp,calls,rpc
 def run_repair(self,torrents,on_disk=(1,)):
  mp,calls,rpc=self.env(torrents,on_disk)
  with patch.object(m.rt,'media_paths',return_value=mp),patch.object(m,'trpc',side_effect=rpc),patch.object(m,'safe_path',side_effect=lambda p,_root=None: Path(p)),patch.object(m,'save'):
   m.repair_corrupt_torrents()
  return calls
 def test_deselects_intentionally_deleted_and_verifies(self):
  calls=self.run_repair([torrent()])
  self.assertIn(('torrent-set',{'ids':[7],'files-unwanted':[0]}),calls)
  self.assertIn(('torrent-verify',{'ids':[7]}),calls)
 def test_whole_disk_loss_keeps_normal_repair(self):
  calls=self.run_repair([torrent()],on_disk=())
  self.assertNotIn(('torrent-set',{'ids':[7],'files-unwanted':[0]}),calls)
  self.assertIn(('torrent-verify',{'ids':[7]}),calls)
 def test_healthy_torrent_ignored(self):
  calls=self.run_repair([torrent(err='')])
  self.assertEqual(calls,[('torrent-get',{'fields':['id','name','errorString','downloadDir','files','fileStats']})])
 def test_cleanup_collects_verify_ids(self):
  source=Path('/mnt/torrent/Dexter/episode.mkv')
  torrents=[{'id':2,'downloadDir':'/downloads/Dexter','files':[{'name':'episode.mkv'},{'name':'next.mkv'}],'fileStats':[{'wanted':True},{'wanted':True}]}]
  mp={'torrent_host':'/mnt/torrent','downloads':'/downloads'}
  with patch.object(m.rt,'media_paths',return_value=mp),patch.object(m,'safe_path',return_value=source),patch.object(m,'trpc',return_value={'torrents':torrents}) as rpc:
   self.assertEqual(m.disable_source(source),[2])
   m.verify_torrents([2])
   self.assertEqual(rpc.call_args.args,('torrent-verify',{'ids':[2]}))
if __name__=='__main__':unittest.main()
