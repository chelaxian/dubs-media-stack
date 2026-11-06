import importlib.util,sys,pathlib,tempfile,unittest,subprocess,os
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from installer import wizard
from unittest.mock import patch
class Wizard(unittest.TestCase):
 def test_private_choices_never_overwrite(self):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);media=root/'media disk';media.mkdir();p=root/'compose'/'.env'
   wizard.write_initial_env(p,str(media),str(root/'data'), '1000','1000','Europe/Moscow')
   self.assertEqual(p.stat().st_mode&0o777,0o600);self.assertIn('MEDIA_ROOT='+str(media),p.read_text());old=p.read_bytes()
   with self.assertRaises(FileExistsError):wizard.write_initial_env(p,str(media),str(root/'other'),'1000','1000','Europe/Moscow')
   self.assertEqual(old,p.read_bytes())
 def test_bad_paths_not_created(self):
  with tempfile.TemporaryDirectory() as d:
   p=pathlib.Path(d)/'.env'
   for media in ['relative','/tmp/a\nX=1','/tmp/$(id)']:
    with self.assertRaises(ValueError):wizard.write_initial_env(p,media,'/srv/dubs','1000','1000','Europe/Moscow')
   self.assertFalse(p.exists())
 def test_preflight_denies_hypervisor(self):
  with patch.object(wizard.Path,'exists',return_value=True):
   with self.assertRaisesRegex(ValueError,'Proxmox'):wizard.preflight()
 def test_no_tty_does_not_mutate(self):
  with patch.object(wizard,'preflight'),patch.object(wizard.sys.stdin,'isatty',return_value=False),patch.object(wizard,'write_initial_env') as write:
   self.assertEqual(wizard.main([]),2);write.assert_not_called()
if __name__=='__main__':unittest.main()
