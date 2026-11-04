import sys,types,unittest,threading
from unittest.mock import Mock,patch
from pathlib import Path
import _dubs_env  # offline roots; src/automations on sys.path
import episodes as ep
class Seasons(unittest.TestCase):
 def run_batch(self,missing=False,cancel_second=False):
  events=[];managed={};m=types.SimpleNamespace(STATE=Path('/fake/web'),WORKER_STATE=Path('/fake/worker'),api=Mock(return_value={'hasFile':False}),trpc=Mock(side_effect=lambda op,args:events.append((op,args))))
  members=[('a'*32,{'episode':1}),('b'*32,{'episode':2})]
  def episode(m,eid):return {'id':eid,'seriesId':4,'season':3,'number':eid,'sonarrId':eid,'future':False}
  item={'display':{'allowed':True,'qualityRank':9,'score':500,'seeders':20},'row':{'fullSeason':True,'downloadUrl':'proxy'}}
  def read(path,default):return {'items':[item]} if 'release-cache' in str(path) else managed
  def download(m,key,e,item,metainfo,start):
   self.assertFalse(start);self.assertEqual(metainfo,b'torrent');events.append(('select',e['id']))
   if missing and e['id']==2:raise ep.UserError('Missing file')
   managed.setdefault('hash',{'wanted':[],'episodes':[]});managed['hash']['wanted'].append(e['id']);managed['hash']['episodes'].append({'job':key,'state':'downloading'});return {'state':'downloading','message':'ok','hash':'hash'}
  def check(m,key,eid):
   if cancel_second and eid==2:raise ep.UserError('Заказ отменён')
  with patch.object(ep,'episode',side_effect=episode),patch.object(ep,'check_cancelled',side_effect=check),patch.object(ep,'search') as search,patch.object(ep,'read',side_effect=read),patch.object(ep,'report') as report,patch.object(ep,'safe_download',return_value=(b'torrent',None)) as fetch,patch.object(ep,'request_download',side_effect=download),patch.object(ep,'schedule_change'),patch.object(ep,'process') as fallback:
   ep.process_batch(m,members)
   self.assertEqual(search.call_count,1);self.assertTrue(search.call_args.args[2]['seasonSearch']);fetch.assert_called_once()
   if missing:self.assertEqual(fallback.call_args.args[1],'b'*32)
   else:fallback.assert_not_called()
  return events
 def test_one_search_one_torrent_select_all_before_start(self):
  events=self.run_batch();self.assertEqual(events[:2],[('select',1),('select',2)]);self.assertEqual(events[2],('torrent-start',{'ids':['hash']}))
 def test_missing_episode_only_uses_individual_fallback(self):self.run_batch(missing=True)
 def test_cancel_one_preserves_other(self):self.assertEqual(self.run_batch(cancel_second=True),[('select',1),('torrent-start',{'ids':['hash']})])
if __name__=='__main__':unittest.main()
