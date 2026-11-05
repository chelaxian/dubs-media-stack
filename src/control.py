#!/usr/bin/python3
"""Per-series Sonarr policy UI and authenticated MyShows watched-file cleanup."""
import copy,datetime,hashlib,html,json,logging,os,secrets,subprocess,threading,time,urllib.parse,xml.etree.ElementTree as ET
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import requests
import re,stat,contextlib,sqlite3,ssl
import sys
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import runtime as rt
sys.path.insert(0,str(rt.ui_dir()))
import render as ui
import security as sec
import episodes
import notifications
import players
import studio_order
import watch_settings
import schedule_settings
STATE=rt.state_root()
CFG=rt.settings()
ROLE=os.environ.get('SERIES_CONTROL_ROLE','web')
WORKER_STATE=rt.worker_root()
STUDIOS=json.loads(rt.studios_path().read_text())
LOCK=threading.RLock();AUTH_LOCK=threading.BoundedSemaphore(2)
connection=rt.connection('sonarr');BASE=connection['base_url']+'/api/v3';HEAD={'X-Api-Key':connection['key']}
def mobile_artifact_names():
 return {x['file'] for x in rt.mobile_artifacts()}
def mobile_artifacts():
 return rt.mobile_artifacts()
WORKER_FILES={'pending-sources.json','cleanup-status.json','deleted.json'}
def state_path(name):
 if name not in {'preferences.json','myshows-auth.json','cleanup-request.json'}|WORKER_FILES:raise ValueError('Invalid state name')
 return (WORKER_STATE if name in WORKER_FILES else STATE)/name

def load(name,default):
 p=state_path(name)
 try:
  with p.open() as f:
   if os.fstat(f.fileno()).st_size>4*1024*1024:raise ValueError('State too large')
   return json.load(f)
 except FileNotFoundError:return default

def save(name,data):
 p=state_path(name);tmp=p.parent/('.'+p.name+'.'+secrets.token_hex(8))
 fd=os.open(tmp,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o640)
 os.fchmod(fd,0o640)
 try:
  with os.fdopen(fd,'w') as f:json.dump(data,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
  os.replace(tmp,p)
 finally:tmp.unlink(missing_ok=True)
def api(path,method='GET',data=None):
 r=requests.request(method,BASE+'/'+path,headers=HEAD,json=data,timeout=40);r.raise_for_status();return r.json() if r.content else None

def curl_args(proxy=None,max_time='35'):
 cmd=['curl','--fail','--silent','--show-error','--max-time',max_time]
 route=proxy if proxy is not None else CFG['proxy']
 if route:cmd+=['--socks5-hostname',route]
 return cmd
def myrpc(method,params,token=None):
 headers='Content-Type: application/json\n'
 if token:headers+='Authorization: Bearer '+token+'\n'
 # Headers (including the token) travel over stdin, never process arguments.
 payload=json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params})
 r=subprocess.run(curl_args()+['-H','@-','--data',payload,'https://api.myshows.me/v2/rpc/'],input=headers,text=True,capture_output=True,timeout=40)
 if r.returncode:raise RuntimeError('MyShows: сеть недоступна')
 j=json.loads(r.stdout)
 if j.get('error'):raise RuntimeError('MyShows API: '+str(j['error'].get('code')))
 return j['result']
def login_myshows(username,password):
 # The official website trims both inputs and accepts either login or e-mail.
 username=username.strip();password=password.strip()
 if not username or not password:raise ValueError('Введите логин или e-mail и пароль MyShows')
 if username.casefold()==CFG['login'].casefold():username=CFG['login']
 payload=json.dumps({'login':username,'password':password})
 proxies=[CFG['proxy']] if CFG['proxy'] else ['']
 for proxy in proxies:
  try:
   r=subprocess.run(curl_args(proxy=proxy,max_time='20')+['-H','Content-Type: application/json','-H','Accept: application/json','-H','Origin: https://myshows.me','-H','Referer: https://myshows.me/','-A','Mozilla/5.0','--data-binary','@-','--write-out','\n%{http_code}','https://myshows.me/api/session'],input=payload,text=True,capture_output=True,timeout=25)
  except subprocess.TimeoutExpired:
   logging.warning('MyShows login: timeout via %s',proxy);continue
  if r.returncode:
   logging.warning('MyShows login: transport error %s via %s',r.returncode,proxy);continue
  try:body,status=r.stdout.rsplit('\n',1);status=int(status)
  except (ValueError,TypeError):continue
  logging.info('MyShows login HTTP %s via %s',status,proxy)
  if status>=500 or status in (403,408):continue
  if status==429:raise ValueError('MyShows ограничил частоту входа. Подождите несколько минут; повторные попытки сейчас не выполняются.')
  if status in (401,422):raise ValueError('MyShows отклонил логин или пароль (HTTP '+str(status)+'). Можно ввести e-mail вместо ника. Проверьте вход с этими данными на myshows.me; вход через Google/Яндекс может требовать отдельного пароля MyShows.')
  if status==400:raise ValueError('MyShows отклонил данные формы (HTTP 400). Введите логин или e-mail и пароль MyShows.')
  if status<200 or status>=300:raise ValueError('MyShows вернул HTTP '+str(status)+'. Используйте токен доступа либо повторите позже.')
  try:token=json.loads(body).get('token')
  except (ValueError,AttributeError):raise ValueError('MyShows вернул неожиданный ответ. Попробуйте подключение токеном.')
  if not token:raise ValueError('MyShows не выдал токен доступа')
  verify_token(token)
  return token
 raise ValueError('Не удалось подключиться к MyShows. Повторите позже; данные входа не проверены.')

