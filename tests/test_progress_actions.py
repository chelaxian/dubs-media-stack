import sys,json,tempfile,types,time,threading,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import _dubs_env  # offline roots; src/automations on sys.path
import episodes as ep
class Tests(unittest.TestCase):
 def env(self,d):
  root=Path(d);(root/'jobs').mkdir();(root/'requests').mkdir();(root/'release-cache').mkdir();return types.SimpleNamespace(STATE=root,WORKER_STATE=root,trpc=Mock(),api=Mock())
 def test_cancel_keeps_other_episode_and_files(self):
  with tempfile.TemporaryDirectory() as d:
   m=self.env(d);h='a'*40;ep.atomic(m.WORKER_STATE/'managed-downloads.json',{h:{'wanted':[0,1],'episodes':[{'job':'b'*32,'episode':{'id':1},'state':'downloading','wanted':[0]},{'job':'c'*32,'episode':{'id':2},'state':'downloading','wanted':[1]}]}})
   ep.cancel_episode(m,1);m.trpc.assert_called_once_with('torrent-set',{'ids':[h],'files-unwanted':[0]});data=ep.read(m.WORKER_STATE/'managed-downloads.json',{});self.assertEqual(data[h]['wanted'],[1]);self.assertEqual(data[h]['episodes'][0]['state'],'cancelled');self.assertEqual(data[h]['episodes'][1]['state'],'downloading')
 def test_cancel_blocks_old_job_but_not_new(self):
  with tempfile.TemporaryDirectory() as d:
   m=self.env(d);key='d'*32;ep.atomic(m.STATE/'requests'/(key+'.json'),{'created':time.time()-5});ep.cancel_episode(m,1)
   with self.assertRaises(ep.UserError):ep.check_cancelled(m,key,1)
   ep.atomic(m.STATE/'requests'/(key+'.json'),{'created':time.time()+1});ep.check_cancelled(m,key,1)
 def test_progress_uses_only_selected_episode(self):
  with tempfile.TemporaryDirectory() as d:
   m=self.env(d);h='a'*40;key='b'*32;ep.atomic(m.WORKER_STATE/'managed-downloads.json',{h:{'episodes':[{'job':key,'episode':{'id':1},'state':'downloading','wanted':[0]}]}});m.trpc.return_value={'torrents':[{'hashString':h,'files':[{'length':1000},{'length':99999}],'fileStats':[{'bytesCompleted':250},{'bytesCompleted':0}],'rateDownload':100}]};ep.monitor(m);r=ep.result(m,key);self.assertEqual(r['percent'],25);self.assertEqual(r['total'],1000);self.assertEqual(r['eta'],8)
 def test_results_publish_before_slow_source_finishes(self):
  with tempfile.TemporaryDirectory() as d:
   m=self.env(d);m.api.side_effect=lambda p:{'id':1,'title':'Test','qualityProfileId':1} if p.startswith('series/') else {}
   e={'id':1,'sonarrId':1,'seriesId':1,'seriesTitle':'Test','season':1,'number':1};key='e'*32;ep.atomic(m.STATE/'requests'/(key+'.json'),{'op':'search'})
   def indexer(m,p):
    if p=='indexer':return [{'id':1,'name':'Fast'},{'id':2,'name':'Slow'}]
    if 'indexerIds=2' in p:time.sleep(.5)
    return [{'title':p,'guid':p,'downloadUrl':'http://localhost:9696/1'}]
   def normalize(*args):return {'matchesSeries':True,'seasons':[1]}, {'episodeNumbers':[1],'seasonNumber':1}
   published=[]
   real=ep.report
   def report(*args,**kw):published.append((time.time(),len(kw.get('rows',[]))));real(*args,**kw)
   with patch.object(ep,'indexer_api',side_effect=indexer),patch.object(ep,'sonarr_search',return_value=[]),patch.object(ep,'normalize',side_effect=normalize),patch.object(ep,'report',side_effect=report):
    start=time.time();rows,_=ep.search(m,key,e)
   self.assertEqual(len(rows),2);self.assertTrue(any(n==1 and at-start<.45 for at,n in published))
 def test_failed_command_with_registered_file_is_success(self):
  with tempfile.TemporaryDirectory() as d:
   m=self.env(d);h='a'*40;key='b'*32
   ep.atomic(m.WORKER_STATE/'managed-downloads.json',{h:{'episodes':[{'job':key,'episode':{'id':1,'sonarrId':42},'state':'importing','command':12,'wanted':[0]}]}})
   m.trpc.return_value={'torrents':[{'hashString':h}]};m.api.return_value={'hasFile':True,'episodeFileId':2,'episodeFile':{'size':100}}
   ep.monitor(m);self.assertEqual(ep.result(m,key)['state'],'done');m.api.assert_called_once_with('episode/42')
 def test_upgrade_old_file_is_not_success(self):
  with tempfile.TemporaryDirectory() as d:
   m=self.env(d);h='a'*40;key='b'*32
   ep.atomic(m.WORKER_STATE/'managed-downloads.json',{h:{'episodes':[{'job':key,'episode':{'id':1,'sonarrId':42,'_upgradeFileId':2},'state':'importing','command':12,'importAttempts':3,'wanted':[0]}]}})
   m.trpc.return_value={'torrents':[{'hashString':h}]}
   m.api.side_effect=lambda path: {'hasFile':True,'episodeFileId':2,'episodeFile':{'size':100}} if path.startswith('episode/') else {'status':'failed'}
   ep.monitor(m);self.assertEqual(ep.result(m,key)['state'],'error')
 def test_upgrade_new_file_is_success(self):
  with tempfile.TemporaryDirectory() as d:
   m=self.env(d);h='a'*40;key='b'*32
   ep.atomic(m.WORKER_STATE/'managed-downloads.json',{h:{'episodes':[{'job':key,'episode':{'id':1,'sonarrId':42,'_upgradeFileId':2},'state':'importing','command':12,'importAttempts':3,'wanted':[0]}]}})
   m.trpc.return_value={'torrents':[{'hashString':h}]};m.api.return_value={'hasFile':True,'episodeFileId':3,'episodeFile':{'size':100}}
   ep.monitor(m);self.assertEqual(ep.result(m,key)['state'],'done');self.assertEqual(ep.read(m.WORKER_STATE/'managed-downloads.json',{})[h]['episodes'][0]['importedFileId'],3)
 def test_manual_import_does_not_attach_untracked_download_id(self):
  with tempfile.TemporaryDirectory() as d:
   m=self.env(d);h='a'*40;key='b'*32;path='/torrents/complete/tv/Panel/job/Show.S01E01.mkv'
   ep.atomic(m.WORKER_STATE/'managed-downloads.json',{h:{'episodes':[{'job':key,'episode':{'id':1,'sonarrId':42,'seriesId':21},'state':'downloading','wanted':[0],'video':0,'quality':{}}]}})
   m.trpc.return_value={'torrents':[{'hashString':h,'files':[{'length':100,'name':'Show.S01E01.mkv'}],'fileStats':[{'bytesCompleted':100}],'downloadDir':'/downloads/complete/tv/Panel/job'}]}
   m.api.side_effect=lambda p,*args:[{'path':path}] if p.startswith('manualimport?') else {'id':12}
   ep.monitor(m);body=m.api.call_args.args[2];self.assertEqual(body['files'][0]['episodeIds'],[42]);self.assertNotIn('downloadId',body['files'][0]);self.assertEqual(body['importMode'],'copy')
 def test_orphaned_import_command_is_retried(self):
  with tempfile.TemporaryDirectory() as d:
   m=self.env(d);h='a'*40;key='b'*32;path='/torrents/complete/tv/Panel/job/Show.S01E01.mkv'
   ep.atomic(m.WORKER_STATE/'managed-downloads.json',{h:{'episodes':[{'job':key,'episode':{'id':1,'sonarrId':42,'seriesId':21},'state':'importing','command':12,'importAttempts':3,'importAt':time.time()-3600,'wanted':[0],'video':0,'quality':{}}]}})
   m.trpc.return_value={'torrents':[{'hashString':h,'files':[{'length':100,'name':'Show.S01E01.mkv'}],'fileStats':[{'bytesCompleted':100}],'downloadDir':'/downloads/complete/tv/Panel/job'}]}
   def api(p,*args):
    if p=='episode/42':return {'hasFile':False,'episodeFileId':0}
    if p=='command/12':return {'status':'orphaned'}
    if p.startswith('manualimport?'):return [{'path':path,'quality':{},'languages':[{'id':11,'name':'Russian'}]}]
    if p=='command':return {'id':13}
    return {}
   m.api.side_effect=api
   ep.monitor(m);entry=ep.read(m.WORKER_STATE/'managed-downloads.json',{})[h]['episodes'][0]
   self.assertEqual(entry['command'],13);self.assertEqual(entry['importAttempts'],1);r=ep.result(m,key);self.assertEqual(r['state'],'importing');self.assertIn('Добавляем',r['message'])
if __name__=='__main__':unittest.main()
