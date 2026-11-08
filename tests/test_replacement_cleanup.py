import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import _dubs_env
import control as m
import episodes as ep

class ReplacementCleanup(unittest.TestCase):
 def test_cleanup_only_after_verified_replacement(self):
  with tempfile.TemporaryDirectory() as root:
   root=Path(root); shows=root/'shows'; torrents=root/'torrents'; shows.mkdir(); torrents.mkdir()
   (shows/'new.mkv').write_bytes(b'new video'); (torrents/'old.mkv').write_bytes(b'old video')
   paths={'shows_host':str(shows),'torrent_host':str(torrents),'sonarr_tv':'/tv'}
   with patch.object(ep.rt,'media_paths',return_value=paths),patch.object(m.rt,'media_paths',return_value=paths),patch.object(m,'disable_source') as disable:
    old=torrents/'old.mkv'; request={'replacementCopies':[{'path':str(old),'fingerprint':m.fingerprint(old),'source':True}]}; episode={'_upgradeFileId':1}
    ep.remove_replaced_sources(m,episode,request,{'hasFile':True,'episodeFileId':1})
    self.assertTrue(old.exists());disable.assert_not_called()
    ep.remove_replaced_sources(m,episode,request,{'hasFile':True,'episodeFileId':2,'episodeFile':{'path':'/tv/new.mkv','size':9}})
    self.assertFalse(old.exists());self.assertEqual(request['replacementCopies'],[]);disable.assert_called_once()
 def test_changed_source_is_kept(self):
  with tempfile.TemporaryDirectory() as root:
   root=Path(root); shows=root/'shows'; torrents=root/'torrents'; shows.mkdir(); torrents.mkdir()
   (shows/'new').write_bytes(b'new');old=torrents/'old';old.write_bytes(b'old')
   paths={'shows_host':str(shows),'torrent_host':str(torrents),'sonarr_tv':'/tv'}
   with patch.object(ep.rt,'media_paths',return_value=paths),patch.object(m.rt,'media_paths',return_value=paths),patch.object(m,'disable_source'):
    req={'replacementCopies':[{'path':str(old),'fingerprint':[0,0,1],'source':False}]}
    ep.remove_replaced_sources(m,{'_upgradeFileId':1},req,{'hasFile':True,'episodeFileId':2,'episodeFile':{'path':'/tv/new','size':3}})
    self.assertTrue(old.exists());self.assertEqual(len(req['replacementCopies']),1)