def verify_token(token):
 if not token or len(token)>8192 or any(x in token for x in '\r\n'):raise ValueError('Некорректный токен')
 profile=myrpc('profile.Get',{},token)
 if profile.get('user',{}).get('login','').casefold()!=CFG['login'].casefold():raise ValueError('Токен должен принадлежать '+CFG['login'])
 return profile

def watching():
 root=rt.feed_state_root();mapping=json.loads((root/'mapping.json').read_text());shows=json.loads((root/'profile.json').read_text());series={x['tvdbId']:x for x in api('series')};out=[]
 for item in shows:
  if item.get('watchStatus')!='watching':continue
  sh=item['show'];mapped=mapping.get(str(sh['id']),{});sr=series.get(mapped.get('TvdbId'))
  if sr:out.append({'myshowsId':sh['id'],'title':sh['title'],'series':sr})
 return sorted(out,key=lambda x:x['title'].casefold())
def chosen_profile(baseid,studio,mode):
 profiles=api('qualityprofile');base=next(x for x in profiles if x['id']==baseid)
 fs=api('customformat');name='Выбор сериала — '+STUDIOS[studio]['name'];cf=next((x for x in fs if x['name']==name),None)
 if cf is None:
  cf=api('customformat','POST',{'name':name,'includeCustomFormatWhenRenaming':False,'specifications':[{'name':'Studio','implementation':'ReleaseTitleSpecification','negate':False,'required':False,'fields':[{'name':'value','value':STUDIOS[studio]['pattern']}]}]})
 # Read after format creation, because Sonarr adds its zero-score item to all profiles.
 base=api('qualityprofile/'+str(baseid));p=copy.deepcopy(base);p.pop('id');p['name']=f'Сериал / база {baseid} / {STUDIOS[studio]["name"]} / '+('только' if mode=='strict' else 'приоритет')
 for item in p['formatItems']:
  if item['name'].startswith('Выбор сериала — '):item['score']=1000000 if item['format']==cf['id'] else 0
 p['minFormatScore']=1000001 if mode=='strict' else base['minFormatScore']
 old=next((x for x in api('qualityprofile') if x['name']==p['name']),None)
 if old:p['id']=old['id'];return api('qualityprofile/'+str(old['id']),'PUT',p)['id']
 return api('qualityprofile','POST',p)['id']
def set_preference(sid,studio,mode):
 with LOCK:
  entries=watching();entry=next((x for x in entries if x['series']['id']==sid),None)
  if not entry:raise ValueError('Сериал отсутствует в списке «смотрю»')
  prefs=load('preferences.json',{});old=prefs.get(str(sid));s=api('series/'+str(sid));base=old['baseProfile'] if old else s['qualityProfileId']
  if studio==-1:target=base
  else:
   if studio<0 or studio>=len(STUDIOS) or mode not in ('fallback','strict'):raise ValueError('Неверный выбор')
   target=chosen_profile(base,studio,mode)
  s['qualityProfileId']=target;api('series/'+str(sid),'PUT',s)
  if studio==-1:prefs.pop(str(sid),None)
  else:prefs[str(sid)]={'baseProfile':base,'studio':STUDIOS[studio]['name'],'mode':mode,'profileId':target}
  save('preferences.json',prefs)

def match_watched(detail,watched,episodes):
 # Unique season/episode mapping only; specials and ambiguous numbering stay intact.
 ids=set()
 for x in watched:
  try:
   date=datetime.datetime.fromisoformat(x.get('watchDate','').replace('Z','+00:00'))
   if date.year>=2000 and date.date()<=datetime.datetime.now().date():ids.add(int(x['id']))
  except (ValueError,TypeError,AttributeError):continue
 by={}
 for e in detail.get('episodes',[]):
  key=(e.get('seasonNumber'),e.get('episodeNumber'));by.setdefault(key,[]).append(e)
 out=[]
 for ep in episodes:
  key=(ep['seasonNumber'],ep['episodeNumber']);matches=by.get(key,[])
  if key[0]>0 and key[1]>0 and len(matches)==1 and int(matches[0]['id']) in ids:out.append(ep)
 return out

