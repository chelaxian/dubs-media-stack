"""Durable notification journal, account settings and a bounded download observer.

The journal is a small SQLite database in WORKER_STATE. Only worker roles
write it; the web role reads it through a read-only connection (the database
uses the rollback journal, never WAL, so readers need no write access to the
directory). The journal owns the epoch instance identity: a recreated or
restored journal gets a fresh epoch, which is how native clients detect a
reset cursor instead of waiting forever on a stale one.

Events carry sanitized display strings built from whitelisted trusted
metadata fields — URLs, private addresses, filesystem paths and raw
exception text are redacted before anything is stored.

Nothing in this module mutates downloads, watched state or files: the
observer only issues Sonarr GETs, notification generation only touches
observation/metadata state, and every failure is logged with the exception
TYPE only and swallowed so media operations never break. Concurrent writers
use disjoint state files (observer / new-episode baseline / invalidation),
so no cross-module locking is needed; the invalidation file uses a small
lock because two worker threads may append to it.
"""
import json,logging,os,re,secrets,sqlite3,threading,time,urllib.parse
from pathlib import Path
import runtime as rt

EVENT_TYPES=('download_started','torrent_complete','library_ready','new_episode','upgrade_started','upgrade_result')
WRITER_ROLES={'worker','operations','test'}  # 'test' keeps the offline suite self-contained
TITLE_MAX=120;MESSAGE_MAX=300;RETENTION=2000
RECORDS_MAX=200;RECORD_TTL=14*86400;CONFIRM_BUDGET=10;INVALID_TTL=900;INVALID_MAX=500
DB=rt.worker_root()/'notifications.sqlite3'
SETTINGS_PATH=rt.state_root()/'notification-settings.json'
OBSERVER_STATE='notifications-observer.json'   # download observer only
BASELINE_STATE='notifications-baseline.json'   # new-episode baseline only (disjoint file)
INVALID_STATE='local-media-invalid.json'       # written from two worker threads -> locked
_INVALID_LOCK=threading.Lock()
_OBSERVER_LOCK=threading.Lock();_BASELINE_LOCK=threading.Lock()

_REDACT='[удалено]'
_URL_RE=re.compile(r'(?i)(?:https?|ftps?|ftp)://\S+|magnet:\?\S+|www\.\S+')
_PATH_RE=re.compile(r'(?:(?:[A-Za-z]:)?(?:/|\\)[^\s"\'<>|]+(?:/|\\)[^\s"\'<>|]+|(?:[A-Za-z]:\\)[^\s"\'<>|]+|\\\\[^\s"\'<>|]+(?:\\[^\s"\'<>|]+)+)')
_IP_RE=re.compile(r'(?<![0-9.])(?:\d{1,3}\.){3}\d{1,3}(?![0-9.])|(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![0-9A-Fa-f:])')

def _writer():
 return os.environ.get('SERIES_CONTROL_ROLE','web') in WRITER_ROLES

def _connect(create):
 """Rollback-journal SQLite: the web role opens the same file with mode=ro
 and never needs to write the directory (unlike WAL, which requires -shm
 access for readers)."""
 if not create:
  if not DB.exists():return None
  return sqlite3.connect('file:'+urllib.parse.quote(str(DB),safe='/')+'?mode=ro',uri=True,timeout=5)
 c=sqlite3.connect(DB,timeout=5)
 c.execute('PRAGMA busy_timeout=5000');c.execute('PRAGMA synchronous=FULL')
 try:os.chmod(DB,0o640)   # worker-owned, shared-group readable (setgid worker root)
 except OSError:pass
 return c

def _init(c):
 c.execute('CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,created REAL NOT NULL,'
           'type TEXT NOT NULL,title TEXT NOT NULL,message TEXT NOT NULL,episode_id INTEGER,series_id INTEGER,'
           'dedupe TEXT UNIQUE)')
 c.execute('CREATE INDEX IF NOT EXISTS events_created ON events(created)')
 c.execute('CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)')
 c.execute('INSERT OR IGNORE INTO meta(key,value) VALUES(?,?)',('instance',secrets.token_hex(16)))

def journal_init():
 """Idempotent startup initialization for worker roles; never requires any
 upstream authentication, so the epoch/cursor exist before MyShows does."""
 if not _writer():return
 try:
  c=_connect(create=True)
  try:_init(c);c.commit()
  finally:c.close()
 except (sqlite3.Error,OSError) as exc:
  logging.warning('Notification journal init failed: %s',type(exc).__name__)

