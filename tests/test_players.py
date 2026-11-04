import sys,types,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import _dubs_env  # offline roots; src/automations on sys.path
import players,episodes
class Tests(unittest.TestCase):
 def test_links_exact_ids_no_credentials(self):
  with tempfile.TemporaryDirectory() as d:
   m=types.SimpleNamespace(episodes=episodes,WORKER_STATE=Path(d),api=Mock(return_value={'tmdbId':60625}))
   episodes.atomic(m.WORKER_STATE/'myshows-episodes.json',{'episodes':{'1':{'seriesId':21,'season':9,'number':2,'jellyfinUrl':'https://jellyfin.example.org/web/#/details?id='+'a'*32+'&serverId='+'b'*32}}})
   with patch.object(players.rt,'settings',return_value={'jellyfin_url':'https://jellyfin.example.org'}):
    r=players.links(m,1,'c'*32)
    m.api.side_effect=RuntimeError('Sonarr offline');offline=players.links(m,1,'c'*32)
   self.assertEqual(r['infuse'],'infuse://series/60625-9-2');self.assertIn('swiftfin',offline);self.assertNotIn('infuse',offline);self.assertIn('id='+'a'*32,r['streamyfin']);self.assertEqual(r['findroid'],'jellyfin://item/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa');self.assertTrue(all('apikey' not in v.lower() and 'token=' not in v.lower() for v in r.values()))
 def test_unknown_and_foreign_host_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   m=types.SimpleNamespace(episodes=episodes,WORKER_STATE=Path(d),api=Mock())
   with self.assertRaises(ValueError):players.links(m,1,'c'*32)
   episodes.atomic(m.WORKER_STATE/'myshows-episodes.json',{'episodes':{'1':{'jellyfinUrl':'https://evil.test/web/#/details?id='+'a'*32}}})
   with self.assertRaises(ValueError):players.links(m,1,'c'*32)
   m.api.assert_not_called()
if __name__=='__main__':unittest.main()