@contextlib.contextmanager
def parent_fd(path,root):
 p=Path(path);r=Path(root)
 if not p.is_absolute() or '..' in p.parts:raise ValueError('Unsafe path')
 parts=p.relative_to(r).parts
 if not parts:raise ValueError('Root path')
 fd=os.open(r,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 try:
  for part in parts[:-1]:
   new=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=new
  yield fd,parts[-1]
 finally:os.close(fd)

def root_for(path):
 p=Path(path);mp=rt.media_paths()
 for r in [mp['shows_host'],mp['torrent_host']]:
  try:p.relative_to(r);return r
  except ValueError:continue
 raise ValueError('Outside media roots')
def safe_path(raw,root):
 p=Path(raw)
 with parent_fd(p,root) as (fd,name):
  try:st=os.stat(name,dir_fd=fd,follow_symlinks=False)
  except FileNotFoundError:return p
  if not stat.S_ISREG(st.st_mode):raise ValueError('Not a regular file')
 return p
@contextlib.contextmanager
def open_media(path):
 with parent_fd(path,root_for(path)) as (fd,name):
  f=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
  with os.fdopen(f,'rb') as stream:
   if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise ValueError('Not regular')
   yield stream

COVER_CANDIDATES=('fanart-360.jpg','fanart.jpg','poster-250.jpg','poster.jpg');COVER_MAX=5*1024*1024
def _cover_sid(sid):
 if not re.fullmatch(r'[1-9][0-9]{0,7}',str(sid)):raise ValueError('Unsafe series id')
 return str(sid)
def cover_cache_image(root,sid):
 """Read the cached cover, preserving the fanart/poster candidate order.

 Every component is opened without following symbolic links; only regular
 files within the cache root qualify."""
 try:sid=_cover_sid(sid)
 except ValueError:return None
 try:
  with parent_fd(Path(root)/sid/'poster.jpg',root) as (fd,name):
   for candidate in COVER_CANDIDATES:
    try:
     handle=os.open(candidate,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
    except OSError:continue
    with os.fdopen(handle,'rb') as f:
     st=os.fstat(f.fileno())
     if not stat.S_ISREG(st.st_mode) or st.st_size>COVER_MAX:continue
     return f.read(COVER_MAX)
 except OSError:pass
 return None
def fetch_cover(m,sid):
 """On-demand poster from the CONFIGURED Sonarr only (fresh installs, remote
 adopt). The API key stays in headers, never the URL; redirects are refused;
 only genuine JPEG bodies within the size bound are accepted."""
 try:sid=_cover_sid(sid)
 except ValueError:return None
 try:
  r=m.requests.get(m.BASE+'/mediacover/'+sid+'/poster.jpg',headers=m.HEAD,timeout=5,allow_redirects=False)
  if r.status_code!=200:return None
  if r.headers.get('Content-Type','').split(';',1)[0].strip().lower()!='image/jpeg':return None
  length=r.headers.get('Content-Length','')
  if length.isdigit() and int(length)>COVER_MAX:return None
  data=r.content
  if not data or len(data)>COVER_MAX or data[:3]!=b'\xff\xd8\xff':return None
  return data
 except Exception:return None
def store_cover(root,sid,image):
 """Best-effort atomic cache write via dir_fd+O_NOFOLLOW. Never raises."""
 try:sid=_cover_sid(sid)
 except ValueError:return False
 try:
  rfd=os.open(str(root),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
  try:
   try:os.mkdir(sid,0o755,dir_fd=rfd)
   except FileExistsError:
    if not stat.S_ISDIR(os.stat(sid,dir_fd=rfd,follow_symlinks=False).st_mode):return False
   dfd=os.open(sid,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=rfd)
   try:
    tmp='.'+secrets.token_hex(8)+'.tmp'
    f=os.open(tmp,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o640,dir_fd=dfd)
    try:
     with os.fdopen(f,'wb') as s:s.write(image);s.flush();os.fsync(s.fileno())
     # rename(2) replaces a symlink at the destination without following it.
     os.rename(tmp,'poster.jpg',src_dir_fd=dfd,dst_dir_fd=dfd)
    finally:
     try:os.unlink(tmp,dir_fd=dfd)
     except FileNotFoundError:pass
   finally:os.close(dfd)
  finally:os.close(rfd)
 except OSError:return False
 return True
def cover_image(m,root,sid):
 """Cache first; on a miss fetch once from Sonarr and try to persist."""
 image=cover_cache_image(root,sid)
 if image is not None:return image
 image=fetch_cover(m,sid)
 if image is None:return None
 # Persistence is an optimization: serve the verified image even if the
 # cache write fails (full disk, readonly cache, ...).
 store_cover(root,sid,image)
 return image

def fingerprint(path):
 with open_media(path) as f:
  st=os.fstat(f.fileno());return [st.st_dev,st.st_ino,st.st_size]

def unlink_media(path,expected):
 with parent_fd(path,rt.media_paths()['torrent_host']) as (fd,name):
  try:st=os.stat(name,dir_fd=fd,follow_symlinks=False)
  except FileNotFoundError:return
  if not stat.S_ISREG(st.st_mode) or [st.st_dev,st.st_ino,st.st_size]!=expected:raise ValueError('File changed')
  os.unlink(name,dir_fd=fd)

def trpc(method,args):
 cfg=rt.connection('transmission');sess=requests.Session();sess.auth=(cfg['user'],cfg['password']);url=cfg['base_url']+'/transmission/rpc' if not cfg['base_url'].endswith('/transmission/rpc') else cfg['base_url']
 r=sess.post(url,json={'method':method,'arguments':args},timeout=30)
 if r.status_code==409:sess.headers['X-Transmission-Session-Id']=r.headers['X-Transmission-Session-Id'];r=sess.post(url,json={'method':method,'arguments':args},timeout=30)
 r.raise_for_status();j=r.json()
 if j['result']!='success':raise ValueError('Transmission RPC failed')
 return j['arguments']
def digest(p):
 h=hashlib.sha256()
 with open_media(p) as f:
  for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
 return h.digest()
def source_copy(seriesid,fileid,library):
 mp=rt.media_paths();history=api(f'history/series?seriesId={seriesid}')
 hits=[h for h in history if h.get('eventType')=='downloadFolderImported' and str(h.get('data',{}).get('fileId'))==str(fileid)]
 if not hits:return None
 data=hits[-1]['data'];raw=data.get('droppedPath','')
 if not raw.startswith(mp['sonarr_torrents']+'/'):return None
 p=safe_path(mp['torrent_host']+'/'+raw.removeprefix(mp['sonarr_torrents']+'/'),mp['torrent_host'])
 if not p.exists():return None
 ps=fingerprint(p);ls=fingerprint(library)
 if ps[2]!=ls[2]:raise ValueError('Исходный файл не совпадает по размеру')
 if ps[:2]!=ls[:2] and digest(p)!=digest(library):raise ValueError('Исходный файл не совпадает по содержимому')
 return p

def disable_source(source):
 mp=rt.media_paths();downloads=mp['downloads']
 torrents=trpc('torrent-get',{'fields':['id','downloadDir','files','fileStats']})['torrents']
 for t in torrents:
  for i,f in enumerate(t['files']):
   raw=t['downloadDir'].rstrip('/')+'/'+f['name']
   if not raw.startswith(downloads+'/'):continue
   # Other torrents may contain absent/unselected subtitle directories. Only inspect
   # the exact verified source; unrelated stale entries must not block cleanup.
   candidate=Path(mp['torrent_host']+'/'+raw.removeprefix(downloads+'/'))
   if candidate!=source:continue
   candidate=safe_path(str(candidate),mp['torrent_host'])
   stats=t.get('fileStats',[])
   # Stop before removing the last selected source. A running seeder otherwise
   # reports missing data after intentional watched-episode cleanup. Keep shared
   # season torrents running while any other episode remains selected.
   if len(stats)==len(t['files']) and not any(j!=i and fs.get('wanted',False) for j,fs in enumerate(stats)):
    trpc('torrent-stop',{'ids':[t['id']]})
   trpc('torrent-set',{'ids':[t['id']],'files-unwanted':[i]})

def cleanup(dry=False):
 if ROLE!='worker':raise PermissionError('Cleanup is restricted to worker')
 mp=rt.media_paths()
 if not rt.cleanup_enabled():
  save('cleanup-status.json',{'time':time.time(),'state':'Удаление просмотренных отключено; файлы сохраняются'});return
 ok,reason=rt.storage_ready()
 if not ok:
  save('cleanup-status.json',{'time':time.time(),'state':'Хранилище не готово ('+reason+'); удаление не выполнялось'});return
 auth=load('myshows-auth.json',{});token=auth.get('token')
 if not token:save('cleanup-status.json',{'time':time.time(),'state':'Нужно подключить MyShows; удаление не выполнялось'});return
 verify_token(token);deleted=[];errors=[]
 # Include every verified previously mapped show, even if moved from watching to completed.
 mapping=json.loads((rt.feed_state_root()/'mapping.json').read_text());series={s['tvdbId']:s for s in api('series')}
 for mid,m in mapping.items():
  sr=series.get(m.get('TvdbId'))
  if not sr or sr.get('seriesType')!='standard':continue
  eps=api('episode?seriesId='+str(sr['id']))
  try:
   watched=myrpc('profile.Episodes',{'showId':int(mid)},token)
   detail=myrpc('shows.GetById',{'showId':int(mid),'withEpisodes':True},token)
   # Revalidate show identity using IMDb before considering destructive operations.
   expected=m.get('ImdbId');actual=detail.get('imdbId')
   if not expected or not actual or int(expected.removeprefix('tt'))!=int(str(actual).removeprefix('tt')):raise ValueError('IMDb не совпадает / отсутствует')
   matched=match_watched(detail,watched,eps);seen={e['id'] for e in matched}
   to_unmonitor=[e['id'] for e in matched if e.get('monitored')]
   if to_unmonitor and not dry:
    with watch_settings.deletion_guard() as allowed:
     if not allowed:save('cleanup-status.json',{'time':time.time(),'state':'Удаление просмотренных отключено; файлы сохраняются'});return
     api('episode/monitor','PUT',{'episodeIds':to_unmonitor,'monitored':False})
   groups={}
   for e in eps:
    if e.get('episodeFileId'):groups.setdefault(e['episodeFileId'],[]).append(e)
   for fid,group in groups.items():
    if not all(e['id'] in seen for e in group):continue
    ef=api('episodefile/'+str(fid));raw=ef['path']
    if not raw.startswith(mp['sonarr_tv']+'/'):raise ValueError('Файл за пределами '+mp['sonarr_tv'])
    lp=safe_path(mp['shows_host']+'/'+raw.removeprefix(mp['sonarr_tv']+'/'),mp['shows_host'])
    if not lp.is_file():continue
    source=source_copy(sr['id'],fid,lp)
    record={'series':sr['title'],'episodes':[f'S{e["seasonNumber"]:02}E{e["episodeNumber"]:02}' for e in group],'fileId':fid,'bytes':lp.stat().st_size,'sourceRemoved':bool(source)}
    if not dry:
     with watch_settings.deletion_guard() as allowed:
      if not allowed:save('cleanup-status.json',{'time':time.time(),'state':'Удаление просмотренных отключено; файлы сохраняются'});return
      # Remove monitoring first; an interruption may leave files, never a re-download loop.
      api('episode/monitor','PUT',{'episodeIds':[e['id'] for e in group],'monitored':False})
      if source:disable_source(source)
      # Persist the exact pending source before deleting the library file; crash recovery retries it.
      source_id=fingerprint(source) if source else None
      if source:
       pending=load('pending-sources.json',[]);pending.append({'path':str(source),'fingerprint':source_id});save('pending-sources.json',pending)
      api('episodefile/'+str(fid),'DELETE')
      if source:unlink_media(source,source_id)
      record['time']=time.time();log=load('deleted.json',[]);log.append(record);save('deleted.json',log[-2000:])
    deleted.append(record)
  except Exception as e:errors.append({'series':sr['title'],'error':type(e).__name__});logging.warning('Cleanup skipped series %s: %s',sr['id'],type(e).__name__)
  time.sleep(.3)
 if not dry:
  # Complete only source removals recorded after all safeguards and before the library delete.
  pending=load('pending-sources.json',[]);remaining=[]
  for record in pending:
   try:
    with watch_settings.deletion_guard() as allowed:
     if not allowed:return
     p=safe_path(record['path'],rt.media_paths()['torrent_host']);disable_source(p);unlink_media(p,record['fingerprint'])
   except Exception:remaining.append(record)
  save('pending-sources.json',remaining)
 save('cleanup-status.json',{'time':time.time(),'state':'Предпросмотр' if dry else ('Проверка завершена с ошибками' if errors else 'Проверка завершена'),'files':deleted,'errors':errors})

def worker():
 # Epoch/cursor must exist before anything else serves or cleans up; the
 # journal init never requires upstream authentication and never raises here.
 notifications.journal_init()
 last=0
 while True:
  requested=load('cleanup-request.json',{}).get('requested',0)
  if not isinstance(requested,(int,float)):requested=0
  now=time.time()
  if now-last>=1800 or (requested>last and now-last>=120):
   try:cleanup()
   except Exception as e:save('cleanup-status.json',{'time':time.time(),'state':'Ошибка проверки; удаление прервано','error':type(e).__name__})
   last=time.time()
  time.sleep(30)

STYLE=(rt.ui_dir()/'style.css').read_text()

def esc(s):return html.escape(str(s),quote=True)
class Handler(sec.HardenedHandler):
 csp="default-src 'none'; style-src 'sha256-"+__import__('base64').b64encode(hashlib.sha256(STYLE.encode()).digest()).decode()+"'; img-src 'self'; script-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
 def log_message(self,*args):pass
 def output(self,body,status=200,cookie=None):
  family=sec.native_family(self.headers.get('User-Agent',''))
  mobile=family in ('native:iOS','native:Android')
  classes=('dubs-native-mobile'+(' dubs-native-ios' if family=='native:iOS' else '')) if mobile else ''
  viewport='width=device-width,initial-scale=1'+(',maximum-scale=1,minimum-scale=1,user-scalable=no' if mobile else '')
  data=('<!doctype html><html lang="ru" class="'+classes+'"><meta charset="utf-8"><meta name="viewport" content="'+viewport+'"><title>Мои сериалы · MyShows</title><meta name="theme-color" content="#000000"><style>'+STYLE+'</style><script src="/ui.js" defer></script><main>'+body+'</main></html>').encode();self.send_response(status);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(data)))
  if cookie:self.send_header('Set-Cookie',cookie)
  self.end_headers();self.wfile.write(data)
 def json_output(self,data,status=200):
  payload=json.dumps(data,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
 def session(self):
  sid=sec.cookies(self,'series_session');session=sec.session(sid,self.headers.get('User-Agent',''))
  if session and session.get('persistent'):self._persistent_cookie=sid
  return session
 def query_params(self):
  """Strict bounded query parsing for the notification paging contract."""
  raw=urllib.parse.urlsplit(self.path).query
  pairs=urllib.parse.parse_qs(raw,keep_blank_values=True,strict_parsing=True,max_num_fields=4,errors='strict')
  if any(len(v)!=1 for v in pairs.values()):raise ValueError('Duplicate query field')
  return {k:v[0] for k,v in pairs.items()}
 def paged_int(self,raw,fallback):
  """Digits-only bounded ints: Python's int() accepts '1_0' and '+5', the
  paging contract must not."""
  if raw is None:return fallback
  if not re.fullmatch(r'[0-9]{1,12}',raw):raise ValueError('Invalid paging number')
  return int(raw)
 def bearer_token(self):
  """None = no Authorization header; '' = present but unusable (always 401)."""
  values=self.headers.get_all('Authorization') or []
  if not values:return None
  if len(values)!=1:return ''
  match=re.fullmatch(r'Bearer ([A-Za-z0-9_-]{43})',values[0])
  return match[1] if match else ''
 def notifications_get(self):
  route=urllib.parse.urlsplit(self.path).path
  if route!='/api/notifications/events':
   if route not in ('/api/notifications/settings','/api/notifications/devices','/api/notifications/history'):self.send_error(404);return
   if route!='/api/notifications/history' and urllib.parse.urlsplit(self.path).query:self.send_error(400);return
   session=self.session()
   if not session:self.send_error(401);return
   if route=='/api/notifications/settings':self.json_output(notifications.get_settings());return
   if route=='/api/notifications/devices':self.json_output({'devices':sec.device_rows(session['user'])});return
   try:
    params=self.query_params();
    if set(params)-{'before','limit'}:raise ValueError('query')
    limit=self.paged_int(params.get('limit'),50);before=self.paged_int(params.get('before'),notifications.tail()+1)
   except (ValueError,TypeError):self.send_error(400);return
   if not 1<=limit<=100 or before<0:self.send_error(400);return
   page=notifications.history_page(before,limit);page['instanceId']=notifications.instance_id()
   self.json_output(page);return
  # The scoped device token authorizes ONLY this read-only feed; an
  # Authorization header that is present but unusable never falls back to
  # the session cookie.
  token=self.bearer_token()
  if token=='':self.send_error(401);return
  if token is None:
   session=self.session()
   if not session:self.send_error(401);return
  else:
   device=sec.device_token_auth(token)
   if not device:self.send_error(401);return
   if not sec.consume([('notif-feed',device['deviceId'],120,60)]):self.send_error(429);return
  try:
   params=self.query_params();
   if set(params)-{'after','limit'}:raise ValueError('query')
   after=self.paged_int(params.get('after'),0);limit=self.paged_int(params.get('limit'),50)
  except (ValueError,TypeError):self.send_error(400);return
  if after<0 or not 1<=limit<=100:self.send_error(400);return
  page=notifications.events_page(after,limit)
  page['instanceId']=notifications.instance_id();page['settings']=notifications.get_settings()
  self.json_output(page)
 def notifications_post(self,s,data):
  if self.path=='/api/notifications/settings':
   if data['enabled'] not in ('true','false') or data['show_titles'] not in ('true','false') or len(data['types'])>512:raise ValueError('Invalid settings')
   try:types=json.loads(data['types'])
   except ValueError:raise ValueError('Invalid types')
   if not isinstance(types,list) or len(types)>len(notifications.EVENT_TYPES) or any(not isinstance(x,str) or x not in notifications.EVENT_TYPES or len(x)>64 for x in types):raise ValueError('Invalid types')
   if not sec.consume([('notif-settings',s['user'],20,300)]):self.send_error(429);return
   self.json_output(notifications.save_settings(data['enabled']=='true',list(dict.fromkeys(types)),data['show_titles']=='true'));return
  if self.path=='/api/notifications/devices':
   if data['platform'] not in ('android','android_tv','ios'):raise ValueError('Invalid platform')
   if not sec.consume([('notif-device',s['user'],6,3600)]):self.send_error(429);return
   epoch=notifications.instance_id()
   if not epoch:self.send_error(503);return
   device_id,token=sec.register_device(s['user'],data['platform'])
   # The raw token exists only in this one response; storage is hashed.
   self.json_output({'deviceId':device_id,'token':token,'cursor':notifications.tail(),'instanceId':epoch});return
  if not sec.consume([('notif-revoke',s['user'],10,300)]):self.send_error(429);return
  if not sec.revoke_device(s['user'],data['deviceId']):self.send_error(404);return
  self.json_output({'ok':True})
 def end_headers(self):
  if getattr(self,'_persistent_cookie',None) and self.path not in ('/logout','/login'):self.send_header('Set-Cookie',self.cookie('series_session',self._persistent_cookie,sec.NATIVE_COOKIE_AGE))
  super().end_headers()
 def do_GET(self):
  try:self.get()
  except (OSError,ValueError,sqlite3.Error):self.send_error(503)
  except Exception:logging.warning('Panel GET failed');self.send_error(503)
 def get(self):
  if not self.security_context['secure'] and self.client_address[0] not in ('127.0.0.1','::1'):
   if not CFG['public_url']:self.send_error(403);return
   self.send_response(302);self.send_header('Location',CFG['public_url']+'/');self.end_headers();return
  if self.path=='/api/schedule-settings':
   session=self.session()
   if not session or session['user'] not in sec.CONFIG['owner_ids']:self.send_error(401);return
   self.json_output(schedule_settings.current());return
  if self.path=='/api/watch-settings':
   session=self.session()
   if not session or session['user'] not in sec.CONFIG['owner_ids']:self.send_error(401);return
   self.json_output(watch_settings.current());return
  if self.path=='/api/studio-order':
   session=self.session()
   if not session or session['user'] not in sec.CONFIG['owner_ids']:self.send_error(401);return
   self.json_output(studio_order.view(STUDIOS));return
  if self.path.startswith('/apps/'):
   name=self.path.removeprefix('/apps/')
   if name not in mobile_artifact_names():self.send_error(404);return
   if not self.session():
    self.send_response(303);self.send_header('Location','/login');self.send_header('Set-Cookie',self.cookie('series_download',name,600));self.end_headers();return
   try:
    base=CFG['mobile'].get('dir') or '/opt/dubs-mobile/releases'
    fd=os.open(str(Path(base)/name),os.O_RDONLY|os.O_NOFOLLOW)
   except FileNotFoundError:self.send_error(404);return
   with os.fdopen(fd,'rb') as package:
    st=os.fstat(package.fileno())
    if not stat.S_ISREG(st.st_mode) or st.st_size>200*1024*1024:self.send_error(404);return
    self.send_response(200);self.send_header('Content-Type','application/octet-stream');self.send_header('Content-Disposition','attachment; filename="'+name+'"');self.send_header('Content-Length',str(st.st_size));self.end_headers()
    while chunk:=package.read(65536):self.wfile.write(chunk)
   return
  if self.path.startswith('/api/player-links/'):
   session=self.session()
   if not session:self.send_error(401);return
   value=self.path.removeprefix('/api/player-links/')
   if not re.fullmatch('[1-9][0-9]{0,9}',value):self.send_error(404);return
   try:self.json_output(players.links(sys.modules[__name__],int(value),session['user']))
   except ValueError:self.json_output({'message':'Серия ещё не сопоставлена с Jellyfin'},404)
   return
  if self.path=='/api/session':
   session=self.session()
   if not session:self.json_output({'message':'Сеанс истёк. Войдите заново'},401);return
   self.json_output({'csrf':session['csrf'],'userId':session['user']});return
  if urllib.parse.urlsplit(self.path).path.startswith('/api/notifications/'):
   self.notifications_get();return
  if self.path=='/api/episodes' or self.path.startswith('/api/jobs/'):
   if not self.session():self.send_error(401);return
   try:data=episodes.snapshot(sys.modules[__name__]) if self.path=='/api/episodes' else episodes.result(sys.modules[__name__],self.path.removeprefix('/api/jobs/'))
   except ValueError:self.send_error(404);return
   self.json_output(data);return
  if self.path=='/ui.js':
   data=(rt.ui_dir()/'ui.js').read_bytes();self.send_response(200);self.send_header('Content-Type','application/javascript; charset=utf-8');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data);return
  if self.path.startswith('/cover/'):
   if not self.session():self.send_error(401);return
   match=re.fullmatch(r'/cover/([1-9][0-9]{0,7})\.jpg',self.path)
   if not match:self.send_error(404);return
   image=cover_image(sys.modules[__name__],rt.covers_root(),match[1])
   if image is None:self.send_error(404);return
   self.send_response(200);self.send_header('Content-Type','image/jpeg');self.send_header('Content-Length',str(len(image)));self.end_headers();self.wfile.write(image);return
  if self.path not in ('/','/login'):self.send_error(404);return
  s=self.session()
  if not s:
   nonce=sec.nonce()
   return self.output('<div class="login-card"><span class="mark">M</span><h1>Мои сериалы</h1><p>Ваша медиатека. Ваши озвучки.<br>Войдите как администратор Jellyfin.</p><form class="login-form" method="post" action="/login"><input type="hidden" name="nonce" value="'+nonce+'"><label>Логин<input name="username" autocomplete="username" maxlength="128" required></label><label>Пароль<input name="password" type="password" autocomplete="current-password" maxlength="1024" required></label><button>Войти</button></form></div>',cookie=self.cookie('series_login',nonce,600))
  try:self.output(self.page(s['csrf']))
  except Exception as exc:logging.warning('Page render failed: %s',type(exc).__name__);self.output('<h1>Сервис временно недоступен</h1><p>Не удалось загрузить страницу. Попробуйте позже.</p>',503)
 def page(self,csrf):return ui.render(sys.modules[__name__],csrf)
 def do_POST(self):
  if not self.security_context['secure'] and self.client_address[0] not in ('127.0.0.1','::1'):
   self.send_error(403);return
  if self.path not in ('/login','/preference','/myshows-login','/myshows','/check','/logout','/api/action','/api/notifications/settings','/api/notifications/devices','/api/notifications/revoke','/api/studio-order','/api/watch-settings','/api/schedule-settings'):self.send_error(404);return
  if not self.check_post():return
  try:
   raw=self.rfile.read(int(self.headers['Content-Length']))
   data=urllib.parse.parse_qs(raw.decode('utf-8'),keep_blank_values=True,strict_parsing=True,max_num_fields=8,errors='strict')
   if any(len(v)!=1 for v in data.values()):raise ValueError('Duplicate field')
   data={k:v[0] for k,v in data.items()}
   fields={'/api/schedule-settings':{'csrf','sync_minutes','upgrade_time','upgrade_interval_hours'},'/api/watch-settings':{'csrf','confirm_watch','delete_watched'},'/api/studio-order':{'csrf','order'},'/login':{'nonce','username','password'},'/preference':{'csrf','series','studio','mode'},'/myshows-login':{'csrf','username','password'},'/myshows':{'csrf','token'},'/check':{'csrf'},'/logout':{'csrf'},'/api/action':{'csrf','op','episode','selection'},'/api/notifications/settings':{'csrf','enabled','types','show_titles'},'/api/notifications/devices':{'csrf','platform'},'/api/notifications/revoke':{'csrf','deviceId'}}
   if set(data)!=fields[self.path]:raise ValueError('Fields do not match')
   if self.path=='/login':self.login(data);return
   s=self.session()
   if not s or not re.fullmatch(r'[A-Za-z0-9_-]{43}',data.get('csrf','')) or not secrets.compare_digest(data.get('csrf',''),s['csrf']):
    logging.warning('Action authentication rejected: session=%s csrf_length=%s csrf_match=%s',bool(s),len(data.get('csrf','')),bool(s) and bool(re.fullmatch(r'[A-Za-z0-9_-]{43}',data.get('csrf',''))) and secrets.compare_digest(data.get('csrf',''),s['csrf']))
    self.send_error(403);return
   if self.path=='/api/schedule-settings':
    if s['user'] not in sec.CONFIG['owner_ids']:self.send_error(403);return
    if not sec.consume([('schedule-settings',s['user'],12,300)]):self.send_error(429);return
    value={k:(data[k] if k=='upgrade_time' else int(data[k])) for k in ('sync_minutes','upgrade_time','upgrade_interval_hours')}
    self.json_output(schedule_settings.update(value));return
   if self.path=='/api/watch-settings':
    if s['user'] not in sec.CONFIG['owner_ids']:self.send_error(403);return
    if not sec.consume([('watch-settings',s['user'],12,300)]):self.send_error(429);return
    if any(data[k] not in ('0','1') for k in ('confirm_watch','delete_watched')):raise ValueError('Invalid preference')
    self.json_output(watch_settings.update({k:data[k]=='1' for k in ('confirm_watch','delete_watched')}));return
   if self.path=='/api/studio-order':
    if s['user'] not in sec.CONFIG['owner_ids']:self.send_error(403);return
    if not sec.consume([('studio-order',s['user'],6,300)]):self.send_error(429);return
    if len(data['order'])>4096:raise ValueError('Order too large')
    with LOCK:result=studio_order.submit(STUDIOS,json.loads(data['order']))
    self.json_output(result,202);return
   if self.path=='/logout':
    sec.logout(sec.cookies(self,'series_session'));self.send_response(303);self.send_header('Location','/');self.send_header('Set-Cookie',self.cookie('series_session','',0));self.end_headers();return
   # Notification management POSTs answer JSON and carry their own narrow
   # rate limits; a scoped device token can never reach here (session+CSRF
   # only, checked above).
   if self.path.startswith('/api/notifications/'):
    self.notifications_post(s,data);return
   if not sec.consume([('actions',s['user'],30,60)]):self.send_error(429);return
   if self.path=='/api/action':
    try:
     if data['op'] in ('download_all','cancel_all'):
      if data['selection'] and data['op']!='download_all':raise ValueError('Unexpected selection')
      if not sec.consume([('bulk-'+data['op'],s['user'],12 if data['op']=='cancel_all' else 4,300)]):self.send_error(429);return
      self.json_output(episodes.submit_all(sys.modules[__name__],data['episode'],data['selection']) if data['op']=='download_all' else episodes.cancel_all(sys.modules[__name__],data['episode']),202);return
     is_plan=data['op']=='download' and not data['selection'] and any(str(e['id'])==data['episode'] and e.get('future') for group in episodes.snapshot(sys.modules[__name__]).get('upcoming',[]) for e in group['episodes'])
     if data['op'] in ('search','search_all','download','watch_all') and not is_plan and not sec.consume([('torrent-search',s['user'],12,300)]):self.send_error(429);return
     with LOCK:key=episodes.submit(sys.modules[__name__],data['op'],data['episode'],data['selection'])
     self.json_output({'job':key},202)
    except episodes.UserError as exc:self.json_output({'message':str(exc)},400)
    except ValueError:self.json_output({'message':'Некорректное задание'},400)
    return
   if self.path=='/preference':
    if not re.fullmatch(r'[1-9][0-9]{0,7}',data['series']) or not re.fullmatch(r'-1|[0-9]{1,2}',data['studio']) or data['mode'] not in ('fallback','strict'):raise ValueError('Invalid choice')
    set_preference(int(data['series']),int(data['studio']),data['mode'])
   elif self.path in ('/myshows-login','/myshows'):
    if not sec.consume([('account-connect',s['user'],4,900)]):self.send_error(429);return
    if not AUTH_LOCK.acquire(blocking=False):self.send_error(503);return
    try:
     if self.path=='/myshows-login':
      if len(data['username'])>128 or len(data['password'])>1024:raise ValueError('Invalid length')
      token=login_myshows(data['username'],data['password'])
     else:
      token=data['token'];verify_token(token)
     with LOCK:save('myshows-auth.json',{'token':token})
    finally:AUTH_LOCK.release()
    save('cleanup-request.json',{'requested':time.time()})
   elif self.path=='/check':
    if not sec.consume([('manual-check',s['user'],2,60)]):self.send_error(429);return
    save('cleanup-request.json',{'requested':time.time()})
   self.send_response(303);self.send_header('Location',('/#show-'+str(int(data['series']))) if self.path=='/preference' else '/#settings');self.end_headers()
  except (ValueError,KeyError,UnicodeError):self.output('<h1>Действие не выполнено</h1><p>Проверьте поля формы и данные подключения.</p><a href="/">Вернуться</a>',400)
  except Exception as exc:logging.warning('Panel action failed: %s',type(exc).__name__);self.output('<h1>Сервис временно недоступен</h1><p>Попробуйте позже.</p><a href="/">Вернуться</a>',503)
 def login(self,data):
  started=time.monotonic()
  def denied():
   time.sleep(max(0,.35-(time.monotonic()-started)));self.send_error(401)
  ip=self.security_context['client']
  if not sec.consume([('login-ip',ip,5,900),('login-global','owner',12,900)]):self.send_error(429);return
  if not sec.use_nonce(data['nonce'],sec.cookies(self,'series_login')):self.send_error(403);return
  if len(data['username'])>128 or len(data['password'])>1024:denied();return
  username=data['username'].strip()
  if username.casefold() not in sec.CONFIG['owner_names']:denied();return
  if not AUTH_LOCK.acquire(blocking=False):self.send_error(503);return
  try:
   try:
    jellyfin_base=rt.jellyfin_api_base()
    r=requests.post(jellyfin_base+'/Users/AuthenticateByName',headers={'Authorization':'MediaBrowser Client="SeriesControl", Device="Web", DeviceId="series-control", Version="2.0"'},json={'Username':username,'Pw':data['password']},timeout=(3,10),allow_redirects=False)
    if r.status_code>=500:self.send_error(503);return
    if r.status_code!=200:denied();return
    result=r.json();user=result['User']
    if user['Id'] not in sec.CONFIG['owner_ids'] or not user['Policy'].get('IsAdministrator') or user['Policy'].get('IsDisabled'):denied();return
    key,csrf=sec.new_session(user['Id'],self.headers.get('User-Agent',''),sec.cookies(self,'series_session'))
    # Do not retain a broad Jellyfin access token after checking the identity.
    if result.get('AccessToken'):
     try:requests.post(jellyfin_base+'/Sessions/Logout',headers={'X-Emby-Token':result['AccessToken']},timeout=3,allow_redirects=False)
     except requests.RequestException:pass
    target=sec.cookies(self,'series_download');location='/apps/'+target if target in mobile_artifact_names() else '/'
    self.send_response(303);self.send_header('Location',location);self.send_header('Set-Cookie',self.cookie('series_session',key,sec.cookie_age(self.headers.get('User-Agent',''))));self.send_header('Set-Cookie',self.cookie('series_login','',0));self.send_header('Set-Cookie',self.cookie('series_download','',0));self.end_headers()
   except requests.RequestException:self.send_error(503)
  finally:AUTH_LOCK.release()

if __name__=='__main__':
 logging.basicConfig(level=logging.INFO)
 if ROLE=='worker':worker()
 elif ROLE=='operations':episodes.run(sys.modules[__name__])
 else:
  watch_settings.ensure_lock()
  bind=rt.panel_bind()
  http=sec.BoundedServer((bind['host'],bind['http']),Handler);tls=sec.BoundedServer((bind['host'],bind['https']),Handler);tls.slots=http.slots
  context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2;context.load_cert_chain(bind['cert'],bind['key']);tls.socket=context.wrap_socket(tls.socket,server_side=True,do_handshake_on_connect=False)
  threading.Thread(target=http.serve_forever,daemon=True).start();tls.serve_forever()