def instance_id():
 """Journal epoch identity for native clients; None while the journal is
 not initialized yet (the web role degrades honestly and never writes the
 worker state directory)."""
 try:
  c=_connect(create=False)
  if c is None:return None
  try:
   row=c.execute("SELECT value FROM meta WHERE key='instance'").fetchone()
   return row[0] if row else None
  finally:c.close()
 except sqlite3.Error as exc:
  logging.warning('Notification journal read failed: %s',type(exc).__name__);return None

def _id(value):
 """Strict nullable int ids: booleans are not ids."""
 return value if isinstance(value,int) and not isinstance(value,bool) else None

def _clean(value,limit):
 """Whitelist-by-construction display text: metadata strings only, with
 embedded URLs, addresses and filesystem paths redacted before storage."""
 if not isinstance(value,str):value='' if value is None else str(value)
 value=_URL_RE.sub(_REDACT,value)
 value=_PATH_RE.sub(_REDACT,value)
 value=_IP_RE.sub(_REDACT,value)
 value=re.sub(r'[\x00-\x1f\x7f]+',' ',value)
 return re.sub(r'\s+',' ',value).strip()[:limit]

def episode_label(item):
 """Bounded 'S01E02 · title' display label from trusted metadata; no
 release quality, source name or any guessed payload."""
 season=item.get('season');number=item.get('number')
 label='S%02dE%02d'%(_id(season) or 0,_id(number) or 0)
 parts=[label]
 if item.get('title'):parts.append(_clean(item['title'],80))
 return ' · '.join(parts)[:MESSAGE_MAX]

def emit_episode(kind,item,generation):
 """Guard argument construction as well as journal I/O from media operations."""
 try:
  labels={'download_started':'Загрузка началась: ','torrent_complete':'Загрузка завершена: ','library_ready':'Серия в медиатеке: '}
  return emit(kind,labels[kind]+_clean(item.get('seriesTitle'),80),episode_label(item),
              item.get('id'),item.get('seriesId'),'%s:%s:%s'%(kind,item.get('id'),generation))
 except Exception as exc:
  logging.warning('Notification formatting failed: %s',type(exc).__name__);return False

def emit(kind,title,message,episode_id=None,series_id=None,dedupe=None):
 """Append one event; never raises and never breaks media operations.

 Returns True only when a new row was journaled. Expected duplicate dedupe
 keys are ignored cleanly (False, no error log); failures are logged with
 the exception TYPE only. Journal dedupe is the second safety layer: the
 durable observer/baseline state must prevent replays on its own because
 retention prunes old dedupe rows."""
 try:
  if kind not in EVENT_TYPES or not _writer():return False
  c=_connect(create=True)
  try:
   _init(c)
   cur=c.execute('INSERT OR IGNORE INTO events(created,type,title,message,episode_id,series_id,dedupe) VALUES(?,?,?,?,?,?,?)',
                 (time.time(),kind,_clean(title,TITLE_MAX),_clean(message,MESSAGE_MAX),_id(episode_id),_id(series_id),dedupe))
   # Retention: keep the newest RETENTION events (ids are monotonic).
   c.execute('DELETE FROM events WHERE id<=(SELECT id FROM events ORDER BY id DESC LIMIT 1 OFFSET ?)',(RETENTION,))
   c.commit()
   return cur.rowcount==1
  finally:c.close()
 except Exception as exc:
  logging.warning('Notification journal write failed: %s',type(exc).__name__);return False

def _row(row):
 return {'id':int(row[0]),'type':row[1],'created':float(row[2]),'title':row[3],'message':row[4],
         'episodeId':row[5],'seriesId':row[6]}

_COLUMNS='id,type,created,title,message,episode_id,series_id'

def events_after(after,limit):
 """Ascending events with id>after (device feed page)."""
 try:
  c=_connect(create=False)
  if c is None:return []
  try:return [_row(r) for r in c.execute('SELECT '+_COLUMNS+' FROM events WHERE id>? ORDER BY id LIMIT ?',(after,limit))]
  finally:c.close()
 except sqlite3.Error as exc:
  logging.warning('Notification journal read failed: %s',type(exc).__name__);return []

def events_before(before,limit):
 """Newest-first events with id<before (journal history page)."""
 try:
  c=_connect(create=False)
  if c is None:return []
  try:return [_row(r) for r in c.execute('SELECT '+_COLUMNS+' FROM events WHERE id<? ORDER BY id DESC LIMIT ?',(before,limit))]
  finally:c.close()
 except sqlite3.Error as exc:
  logging.warning('Notification journal read failed: %s',type(exc).__name__);return []

def tail():
 """Global latest event id (0 when the journal is empty/absent)."""
 try:
  c=_connect(create=False)
  if c is None:return 0
  try:return int(c.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0])
  finally:c.close()
 except sqlite3.Error as exc:
  logging.warning('Notification journal read failed: %s',type(exc).__name__);return 0

