import sys,tempfile,threading,types,unittest
from pathlib import Path
from unittest.mock import patch
import _dubs_env  # offline roots; src/automations on sys.path
import episodes as ep
class Bulk(unittest.TestCase):
 def test_all_unwatched_queue_deduplicated_and_filtered(self):
  with tempfile.TemporaryDirectory() as root:
   m=types.SimpleNamespace(STATE=Path(root)/'web',WORKER_STATE=Path(root)/'worker',LOCK=threading.RLock())
   (m.STATE/'requests').mkdir(parents=True);(m.WORKER_STATE/'jobs').mkdir(parents=True)
   eps=[{'id':i} for i in range(1,30)]+[{'id':30,'hasFile':True},{'id':31,'future':True}]
   ep.atomic(m.STATE/'requests'/('a'*32+'.json'),{'op':'download','episode':1,'created':1})
   with patch.object(ep,'episode',return_value={'seriesId':8}) as auth,patch.object(ep,'snapshot',return_value={'released':[{'seriesId':8,'episodes':eps},{'seriesId':9,'episodes':[{'id':99}]}]}):
    result=ep.submit_all(m,'1');self.assertEqual({x['episode'] for x in result['jobs']},set(range(2,30)));self.assertEqual(result['skipped'],3);auth.assert_called_with(m,1)
    self.assertEqual(ep.submit_all(m,'1')['jobs'],[])
   self.assertEqual(len(list((m.STATE/'requests').glob('*.json'))),29)
 def test_cancel_all_only_active_in_selected_series(self):
  with tempfile.TemporaryDirectory() as root:
   m=types.SimpleNamespace(STATE=Path(root),LOCK=threading.RLock());(m.STATE/'requests').mkdir()
   view={'released':[{'seriesId':8,'episodes':[{'id':1},{'id':2},{'id':3}]},{'seriesId':9,'episodes':[{'id':4}]}],'actions':{'1':{'state':'running'},'2':{'state':'downloading'},'3':{'state':'done'},'4':{'state':'downloading'}}}
   with patch.object(ep,'episode',return_value={'seriesId':8}),patch.object(ep,'snapshot',return_value=view):
    result=ep.cancel_all(m,'1');self.assertEqual({x['episode'] for x in result['jobs']},{1,2})
   requests=[ep.read(p,{}) for p in (m.STATE/'requests').glob('*.json')];self.assertTrue(all(r['op']=='cancel' for r in requests))
 def test_selected_pack_only_covered_episodes(self):
  with tempfile.TemporaryDirectory() as root:
   m=types.SimpleNamespace(STATE=Path(root)/'web',WORKER_STATE=Path(root)/'worker',LOCK=threading.RLock())
   (m.STATE/'requests').mkdir(parents=True);(m.WORKER_STATE/'release-cache').mkdir(parents=True)
   import time
   key='b'*32;tag='c'*20
   ep.atomic(m.WORKER_STATE/'release-cache'/(key+'.json'),{'created':time.time(),'episode':{'seriesId':8,'bulkIds':{'1':1,'2':2}},'items':[{'tag':tag,'display':{'allowed':True},'row':{'episodeNumbers':[2]}}]})
   view={'released':[{'seriesId':8,'episodes':[{'id':1},{'id':2},{'id':3},{'id':4,'future':True}]}]}
   with patch.object(ep,'episode',return_value={'seriesId':8}),patch.object(ep,'snapshot',return_value=view):
    result=ep.submit_all(m,'1',key+':'+tag);self.assertEqual([x['episode'] for x in result['jobs']],[2])
   req=ep.read(next((m.STATE/'requests').glob('*.json')),{});self.assertNotIn('batch',req);self.assertEqual(req['selection'],key+':'+tag)
   with self.assertRaises(ep.UserError):ep.selected_bulk_ids(m,key+':'+tag,99)
 def test_watch_all_only_released_and_refresh_once(self):
  m=types.SimpleNamespace();calls=[]
  with patch.object(ep,'episode',side_effect=lambda m,id:{'id':id,'future':False}),patch.object(ep,'released_group',return_value={'episodes':[{'id':1},{'id':2},{'id':3,'future':True}]}),patch.object(ep,'v3',side_effect=lambda m,op,args:calls.append(args['id'])),patch.object(ep,'refresh') as refresh,patch.object(ep,'report') as report:
   ep.process(m,'a'*32,{'op':'watch_all','episode':1});self.assertEqual(calls,[1,2]);refresh.assert_called_once_with(m,True);self.assertEqual(report.call_args.args[2],'done')
 def test_invalid_anchor_rejected(self):
  with self.assertRaises(ValueError):ep.submit_all(None,'../1')
  with patch.object(ep,'episode',side_effect=ep.UserError('not watching')):
   with self.assertRaises(ep.UserError):ep.submit_all(None,'5')
 def test_manual_selection_can_replace_downloaded_episode(self):
  with tempfile.TemporaryDirectory() as root:
   m=types.SimpleNamespace(STATE=Path(root)/'web',WORKER_STATE=Path(root)/'worker',LOCK=threading.RLock())
   (m.STATE/'requests').mkdir(parents=True);(m.WORKER_STATE/'jobs').mkdir(parents=True)
   view={'released':[{'seriesId':8,'episodes':[{'id':1,'hasFile':True},{'id':2}]}]}
   with patch.object(ep,'episode',return_value={'seriesId':8}),patch.object(ep,'snapshot',return_value=view),patch.object(ep,'selected_bulk_ids',return_value={'1'}):
    result=ep.submit_all(m,'1','b'*32+':'+'c'*20)
   self.assertEqual([x['episode'] for x in result['jobs']],[1])
