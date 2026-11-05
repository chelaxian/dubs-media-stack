"""Latest configured releases are shared by rendering and download authorization."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import _dubs_env
import runtime


class MobileArtifacts(unittest.TestCase):
 def test_real_dubs_tv_releases_keep_only_latest(self):
  names=['Dubs-TV-1.0.apk','Dubs-TV-1.4.apk','Dubs-TV-1.5.1.apk','Dubs-1.4.apk','DubsTV-1.0.apk']
  with tempfile.TemporaryDirectory() as directory:
   for name in names:(Path(directory)/name).touch()
   with patch.object(runtime,'settings',return_value={'mobile':{'dir':directory,'artifacts':[{'file':n} for n in names]}}):
    self.assertEqual([x['file'] for x in runtime.mobile_artifacts()],names[2:])
  self.assertIsNone(runtime._mobile_release('prefix-Dubs-TV-1.5.1.apk'))
  self.assertIsNone(runtime._mobile_release('Dubs-TV-1.5.1.apk.bak'))

 def test_numeric_versions_platforms_variants_and_config_order(self):
  names = ['Dubs-1.9.apk', 'Dubs-1.10.apk', 'Dubs-1.9-TrollStore.ipa',
           'Dubs-1.10-TrollStore.ipa', 'DubsTV-1.2.apk', 'DubsTV-1.1.apk',
           'Swiftfin-1.5.ipa', 'Swiftfin-1.5.1.ipa', 'Findroid-Dubs-1.0-arm64.apk',
           'Findroid-Dubs-1.1-arm64.apk', 'Findroid-Dubs-TV-1.0-arm64.apk',
           'Findroid-Dubs-TV-1.2-arm64.apk', 'Findroid-Dubs-1.0-x86_64.apk',
           'Dubs-1.0.ipa', 'Other-1.0.apk', 'Other-2.0.apk', 'SHA256SUMS',
           'prefix-Dubs-0.1.apk', 'Dubs-1.0.apk.bak']
  expected = [names[i] for i in [1,3,4,7,9,11,12,13,14,15,16,17,18]]
  with tempfile.TemporaryDirectory() as directory:
   root = Path(directory)
   for name in names:(root/name).touch()
   config = {'mobile': {'dir':directory, 'artifacts':[{'file':n,'label':'Label '+n} for n in names]}}
   with patch.object(runtime, 'settings', return_value=config):
    self.assertEqual(runtime.mobile_artifacts(), [{'file':n,'label':'Label '+n} for n in expected])
    # Future releases require only configuration and an actual regular file.
    config['mobile']['artifacts'].insert(0, {'file':'Dubs-2.0.apk'})
    self.assertEqual([x['file'] for x in runtime.mobile_artifacts()], expected)
    (root/'Dubs-2.0.apk').touch()
    self.assertEqual([x['file'] for x in runtime.mobile_artifacts()], ['Dubs-2.0.apk']+[n for n in expected if n!='Dubs-1.10.apk'])
    self.assertTrue((root/'Dubs-1.9.apk').exists())

 def test_equivalent_numeric_versions_and_direct_tv_families(self):
  self.assertEqual(runtime._mobile_release('Swiftfin-1.5.0.ipa'),runtime._mobile_release('Swiftfin-1.5.ipa'))
  for client in ['Findroid','FindroidTV']:
   family,older=runtime._mobile_release(client+'-1.9.apk')
   same_family,newer=runtime._mobile_release(client+'-1.10.apk')
   self.assertEqual(family,same_family)
   self.assertGreater(newer,older)
  self.assertNotEqual(runtime._mobile_release('Findroid-1.0.apk')[0],runtime._mobile_release('FindroidTV-1.0.apk')[0])

 def test_unsafe_and_missing_latest_cannot_hide_safe_release(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);(root/'Dubs-1.0.apk').touch();(root/'directory.apk').mkdir()
   (root/'Dubs-9.0.apk').symlink_to(root/'Dubs-1.0.apk')
   names=['Dubs-1.0.apk','Dubs-9.0.apk','Dubs-10.0.apk','../outside.apk',
          '/outside.apk','nested/file.apk','nested\\file.apk','.hidden.apk','directory.apk']
   with patch.object(runtime,'settings',return_value={'mobile':{'dir':directory,'artifacts':[{'file':n} for n in names]}}):
    self.assertEqual(runtime.mobile_artifacts(),[{'file':'Dubs-1.0.apk','label':'Dubs-1.0.apk'}])

if __name__=='__main__':unittest.main()
