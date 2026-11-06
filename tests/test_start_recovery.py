import tempfile,time,types,unittest
from pathlib import Path
import _dubs_env
import episodes as ep

class StartRecovery(unittest.TestCase):
 def test_recovers_only_unconfirmed_start_and_preserves_later_pause(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);(root/'jobs').mkdir();calls=[];state={'status':0}
   torrent={'id':1,'hashString':'a'*40,'files':[{'name':'S01E01.mkv','length':100}],
            'fileStats':[{'bytesCompleted':0}],'downloadDir':'/downloads','error':0,'rateDownload':0}
   def rpc(method,args):
    calls.append(method)
    if method=='torrent-get':return {'torrents':[dict(torrent,status=state['status'])]}
    return {}
   m=types.SimpleNamespace(WORKER_STATE=root,trpc=rpc)
   record={'hash':'a'*40,'wanted':[0],'startPending':time.time()-61,'startAttempts':0,
           'episodes':[{'job':'b'*32,'episode':{'id':1},'wanted':[0],'state':'downloading'}]}
   ep.atomic(root/'managed-downloads.json',{'a'*40:record})
   ep.monitor(m);self.assertEqual(calls.count('torrent-start'),1)
   saved=ep.read(root/'managed-downloads.json',{})['a'*40];self.assertEqual(saved['startAttempts'],1)
   state['status']=4;ep.monitor(m)
   self.assertNotIn('startPending',ep.read(root/'managed-downloads.json',{})['a'*40])
   state['status']=0;ep.monitor(m);self.assertEqual(calls.count('torrent-start'),1)