if __name__=='__main__':unittest.main()

class SeasonScope(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.m=types.SimpleNamespace(STATE=Path(self.tmp.name)/'web',WORKER_STATE=Path(self.tmp.name)/'worker',LOCK=threading.RLock())
  (self.m.STATE/'requests').mkdir(parents=True);(self.m.WORKER_STATE/'jobs').mkdir(parents=True)
  self.items=[{'id':1,'season':2,'number':1},{'id':2,'season':2,'number':2},{'id':3,'season':3,'number':1},{'id':4,'season':3,'number':2,'hasFile':True},{'id':5,'season':2,'number':3,'future':True}]
  self.view={'released':[{'seriesId':8,'episodes':self.items}],'actions':{'1':{'state':'running'},'2':{'state':'downloading'},'3':{'state':'running'}}}
 def test_queue_and_cancel_do_not_cross_seasons(self):
  with patch.object(ep,'episode',return_value={'seriesId':8,'season':2}),patch.object(ep,'snapshot',return_value=self.view):
   result=ep.submit_all(self.m,'1',season_only=True)
   self.assertEqual({x['episode'] for x in result['jobs']},{1,2})
   result=ep.cancel_all(self.m,'1',season_only=True)
   self.assertEqual({x['episode'] for x in result['jobs']},{1,2})
 def test_watch_season_only(self):
  calls=[]
  def episode(m,id):return {'id':id,'season':2 if id in (1,2,5) else 3,'future':False}
  with patch.object(ep,'episode',side_effect=episode),patch.object(ep,'released_group',return_value={'episodes':self.items}),patch.object(ep,'v3',side_effect=lambda m,op,args:calls.append(args['id'])),patch.object(ep,'refresh'),patch.object(ep,'report'):
   ep.process(self.m,'a'*32,{'op':'watch_season','episode':1})
  self.assertEqual(calls,[1,2])
 def test_search_only_requested_season(self):
  calls=[]
  def episode(m,id):return {'id':id,'season':2 if id in (1,2,5) else 3,'number':id,'future':False}
  def search(m,key,sample,publish):calls.append(sample);return [],[]
  with patch.object(ep,'episode',side_effect=episode),patch.object(ep,'released_group',return_value={'episodes':self.items}),patch.object(ep,'search',side_effect=search),patch.object(ep,'report'):
   ep.process(self.m,'a'*32,{'op':'search_season','episode':1})
  self.assertEqual(len(calls),1);self.assertEqual(set(calls[0]['bulkIds']),{'1','2'});self.assertTrue(calls[0]['seasonSearch'])
