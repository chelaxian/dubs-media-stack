import tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
import _dubs_env
import episodes as ep
import control

class ManualReplacement(unittest.TestCase):
 def test_backup_preserves_old_file_and_rejects_untrusted_path(self):
  with tempfile.TemporaryDirectory() as root:
   shows=Path(root)/'shows';torrents=Path(root)/'torrents';shows.mkdir();torrents.mkdir()
   (shows/'episode.mkv').write_bytes(b'original video')
   paths={'shows_host':str(shows),'torrent_host':str(torrents),'sonarr_tv':'/tv'}
   e={'sonarrId':4,'_upgradeFileId':9};current={'episodeFile':{'path':'/tv/episode.mkv'}}
   with patch.object(ep.rt,'media_paths',return_value=paths):
    ep.preserve_replaced_file(control,current,e)
    backups=list((torrents/'.dubs-replacement-backups').iterdir())
    self.assertEqual(len(backups),1);self.assertEqual(backups[0].read_bytes(),b'original video')
    with self.assertRaises(ep.UserError):ep.preserve_replaced_file(control,{'episodeFile':{'path':'/tv/../outside'}},e)
