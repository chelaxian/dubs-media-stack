import hashlib,types,unittest
from unittest.mock import Mock,patch
import _dubs_env
import tracker_merge as tm

class TrackerMergeTests(unittest.TestCase):
 def test_hashes_exact_info_bytes(self):
  raw=b'd4:infod4:name4:test6:lengthi10eee'
  self.assertEqual(tm.info_slice(raw),b'd4:name4:test6:lengthi10ee')
 def test_private_and_unmatched_torrents_never_mutated(self):
  m=types.SimpleNamespace(trpc=Mock());ep=types.SimpleNamespace()
  h='a'*40
  self.assertEqual(tm.merge(ep,m,{'hashString':h,'isPrivate':True},[{}]),0)
  with patch.object(tm,'source_metadata',return_value=('b'*40,False,['udp://tracker.opentrackr.org:1337/announce'])):
   self.assertEqual(tm.merge(ep,m,{'hashString':h,'isPrivate':False,'trackers':[]},[{}]),0)
  m.trpc.assert_not_called()
 def test_match_adds_once_and_rejects_unknown_private_tracker(self):
  m=types.SimpleNamespace(trpc=Mock());h='a'*40
  safe='udp://tracker.opentrackr.org:1337/announce';bad='http://127.0.0.1/announce'
  with patch.object(tm,'source_metadata',return_value=(h,False,[safe,safe,bad])):
   self.assertEqual(tm.merge(None,m,{'hashString':h,'isPrivate':False,'trackers':[],'status':4},[{}]),1)
  self.assertEqual(m.trpc.call_args_list[0].args[1]['trackerAdd'],[safe]);self.assertEqual(m.trpc.call_args_list[1].args[0],'torrent-reannounce')
 def test_source_marked_private_never_merged(self):
  m=types.SimpleNamespace(trpc=Mock());h='a'*40
  with patch.object(tm,'source_metadata',return_value=(h,True,['udp://tracker.opentrackr.org:1337/announce'])):
   self.assertEqual(tm.merge(None,m,{'hashString':h,'isPrivate':False,'trackers':[]},[{}]),0)
  m.trpc.assert_not_called()
if __name__=='__main__':unittest.main()
