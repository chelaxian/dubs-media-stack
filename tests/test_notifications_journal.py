"""Journal semantics offline tests: dedupe, retention, honesty, bounded state.

No network is used. Each test gets an isolated journal database inside its
own temporary directory; the offline role ('test') is a writer exactly like
production workers.
"""
import os,sqlite3,tempfile,unittest,json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import _dubs_env  # noqa: F401  (offline roots, src on sys.path)
import notifications as n
import security as s

class JournalTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  self.old_settings=n.SETTINGS_PATH
  n.SETTINGS_PATH=self.root/'notification-settings.json'
 def fresh(self):
  "Isolated journal DB: every test owns its directory, nothing is reused."
  return patch.object(n,'DB',self.root/'notifications.sqlite3')
 def tearDown(self):
  n.SETTINGS_PATH=self.old_settings;self.tmp.cleanup()

 def test_dedupe_key_journaled_once_across_restarts(self):
  with self.fresh():
   self.assertTrue(n.emit('download_started','t','m',1,2,'download_started:5:job'))
   self.assertFalse(n.emit('download_started','t','m',1,2,'download_started:5:job'))
   # A restarted worker re-opens the same file: dedupe survives.
   self.assertFalse(n.emit('download_started','t','m',1,2,'download_started:5:job'))
   self.assertEqual(n.tail(),1)

 def test_re_download_is_new_generation_event(self):
  with self.fresh():
   self.assertTrue(n.emit('download_started','t','m',5,2,'download_started:5:jobA'))
   self.assertTrue(n.emit('download_started','t','m',5,2,'download_started:5:jobB'))
   self.assertEqual(n.tail(),2)

 def test_missing_dedupe_always_journaled(self):
  with self.fresh():
   self.assertTrue(n.emit('new_episode','t','m'))
   self.assertTrue(n.emit('new_episode','t','m'))
   self.assertEqual(n.tail(),2)

 def test_retention_bounded(self):
  with self.fresh(),patch.object(n,'RETENTION',50):
   for i in range(60):n.emit('new_episode','t%d'%i,'m',dedupe='e%d'%i)
   self.assertEqual(n.tail(),60)   # the cursor stays at the newest id...
   ids=[x['id'] for x in n.events_after(0,100)]
   self.assertEqual(len(ids),50)   # ...while only the newest RETENTION rows remain
   self.assertEqual(ids,list(range(ids[0],ids[0]+50)))

 def test_payloads_sanitized_and_bounded(self):
  with self.fresh():
   n.emit('new_episode','Заголовок\nс\tпереводами '+'x'*400,
    'Смотрите на http://192.168.1.5:8096 или magnet:?xt=1 путь /mnt/shows/secret path \x01\x02',7,3,'s1')
   row=n.events_after(0,1)[0]
   self.assertLessEqual(len(row['title']),n.TITLE_MAX)
   self.assertLessEqual(len(row['message']),n.MESSAGE_MAX)
   for secret in ('http','magnet','192.168','/mnt','shows'):
    self.assertNotIn(secret,row['message'])
   for ch in row['title']+row['message']:
    self.assertGreaterEqual(ord(ch),32)   # control characters never survive
   self.assertEqual(row['episodeId'],7);self.assertEqual(row['seriesId'],3)

 def test_boolean_ids_rejected_strict(self):
  with self.fresh():
   n.emit('new_episode','t','m',True,True,'bools')
   row=n.events_after(0,1)[0]
   self.assertIsNone(row['episodeId']);self.assertIsNone(row['seriesId'])

 def test_unknown_type_and_reader_role_refused(self):
  with self.fresh():
   self.assertFalse(n.emit('nope','t','m',dedupe='x'))
   with patch.dict(os.environ,{'SERIES_CONTROL_ROLE':'web'}):
    self.assertFalse(n.emit('new_episode','t','m',dedupe='y'))
   self.assertFalse(n.DB.exists())   # the web role must not even create the file

 def test_reader_never_creates_or_writes(self):
  with self.fresh():
   self.assertEqual(n.events_after(0,10),[]);self.assertEqual(n.history_page(5,10)['events'],[])
   self.assertEqual(n.tail(),0)
   self.assertFalse(n.DB.exists())
   with patch.object(n,'_writer',return_value=True):
    n.emit('new_episode','t','m',dedupe='z')
    self.assertEqual(os.stat(n.DB).st_mode&0o777,0o640)   # private, group-readable only
   self.assertEqual([x['id'] for x in n.events_after(0,10)],[1])
   self.assertEqual([x['id'] for x in n.history_page(2,10)['events']],[1])
   self.assertEqual(n.history_page(2,10)['nextBefore'],1)
   self.assertEqual(n.history_page(1,10)['events'],[])   # page before the oldest id
   self.assertIsNone(n.history_page(1,10)['nextBefore'])

 def test_journal_init_idempotent_epoch_stable(self):
  with self.fresh():
   self.assertIsNone(n.instance_id())   # honest degradation before init
   n.journal_init();first=n.instance_id()
   self.assertTrue(first)
   n.journal_init()                      # second startup keeps the epoch
   self.assertEqual(n.instance_id(),first)
   os.remove(n.DB)                       # a restored/recreated journal is a new epoch
   n.journal_init()
   self.assertNotEqual(n.instance_id(),first)

 def test_emit_failure_honest_not_fatal(self):
  with self.fresh(),patch.object(n,'_connect',side_effect=sqlite3.OperationalError('disk')):
   self.assertFalse(n.emit('new_episode','t','m',dedupe='f'))   # never raises

 def test_journal_read_failure_returns_empty(self):
  with self.fresh(),patch.object(n,'_connect',side_effect=sqlite3.DatabaseError('corrupt')):
   self.assertEqual(n.events_after(0,10),[]);self.assertEqual(n.tail(),0)

 def test_device_tokens_hashed_bounded_expiring_revocable(self):
  d1,t1=s.register_device('owner-a','android');d2,t2=s.register_device('owner-a','ios')
  self.assertEqual(len(t1),43);self.assertEqual(len(d1),32)
  self.assertIsNotNone(s.device_token_auth(t1))
  with s.connect() as c:
   stored=c.execute('SELECT token_hash FROM devices WHERE device_id=?',(d1,)).fetchone()[0]
  self.assertNotIn(t1,stored)   # hashed at rest, never the raw token
  with patch.object(s,'DEVICE_TTL',-10):
   _,t3=s.register_device('owner-a','android');self.assertIsNone(s.device_token_auth(t3))   # expired
  self.assertTrue(s.revoke_device('owner-a',d1));self.assertIsNone(s.device_token_auth(t1))
  self.assertFalse(s.revoke_device('owner-b',d2))            # only the owner revokes
  self.assertFalse(s.device_token_auth(t1+'x'));self.assertFalse(s.device_token_auth(None))
  with patch.object(s,'DEVICE_MAX',1):
   s.register_device('owner-a','android_tv');s.register_device('owner-a','android_tv')
   rows=s.device_rows('owner-a')
   self.assertLessEqual(len(rows),1)
   for row in rows:self.assertEqual(set(row),{'deviceId','platform','lastSeen'})   # never tokens

 def test_settings_defaults_roundtrip_and_corruption(self):
  self.assertEqual(n.get_settings()['enabled'],True)
  self.assertEqual(set(n.get_settings()['types']),set(n.EVENT_TYPES))
  out=n.save_settings(False,['new_episode','bogus','torrent_complete','new_episode'],True)
  self.assertEqual(out,{'enabled':False,'types':['new_episode','torrent_complete'],'showTitles':True})
  self.assertEqual(os.stat(n.SETTINGS_PATH).st_mode&0o777,0o640)
  n.SETTINGS_PATH.write_text('{broken')
  self.assertEqual(n.get_settings()['enabled'],True)   # corrupt file -> safe defaults
  n.SETTINGS_PATH.write_text('x'*9000)
  self.assertEqual(set(n.get_settings()['types']),set(n.EVENT_TYPES))
  n.save_settings(True,[],False)
  self.assertEqual(n.get_settings()['types'],[])   # explicit empty allow-list is honored

 def test_daily_outcome_classification_honest(self):
  with self.fresh():
   self.assertTrue(n.upgrade_started('2026-11-04'))
   self.assertFalse(n.upgrade_started('2026-11-04'))   # once per local date
   n.upgrade_result('2026-11-04',2,1,4)
   n.upgrade_result('2026-11-05',0,0,3)
   n.upgrade_result('2026-11-06',1,0,3)
   results=[x for x in n.events_after(0,100) if x['type']=='upgrade_result']   # ascending day order
   self.assertEqual(len(results),3)
   self.assertIn('ошиб',results[0]['title'].lower())
   self.assertNotIn('пока нет',results[0]['title']+results[0]['message'])   # an error day is never 'no improvements'
   self.assertIn('пока нет',results[1]['title'])
   self.assertIn('Загрузка началась',results[2]['message'])
   started=[x for x in n.events_after(0,100) if x['type']=='upgrade_started']
   self.assertEqual(len(started),1)

 def test_episode_label_bounds_and_no_quality(self):
  label=n.episode_label({'season':2,'number':13,'title':'Название'})
  self.assertEqual(label,'S02E13 · Название')
  self.assertEqual(n.episode_label({}),'S00E00')
  self.assertEqual(n.episode_label({'season':True,'number':1,'title':'x'}),'S00E01 · x')
  self.assertLessEqual(len(n.episode_label({'season':1,'number':1,'title':'y'*999})),n.MESSAGE_MAX)

 def test_new_episode_baseline_advances_beyond_journal_retention(self):
  "Replay after the journal lost its dedupe rows must stay silent."
  import episodes as ep
  m=SimpleNamespace(WORKER_STATE=self.root)
  item={'id':77,'showId':9,'seriesId':4,'seriesTitle':'Шоу','season':1,'number':1,'title':'Серия','airDate':'2026-11-01T00:00:00Z'}
  with self.fresh():
   n.new_episode_pass(m,[dict(item)])            # first sighting: baseline, silent
   self.assertEqual(n.tail(),0)
   n.new_episode_pass(m,[dict(item)])            # same air date: no spam
   self.assertEqual(n.tail(),0)
   newer=dict(item,airDate='2026-11-03T00:00:00Z')
   n.new_episode_pass(m,[newer])
   self.assertEqual(n.tail(),1)
   # Journal wiped (retention/manual): the durable baseline still suppresses.
   c=sqlite3.connect(n.DB);c.execute('DELETE FROM events');c.commit();c.close()
   n.new_episode_pass(m,[newer])
   self.assertEqual(n.tail(),0)

 def test_local_media_invalidation_bounded_and_expiring(self):
  import time as _t
  item={'seriesId':4,'season':2,'number':13}
  n.invalidate_local_media([item])
  self.assertIn('sonarr:4:2:13',n.stale_local_media())
  with patch.object(n,'INVALID_TTL',-1):
   n.invalidate_local_media([{'seriesId':5,'season':1,'number':1}])
  self.assertEqual(n.stale_local_media(),{'sonarr:4:2:13'})   # expired entry healed
  with self.fresh(),patch.object(n,'INVALID_MAX',2):
   for number in range(5):n.invalidate_local_media([{'seriesId':9,'season':1,'number':number}])
   self.assertLessEqual(len(n.stale_local_media()),2)
  n.invalidate_local_media([{'seriesId':'x',None:None},None,'text'])   # hostile shapes never raise

if __name__=='__main__':unittest.main()
