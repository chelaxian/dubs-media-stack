import tempfile,time,types,unittest
from pathlib import Path
import _dubs_env
import episodes as ep
from unittest.mock import patch

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

 def test_stopped_active_order_is_resumed_only_with_exact_files_and_storage(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);(root/'jobs').mkdir();calls=[]
   torrent={'hashString':'a'*40,'status':0,'files':[{'name':'S01E01.mkv','length':100}],
            'fileStats':[{'bytesCompleted':0,'wanted':True}],'downloadDir':'/downloads','error':0,'rateDownload':0}
   def rpc(method,args):
    calls.append(method)
    return {'torrents':[torrent]} if method=='torrent-get' else {}
   m=types.SimpleNamespace(WORKER_STATE=root,trpc=rpc)
   record={'hash':'a'*40,'wanted':[0],'pausedSince':time.time()-121,
           'episodes':[{'job':'b'*32,'episode':{'id':1},'wanted':[0],'state':'downloading'}]}
   ep.atomic(root/'managed-downloads.json',{'a'*40:record})
   with patch.object(ep.rt,'storage_ready',return_value=(False,'offline')):ep.monitor(m)
   self.assertNotIn('torrent-start',calls)
   torrent['fileStats'][0]['wanted']=False
   with patch.object(ep.rt,'storage_ready',return_value=(True,'')):ep.monitor(m)
   self.assertNotIn('torrent-start',calls)
   torrent['fileStats'][0]['wanted']=True
   with patch.object(ep.rt,'storage_ready',return_value=(True,'')):ep.monitor(m)
   self.assertEqual(calls.count('torrent-start'),1)
   saved=ep.read(root/'managed-downloads.json',{})['a'*40]
   self.assertEqual(saved['startAttempts'],1)
   self.assertTrue(saved.get('startPending'))

 def test_reannounces_stalled_active_torrent_without_restarting_it(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);(root/'jobs').mkdir();calls=[];h='c'*40
   torrent={'hashString':h,'status':4,'files':[{'name':'S01E01.mkv','length':100}],
            'fileStats':[{'bytesCompleted':4,'wanted':True}],'downloadDir':'/downloads','error':0,'rateDownload':0}
   def rpc(method,args):
    calls.append(method)
    return {'torrents':[torrent]} if method=='torrent-get' else {}
   m=types.SimpleNamespace(WORKER_STATE=root,trpc=rpc)
   ep.atomic(root/'managed-downloads.json',{h:{'hash':h,'wanted':[0],
    'episodes':[{'job':'d'*32,'episode':{'id':1},'wanted':[0],'state':'downloading'}]}})
   ep.STALL_OBSERVATIONS[h]={'progress':4,'since':time.time()-901}
   ep.monitor(m)
   self.assertIn('torrent-reannounce',calls)
   self.assertNotIn('torrent-start',calls)
   ep.STALL_OBSERVATIONS.pop(h,None)