def events_page(after,limit):
 latest=tail()
 if after>latest:return {'events':[],'cursor':latest,'hasMore':False,'reset':True}
 rows=events_after(after,limit);cursor=rows[-1]['id'] if rows else after
 return {'events':rows,'cursor':cursor,'hasMore':tail()>cursor}

def history_page(before,limit):
 rows=events_before(before,limit)
 return {'events':rows,'cursor':tail(),'nextBefore':rows[-1]['id'] if rows else None}

# ---------------------------------------------------------------- settings
def _valid_types(value):
 if not isinstance(value,list):return list(EVENT_TYPES)
 out=[]
 for item in value:
  if isinstance(item,str) and item in EVENT_TYPES and len(item)<=64 and item not in out:out.append(item)
 return out

def get_settings():
 try:
  with open(SETTINGS_PATH,'rb') as f:
   raw=json.loads(f.read(4096).decode('utf-8'))
  if not isinstance(raw,dict):raise ValueError('settings')
 except (OSError,ValueError,UnicodeError):raw={}
 return {'enabled':bool(raw.get('enabled',True)),'types':_valid_types(raw.get('types')) or [],
         'showTitles':bool(raw.get('showTitles',False))}

def _atomic_json(path,data,mode):
 tmp=path.parent/('.'+path.name+'.'+secrets.token_hex(8))
 fd=os.open(tmp,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,mode);os.fchmod(fd,mode)
 try:
  with os.fdopen(fd,'w') as f:json.dump(data,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
  os.replace(tmp,path)
 finally:tmp.unlink(missing_ok=True)

def save_settings(enabled,types,show_titles):
 """Write exact, bounded account settings; returns the normalized value."""
 data={'enabled':bool(enabled),'types':_valid_types(types)[:len(EVENT_TYPES)],'showTitles':bool(show_titles)}
 _atomic_json(SETTINGS_PATH,data,0o640)
 return get_settings()

# ------------------------------------------------------- small state files
def _read_state(m,name):
 import episodes
 return episodes.read(m.WORKER_STATE/name,{})

def _save_state(m,name,state):
 import episodes
 episodes.atomic(m.WORKER_STATE/name,state,0o640)

def datetime_stamp(raw):
 import datetime as dt
 return dt.datetime.fromisoformat(str(raw).replace('Z','+00:00')).timestamp()

def _air(item):
 try:return datetime_stamp(item.get('airDate'))
 except (ValueError,TypeError,AttributeError):return None

# ------------------------------------------------------------ new episodes
def new_episode_pass(m,released):
 """Journal newly aired tracked episodes; the FIRST pass only records the
 per-show baseline so neither the initial sync nor a newly added old series
 can emit historical spam. The per-show baseline ADVANCES with each handled
 air date and lives in its own state file, so replay never depends on the
 retained journal. Unknown air dates never invent events."""
 try:
  with _BASELINE_LOCK:_new_episodes(m,released)
 except Exception as exc:logging.warning('New episode scan failed: %s',type(exc).__name__)

def _new_episodes(m,released):
 state=_read_state(m,BASELINE_STATE);shows=state.setdefault('shows',{});now=time.time()
 grouped={}
 for item in released or []:
  if not isinstance(item,dict):continue
  air=_air(item)
  if air is None:continue
  grouped.setdefault(str(item.get('showId')),[]).append((air,item))
 for show,items in grouped.items():
  previous=shows.get(show)
  items=[pair for pair in items if pair[0]<=now]
  for air,item in sorted(items,key=lambda pair:pair[0]):
   if previous is not None and air>previous:
    emit('new_episode','Новая серия: '+_clean(item.get('seriesTitle'),80),episode_label(item),
         item.get('id'),item.get('seriesId'),'new_episode:%s'%item.get('id'))
  shows[show]=max([previous or 0]+[air for air,_ in items])
 state['newSync']=now
 _save_state(m,BASELINE_STATE,state)

# ------------------------------------------------------- Sonarr queue observer
def observe_sonarr(m):
 """Bounded, read-only Sonarr queue watcher for downloads the panel did not
 start itself (Sonarr automatic search / lists / Jellyseerr).

 Journals download_started (only once OBSERVED transfer progress exists on
 the tracked files), torrent_complete (the tracked episode's portion, never
 a whole season pack) and library_ready (only a real nonempty registered
 file). The first pass is a baseline: ongoing and completed queue entries
 are recorded silently and are never newly announced later — including
 their transfer start. A download that reappears after leaving the queue
 (e.g. re-grabbed after cancellation) is a NEW generation and announces
 again. Disappeared queue rows never count as imported. Panel-managed
 generations stay solely with the panel hooks."""
 try:
  with _OBSERVER_LOCK:_observe(m)
 except Exception as exc:logging.warning('Download observer failed: %s',type(exc).__name__)

def _queue(m):
 rows=[]
 for page in (1,2,3):
  items=m.api('queue?page=%d&pageSize=200&includeEpisode=true&includeSeries=true'%page)
  if isinstance(items,dict):items=items.get('records')
  if not isinstance(items,list):raise ValueError('Invalid queue response')
  rows.extend(items)
  if len(items)<200:break
 return rows[:600]

def _identity(row):
 did=row.get('downloadId')
 text=str(did).strip().lower() if did else ''
 return re.sub(r'[^0-9a-f]','',text)[:40] or ('sonarr:%s'%row.get('episodeId') if row.get('episodeId') else None)

def _panel_map(m):
 """{myshows episode id: set(hashes)} for every panel-managed download,
 plus the set of episodes with an ACTIVE panel record (panel hooks own
 those generations entirely)."""
 import episodes
 managed=episodes.read(m.WORKER_STATE/'managed-downloads.json',{})
 by_episode={};active=set()
 for owned in managed.values():
  running=any(x.get('state') not in ('done','cancelled') for x in owned.get('episodes',[]))
  for x in owned.get('episodes',[]):
   eid=x.get('episode',{}).get('id')
   if not isinstance(eid,int):continue
   if x.get('state') not in ('done','cancelled'):
    by_episode.setdefault(eid,set()).add(str(owned.get('hash','')).lower());active.add(eid)
 return by_episode,active

def _observe(m):
 import episodes
 snapshot=episodes.read(m.WORKER_STATE/'myshows-episodes.json',{})
 tracked=snapshot.get('episodes') or {}
 if not isinstance(tracked,dict) or not tracked:return
 mapped={v['sonarrId']:(str(k),v) for k,v in tracked.items()
         if isinstance(v,dict) and _id(v.get('sonarrId')) is not None}
 panel,_=_panel_map(m)
 state=_read_state(m,OBSERVER_STATE);initialized=bool(state.get('baseline'))
 records=state.get('records') or {};seen=state.get('seen') or {};generations=state.get('generations') or {}
 now=time.time();current={};queue=_queue(m) # errors abort without altering baseline/pending state
 for row in queue:
  if not isinstance(row,dict):continue
  ep=row.get('episode') or {};sid=ep.get('id') if isinstance(ep,dict) else None
  found=mapped.get(sid or row.get('episodeId'))
  if not found:continue
  eid,item=found;identity=_identity(row)
  if not identity:continue
  key=eid+':'+identity;current[key]=True
  # Panel and automatic observation have a single owner for this generation.
  if identity in panel.get(item.get('id'),set()):continue
  record=records.get(eid)
  if key not in seen:
   generation=int(generations.get(eid,0))+1;generations[eid]=generation
   # Capture the registered file BEFORE observing this download's import.
   actual=m.api('episode/%d'%item['sonarrId'])
   if not isinstance(actual,dict):raise ValueError('Invalid episode response')
   record={'hash':identity,'generation':generation,'created':now,'silent':not initialized,
           'started':False,'completed':False,'ready':False,'fileId':actual.get('episodeFileId')}
   records[eid]=record
  if not isinstance(record,dict) or record.get('hash')!=identity:continue
  size=row.get('size');left=row.get('sizeLeft')
  complete=False;progress=False
  # A queue size can represent a whole season pack. Only exact wanted files
  # establish episode progress for packs; queue totals are used for a single episode.
  if hasattr(m,'trpc'):
   torrents=m.trpc('torrent-get',{'ids':[identity],'fields':['files','fileStats']}).get('torrents',[])
   if torrents:
    torrent=torrents[0]
    try:
     wanted,_=episodes.select_files(torrent['files'],item,{})
     total=sum(torrent['files'][i]['length'] for i in wanted)
     done=sum(torrent['fileStats'][i]['bytesCompleted'] for i in wanted)
     progress=done>0;complete=total>0 and done>=total
    except (KeyError,ValueError,IndexError,episodes.UserError):pass
  generation='auto:%s:%s'%(identity,record['generation'])
  for phase,observed,flag in [('download_started',progress,'started'),('torrent_complete',complete,'completed')]:
   if observed and not record[flag]:
    if not record['silent']:emit_episode(phase,item,generation)
    record[flag]=True
 # Pending downloads remain observable after the queue entry disappears.
 # A nonempty NEW registered file, never disappearance/import queueing, is ready.
 checked=0
 pending=[(eid,record) for eid,record in records.items() if isinstance(record,dict) and not record.get('ready')]
 offset=int(state.get('confirmOffset',0))%max(1,len(pending))
 pending=pending[offset:]+pending[:offset]
 for eid,record in pending:
  item=tracked.get(eid)
  if not isinstance(item,dict) or not isinstance(record,dict):continue
  if record.get('ready') or checked>=CONFIRM_BUDGET:continue
  checked+=1
  actual=m.api('episode/%d'%item['sonarrId'])
  if not isinstance(actual,dict):raise ValueError('Invalid episode response')
  fid=actual.get('episodeFileId')
  if not fid or fid==record.get('fileId'):continue
  ef=m.api('episodefile/%d'%fid)
  if not isinstance(ef,dict):raise ValueError('Invalid file response')
  if not isinstance(ef.get('size'),(int,float)) or ef['size']<=0:continue
  if ef.get('seriesId') not in (None,item.get('seriesId')):continue
  record['ready']=True
  invalidate_local_media([item])
  if not record.get('silent'):
   emit_episode('library_ready',item,'auto:%s:%s'%(record['hash'],record['generation']))
 records={k:v for k,v in records.items() if k in tracked and now-v.get('created',0)<RECORD_TTL}
 if len(records)>RECORDS_MAX:
  records=dict(sorted(records.items(),key=lambda kv:kv[1].get('created',0),reverse=True)[:RECORDS_MAX])
 state.update({'baseline':True,'records':records,'seen':current,'confirmOffset':offset+checked,
               'generations':{k:v for k,v in generations.items() if k in tracked}})
 _save_state(m,OBSERVER_STATE,state)

# ------------------------------------------------- localMedia invalidation
def invalidate_local_media(entries):
 """Mark tracked episodes whose local metadata must not be trusted anymore
 (import/replacement confirmed). Observation/metadata state only — nothing
 else is touched. Entries expire, so a missed refresh self-heals."""
 try:
  stamp=time.time()+INVALID_TTL
  with _INVALID_LOCK:
   state={}
   try:
    with open(rt.worker_root()/INVALID_STATE,'rb') as f:state=json.loads(f.read(65536).decode('utf-8'))
    if not isinstance(state,dict):state={}
   except (OSError,ValueError,UnicodeError):state={}
   for item in entries or []:
    if not isinstance(item,dict):continue
    series=item.get('seriesId');season=item.get('season');number=item.get('number')
    if not all(isinstance(v,int) and not isinstance(v,bool) for v in (series,season,number)):continue
    state['sonarr:%d:%d:%d'%(series,season,number)]=stamp
   if len(state)>INVALID_MAX:
    keep=sorted(state.items(),key=lambda kv:kv[1],reverse=True)[:INVALID_MAX];state=dict(keep)
   _atomic_json(rt.worker_root()/INVALID_STATE,state,0o640)
 except Exception as exc:logging.warning('Local media invalidation failed: %s',type(exc).__name__)

def stale_local_media(verified_before=None):
 """Keys whose localMedia must be hidden until fresh metadata arrives."""
 try:
  with open(rt.worker_root()/INVALID_STATE,'rb') as f:state=json.loads(f.read(65536).decode('utf-8'))
  if not isinstance(state,dict):return set()
  now=time.time()
  return {k for k,expire in state.items() if isinstance(expire,(int,float)) and (expire>now if verified_before is None else expire-INVALID_TTL>=verified_before)}
 except (OSError,ValueError,UnicodeError):return set()

# ------------------------------------------------------------ daily upgrades
def upgrade_started(day):
 return emit('upgrade_started',title='Ежедневная проверка улучшений запущена',
             message='Проверяем скачанные непросмотренные серии по расписанию',dedupe='upgrade_started:'+day)

def upgrade_result(day,found,errors,total):
 """Honest daily outcome: errors are reported as errors even when some
 upgrades were found — never 'no improvements' after a failed check."""
 if errors:
  title='Ежедневная проверка завершилась с ошибками'
  message='Не удалось проверить серий: %d из %d. Текущие файлы сохранены.'%(errors,total)
  if found:message+=' Улучшение начато для %d серий.'%found
 elif found:
  title='Найдены улучшения озвучки или качества'
  message='Загрузка началась для %d серий.'%found
 else:
  title='Улучшений пока нет'
  message='Проверено серий: %d. Текущие файлы сохранены; проверка продолжится по расписанию.'%total
 return emit('upgrade_result',title=title,message=message,dedupe='upgrade_result:'+day)
