import importlib.util,unittest
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'automations'/'torrent_telegram_report.py'
s=importlib.util.spec_from_file_location('torrent_telegram_report',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class ReportTest(unittest.TestCase):
 def test_waiting_is_hypothesis_not_asserted_block(self):
  d={'onepieceReady':True,'monkeys':{'status':4,'downloadedEver':0,'seeds':0,'peersConnected':0,'peersSendingToUs':0}}
  self.assertEqual(m.category(d['monkeys']),'waiting');self.assertIn('предположение',m.message(d));self.assertIn('импортирован',m.message(d))
 def test_paused_and_missing_are_distinct(self):
  self.assertEqual(m.category(None),'missing');self.assertEqual(m.category({'status':0,'downloadedEver':0}),'paused')
 def test_progress_requires_observed_bytes(self):
  self.assertEqual(m.category({'status':4,'downloadedEver':100}),'progress')
if __name__=='__main__':unittest.main()
