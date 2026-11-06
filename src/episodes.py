"""MyShows episode views and bounded, owner-only episode operations."""
import base64,concurrent.futures,datetime,hashlib,ipaddress,json,logging,os,re,secrets,subprocess,tempfile,threading,time,urllib.parse
from pathlib import Path
import requests
import sys
import jellyfin_links
import studio_order
import runtime as rt
import upgrades
import schedule_settings
import notifications
import tracker_merge
POOL=concurrent.futures.ThreadPoolExecutor(max_workers=2);SYNC_LOCK=threading.Lock();DOWNLOAD_LOCK=threading.Lock();CACHE_LOCK=threading.RLock()
VIDEO={'.mkv','.mp4','.avi','.m4v','.ts','.mov','.webm'};AUX={'.srt','.ass','.ssa','.sub','.vtt','.mka','.ac3','.aac'}
class UserError(Exception):pass

def atomic(path,data,mode=0o640):
 tmp=path.parent/('.'+path.name+'.'+secrets.token_hex(8));fd=os.open(tmp,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,mode);os.fchmod(fd,mode)
 with os.fdopen(fd,'w') as f:json.dump(data,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
 os.replace(tmp,path)
def read(path,default):
 try:
  fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
  with os.fdopen(fd) as f:
   if os.fstat(f.fileno()).st_size>12*1024*1024:raise UserError('Слишком большой ответ')
   return json.load(f)
 except FileNotFoundError:return default

def v3(m,method,params):
 token=m.load('myshows-auth.json',{}).get('token')
 if not token:raise UserError('Подключите аккаунт MyShows в настройках')
 allowed={'lists.EpisodesUnwatched','entity.Calendar','manage.CheckEpisode','shows.Episode'}
 if method not in allowed:raise ValueError('RPC method rejected')
 headers='Content-Type: application/json\nAuthorization2: Bearer '+token+'\nAccept-Language: ru\n'
 cmd=['curl','--fail','-sS','--max-time','35']
 if m.CFG['proxy']:cmd+=['--socks5-hostname',m.CFG['proxy']]
 r=subprocess.run(cmd+['-H','@-','--data',json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}),'https://myshows.me/v3/rpc/'],input=headers,text=True,capture_output=True,timeout=40)
 if r.returncode:raise UserError('MyShows временно недоступен. Попробуйте ещё раз')
 try:j=json.loads(r.stdout)
 except ValueError:raise UserError('MyShows вернул неожиданный ответ')
 if j.get('error'):raise UserError('MyShows не выполнил запрос; проверьте подключение аккаунта')
 return j.get('result')
def utcdate(raw):
 try:return datetime.datetime.fromisoformat(raw.replace('Z','+00:00')).timestamp()
 except (ValueError,AttributeError,TypeError):return None

def refresh(m,wait=False):
 if not SYNC_LOCK.acquire(blocking=wait):return
 try:
  # Re-verify token owner before using authenticated endpoints.
  m.verify_token(m.load('myshows-auth.json',{}).get('token'))
  watching={x['myshowsId']:x for x in m.watching()}
  # get_library (when the media module provides it) is the single
  # whole-library fetch and already carries the exact episode URLs, so
  # get_links must not run a second full scan. The old flat links cache is
  # still maintained for the players flow; a failed lookup degrades to the
  # last cached snapshot and never blocks the refresh.
  metadata_started=time.time();jf_links={};library={}
  if hasattr(jellyfin_links,'get_library'):
   try:
    for key,entry in (jellyfin_links.get_library(m) or {}).items():
     if not isinstance(entry,dict):continue
     if entry.get('jellyfinUrl'):jf_links[key]=entry['jellyfinUrl']
     library[key]=entry.get('localMedia')
   except Exception as exc:logging.warning('Jellyfin library metadata: %s',type(exc).__name__)
  else:
   try:jf_links=jellyfin_links.get_links(m)
   except Exception as exc:logging.warning('Jellyfin link lookup: %s',type(exc).__name__)
  if not jf_links:jf_links=read(m.WORKER_STATE/'jellyfin-links.json',{})
  if jf_links:atomic(m.WORKER_STATE/'jellyfin-links.json',jf_links)
  unwatched=v3(m,'lists.EpisodesUnwatched',{}) or []
  today=datetime.date.today();month=today.replace(day=1);days=[]
  for offset in range(6):
   next_month=(month.replace(day=28)+datetime.timedelta(days=4)).replace(day=1)
   received=v3(m,'entity.Calendar',{'startDate':month.isoformat(),'endDate':(next_month-datetime.timedelta(days=1)).isoformat()}) or {}
   days.extend(received.get('days',[]));month=next_month
  calendar={'days':days}
  sonarr={};released=[];upcoming=[];byid={}
  def convert(group,view,day=None):
   show=group.get('show',{});entry=watching.get(show.get('id'))
   if not entry:return
   sid=entry['series']['id']
   if sid not in sonarr:
    records=m.api('episode?seriesId='+str(sid));lookup={}
    for e in records:lookup.setdefault((e['seasonNumber'],e['episodeNumber']),[]).append(e)
    sonarr[sid]=lookup
   eps=[]
   for e in group.get('episodes',[]):
    if not isinstance(e.get('id'),int):continue
    season=e.get('seasonNumber');number=e.get('episodeNumber');matches=sonarr[sid].get((season,number),[]);matched=matches[0] if len(matches)==1 else None
    stamp=utcdate(e.get('airDate'));future=stamp is not None and stamp>time.time()
    if view=='upcoming' and not future:continue
    jf_url=jf_links.get(str(entry['series']['tvdbId'])+':'+str(season)+':'+str(number))
    item={'jellyfinUrl':jf_url,'localMedia':library.get(str(entry['series']['tvdbId'])+':'+str(season)+':'+str(number)),'id':e['id'],'showId':show['id'],'seriesId':sid,'seriesTitle':entry['title'],'title':e.get('title') or 'Без названия','season':season,'number':number,'airDate':e.get('airDate'),'calendarDate':day or (e.get('airDate') or '')[:10],'rating':e.get('rating'),'comments':e.get('commentsCount'),'runtime':e.get('runtime'),'future':future,'sonarrId':matched['id'] if matched else None,'hasFile':bool(jf_url or matched and matched.get('hasFile'))}
    if view=='released' and future:continue
    eps.append(item);byid[str(e['id'])]=item
   if eps:
    eps.sort(key=lambda x:(x.get('season') or 0,x.get('number') or 0));result={'id':show['id'],'seriesId':sid,'title':entry['title'],'status':show.get('status'),'episodes':eps};(released if view=='released' else upcoming).append(result)
  for group in unwatched:convert(group,'released')
  for day in calendar.get('days',[]):
   for group in day.get('shows',[]):convert(group,'upcoming',day.get('date'))
  result={'updated':time.time(),'metadataUpdated':metadata_started,'released':released,'upcoming':upcoming,'episodes':byid,'source':'MyShows lists.EpisodesUnwatched / entity.Calendar','error':None}
  atomic(m.WORKER_STATE/'myshows-episodes.json',result)
  # Journal newly aired episodes only after the snapshot itself succeeded;
  # the pass owns the per-show baseline (first sync stays silent) and never
  # raises into the media refresh.
  notifications.new_episode_pass(m,[x for group in released+upcoming for x in group['episodes']])
 except Exception as exc:
  logging.warning('Episode snapshot failed: %s',type(exc).__name__);old=read(m.WORKER_STATE/'myshows-episodes.json',{});old['error']='Не удалось обновить MyShows. Показаны последние полученные данные';atomic(m.WORKER_STATE/'myshows-episodes.json',old)
 finally:SYNC_LOCK.release()

def snapshot(m):
 data=read(m.WORKER_STATE/'myshows-episodes.json',{'released':[],'upcoming':[],'updated':None,'error':'Загружаем данные MyShows…'})
 data.pop('episodes',None)
 scheduled=read(m.WORKER_STATE/'scheduled-public.json',{})
 data['scheduled']=scheduled
 actions={}
 for path in (m.STATE/'requests').glob('*.json'):
  req=read(path,{})
  if req.get('op')!='download':continue
  eid=str(req.get('episode'));old=actions.get(eid,{})
  if req.get('created',0)<old.get('created',0):continue
  status=result(m,path.stem)
  actions[eid]={'job':path.stem,'created':req.get('created',0),**status}
 data['actions']=actions
 # Serve-time honesty: after a confirmed import/replacement the previous
 # localMedia facts are hidden until a fresh refresh re-verifies them; the
 # invalidation entries expire, so a missed refresh self-heals.
 stale=notifications.stale_local_media(data.get('metadataUpdated',0))
 if stale:
  for group in data.get('released',[])+data.get('upcoming',[]):
   for item in group.get('episodes',[]):
    if 'sonarr:%s:%s:%s'%(item.get('seriesId'),item.get('season'),item.get('number')) in stale:item['localMedia']=None
 return data

def episode(m,eid):
 data=read(m.WORKER_STATE/'myshows-episodes.json',{});e=data.get('episodes',{}).get(str(eid))
 if not e:raise UserError('Эпизод отсутствует в актуальном списке MyShows; обновите вкладку')
 if not any(x['myshowsId']==e['showId'] and x['series']['id']==e['seriesId'] for x in m.watching()):raise UserError('Сериал больше не находится в списке «смотрю»')
 e['future']=bool(utcdate(e.get('airDate')) and utcdate(e['airDate'])>time.time())
 if 'sonarr:%s:%s:%s'%(e.get('seriesId'),e.get('season'),e.get('number')) in notifications.stale_local_media(data.get('metadataUpdated',0)):e['localMedia']=None
 return e

def submit(m,op,eid,selection):
 if op not in ('refresh','watch','search','download','cancel','watch_all','search_all','watch_season','search_season'):raise ValueError('Unknown operation')
 if not re.fullmatch(r'[0-9]{1,10}',eid) or int(eid)<=0 and op!='refresh':raise ValueError('Invalid episode')
 if selection and not re.fullmatch(r'[0-9a-f]{32}:[0-9a-f]{20}',selection):raise ValueError('Invalid selection')
 queue=m.STATE/'requests';files=list(queue.glob('*.json'))
 for f in files:
  if time.time()-f.stat().st_mtime>7*86400:f.unlink(missing_ok=True)
 files=list(queue.glob('*.json'))
 active=sum(not (m.WORKER_STATE/'jobs'/f.name).exists() for f in files)
 if active>=12:raise UserError('Очередь занята; попробуйте чуть позже')
 key=secrets.token_hex(16);atomic(queue/(key+'.json'),{'op':op,'episode':int(eid),'selection':selection,'created':time.time()});return key

def released_group(m,anchor):
 if not re.fullmatch(r'[1-9][0-9]{0,9}',str(anchor)):raise ValueError('Invalid episode')
 e=episode(m,int(anchor))
 group=next((g for g in snapshot(m).get('released',[]) if g.get('seriesId')==e['seriesId']),None)
 if not group:raise UserError('Нет вышедших непросмотренных серий')
 return group

def selected_bulk_ids(m,selection,series_id):
 try:cachekey,tag=selection.split(':')
 except ValueError:raise ValueError('Invalid selection')
 if not re.fullmatch(r'[0-9a-f]{32}',cachekey) or not re.fullmatch(r'[0-9a-f]{20}',tag):raise ValueError('Invalid selection')
 cache=read(m.WORKER_STATE/'release-cache'/(cachekey+'.json'),{})
 scope=cache.get('episode',{})
 if scope.get('seriesId')!=series_id or not scope.get('bulkIds') or time.time()-cache.get('created',0)>1800:raise UserError('Результаты поиска устарели; выполните поиск снова')
 item=next((x for x in cache.get('items',[]) if x.get('tag')==tag),None)
 if not item:raise UserError('Раздача не найдена; повторите поиск')
 numbers=item['row'].get('episodeNumbers') or []
 return {eid for eid,number in scope['bulkIds'].items() if not numbers or number in numbers}

def submit_all(m,anchor,selection='',season_only=False):
 if not re.fullmatch(r'[1-9][0-9]{0,9}',anchor):raise ValueError('Invalid episode')
 e=episode(m,int(anchor))
 groups=snapshot(m).get('released',[])
 group=next((g for g in groups if g.get('seriesId')==e['seriesId']),None)
 if not group:raise UserError('Нет вышедших непросмотренных серий')
 eps=[x for x in group['episodes'] if not x.get('future') and (selection or not x.get('hasFile')) and (not season_only or x.get('season')==e.get('season'))]
 if selection:
  allowed=selected_bulk_ids(m,selection,e['seriesId']);eps=[x for x in eps if str(x['id']) in allowed]
  if not eps:raise UserError('В этой раздаче нет нужных непросмотренных серий')
 with m.LOCK:
  requests_by_episode={}
  for path in (m.STATE/'requests').glob('*.json'):
   req=read(path,{})
   if req.get('op')!='download':continue
   if result(m,path.stem).get('state') not in ('error','cancelled','done'):
    requests_by_episode[req.get('episode')]=path.stem
  jobs=[];batch=secrets.token_hex(16)
  for item in eps:
   if item['id'] in requests_by_episode:continue
   key=secrets.token_hex(16)
   atomic(m.STATE/'requests'/(key+'.json'),{'op':'download','episode':item['id'],'selection':selection,'created':time.time(),**({} if selection else {'batch':batch+'-'+str(item.get('season',0))})})
   requests_by_episode[item['id']]=key;jobs.append({'episode':item['id'],'job':key})
  if jobs:atomic(m.STATE/'requests'/(batch+'.ready'),{'count':len(jobs)})
 return {'jobs':jobs,'skipped':len(group['episodes'])-len(jobs)}

def cancel_all(m,anchor,season_only=False):
 if not re.fullmatch(r'[1-9][0-9]{0,9}',anchor):raise ValueError('Invalid episode')
 e=episode(m,int(anchor));view=snapshot(m)
 group=next((g for g in view.get('released',[]) if g['seriesId']==e['seriesId']),None)
 if not group:raise UserError('Сериал отсутствует в текущем списке')
 jobs=[]
 with m.LOCK:
  for item in group['episodes']:
   if season_only and item.get('season')!=e.get('season'):continue
   task=view.get('actions',{}).get(str(item['id']),{})
   if task.get('state') not in ('queued','running','waiting','downloading','importing','paused'):continue
   key=secrets.token_hex(16);atomic(m.STATE/'requests'/(key+'.json'),{'op':'cancel','episode':item['id'],'selection':'','created':time.time()});jobs.append({'episode':item['id'],'job':key})
 return {'jobs':jobs}

def result(m,key):
 if not re.fullmatch(r'[0-9a-f]{32}',key):raise ValueError('Bad job')
 return read(m.WORKER_STATE/'jobs'/(key+'.json'),{'state':'queued','message':'Задание в очереди'})
def report(m,key,state,message,**extra):atomic(m.WORKER_STATE/'jobs'/(key+'.json'),{'state':state,'message':message,'time':time.time(),**extra})

def indexer_api(m,path):
 cfg=rt.connection('prowlarr');r=requests.get(cfg['base_url']+'/api/v1/'+path,headers={'X-Api-Key':cfg['key']},timeout=(5,45));r.raise_for_status();return r.json()
def sonarr_search(m,e):
 r=requests.get(m.BASE+'/release',params=({'seriesId':e['seriesId'],'seasonNumber':e['season']} if e.get('seasonSearch') else {'episodeId':e['sonarrId']}),headers=m.HEAD,timeout=(5,45));r.raise_for_status();return r.json()

def quality_id_allowed(profile,qid):
 def walk(items):
  for x in items:
   if x.get('allowed') and x.get('quality',{}).get('id')==qid:return True
   if x.get('allowed') and walk(x.get('items',[])):return True
  return False
 return walk(profile['items'])
def season_hints(title):
 found=set(int(x) for x in re.findall(r'(?i)(?:\bS|season[\s._-]*|сезон[ыа]?[\s:._-]*)(\d{1,2})(?!\d)',title))
 for a,b in re.findall(r'(?i)(?:season|сезон[ыа]?)[\s:._-]*(\d{1,2})\s*[-–]\s*(\d{1,2})',title):found.update(range(int(a),min(int(b),int(a)+30)+1))
 return sorted(found)
def title_queries(series,localized):
 # Sonarr's TVDB-linked aliases and MyShows localized title are trusted metadata.
 # Strip only a trailing release-year disambiguator for search, never for identity.
 values=[re.sub(r'\s*[\[(](?:19|20)\d{2}[\])]\s*$','',series.get('title','')),localized]
 values += [x.get('title','') for x in series.get('alternateTitles',[]) if isinstance(x,dict)]
 result=[];seen=set()
 for name in values:
  name=re.sub(r'\s+',' ',name or '').strip()
  if len(name)<2 or len(name)>160 or name.casefold() in seen:continue
  seen.add(name.casefold());result.append(name)
  if len(result)>=4:break
 return result

def indexer_queries(sources,queries):
 # Launch both useful names on preferred indexers before slower secondary ones.
 def priority(source):
  name=source.get('name','').casefold()
  return next((i for i,marker in enumerate(('rutracker','kinozal','lostfilm','noname','nnm','rutor')) if marker in name),6)
 return [(source,query) for source in sorted(sources,key=priority) for query in queries]

def series_match(parsed,title,expected,identifiers=None):
 if not expected:return True
 mapped=(parsed.get('series') or {}).get('id')
 if mapped is not None:return mapped==expected['id']
 # Only compare provider IDs of the same type; a conflict is authoritative.
 identifiers=identifiers or {}
 compared=[]
 for key in ('tvdbId','tmdbId','imdbId'):
  actual=identifiers.get(key);want=expected.get(key)
  if actual and str(actual) not in ('0','tt0000000') and want and str(want) not in ('0','tt0000000'):
   def canonical_id(x):return str(x).lower().removeprefix('tt').lstrip('0') or '0'
   compared.append(canonical_id(actual)==canonical_id(want))
 if compared:return all(compared)
 info=parsed.get('parsedEpisodeInfo') or {};candidate=info.get('seriesTitle') or ''
 def slug(value):return re.sub(r'[^a-zа-яё0-9]','',re.sub(r'\s*[\[(](?:19|20)\d{2}[\])]','',value).casefold())
 aliases=[expected['title'],expected.get('_myshowsTitle','')]+[x.get('title','') for x in expected.get('alternateTitles',[]) if isinstance(x,dict)]
 known={slug(x) for x in aliases if x};joined={a+b for a in known for b in known if a!=b}
 parts=[slug(x.strip()) for x in re.split(r'\s*/\s*',candidate) if x.strip()]
 exact=bool(candidate) and (slug(candidate) in known|joined or parts and all(x in known for x in parts))
 # Extra release-name wording is accepted only with two independently sourced,
 # distinct script aliases (MyShows + Sonarr) and bounded title-word matches.
 # This does not turn a substring of one title into an identity match.
 original=re.sub(r'\s*[\[(](?:19|20)\d{2}[\])]\s*$','',expected.get('title','')).strip()
 localized=(expected.get('_myshowsTitle') or '').strip()
 def phrase(value):
  words=re.findall(r'[a-zа-яё0-9]+',value.casefold())
  return r'(?<![a-zа-яё0-9])'+r'[^a-zа-яё0-9]+'.join(map(re.escape,words))+r'(?![a-zа-яё0-9])' if words else r'(?!)'
 distinct_scripts=bool(re.search(r'[a-z]',original,re.I) and re.search(r'[а-яё]',localized,re.I))
 dual=bool(candidate and distinct_scripts and len(slug(original))>=4 and len(slug(localized))>=4 and re.search(phrase(original),candidate.casefold()) and re.search(phrase(localized),candidate.casefold()))
 if not exact and not dual:return False
 # A later season may carry its air year rather than the first season's year.
 # Accept that year only with BOTH independently sourced title variants.
 if re.search(r'[\[(](?:19|20)\d{2}[\])]',expected['title']):
  years=set(re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)',title))
  if str(expected.get('year')) in years:return True
  return bool(dual and expected.get('_airYear') and str(expected['_airYear']) in years)
 return True

def normalize(m,row,profile,source,expected=None):
 matches_series=True
 title=row.get('title','');scores={x['format']:x['score'] for x in profile.get('formatItems',[])};cfs=row.get('customFormats',[]);score=sum(scores.get(x['id'],0) for x in cfs)
 q=row.get('quality',{});qinfo=q.get('quality',{})
 veto=any(x.get('name')==rt.settings()['veto_format'] for x in cfs)
 voices=[m.STUDIOS[i]['name'] for i in studio_order.indices(m.STUDIOS) if m.STUDIOS[i]['name']!=studio_order.CATCHALL and re.search(m.STUDIOS[i]['pattern'],title)]
 reasons=list(row.get('rejections',[]))
 # Prowlarr results are parsed locally by Sonarr to apply the exact same rules.
 if source=='prowlarr':
  parsed=m.api('parse?title='+urllib.parse.quote(title));info=parsed.get('parsedEpisodeInfo') or {};q=info.get('quality',{});qinfo=q.get('quality',{});cfs=parsed.get('customFormats',[]);score=sum(scores.get(x['id'],0) for x in cfs);veto=any(x.get('name')==rt.settings()['veto_format'] for x in cfs)
  if expected and not series_match(parsed,title,expected,row):matches_series=False;reasons.append('Sonarr не подтвердил соответствие этому сериалу')
  numbers=info.get('episodeNumbers',[])
  span=re.search(r'(?i)\bS(\d{1,2})E(\d{1,3})\s*[-–]\s*(?:E)?(\d{1,3})(?!\d)',title)
  if span and int(span[1])==info.get('seasonNumber') and 0<int(span[2])<=int(span[3])<=100:numbers=list(range(int(span[2]),int(span[3])+1))
  row={**row,'seasonNumber':info.get('seasonNumber'),'episodeNumbers':numbers,'fullSeason':info.get('fullSeason',False),'quality':q,'languages':info.get('languages',[])}
 allowed=quality_id_allowed(profile,qinfo.get('id')) and score>=profile['minFormatScore'] and not veto
 if not allowed:reasons.append('Не соответствует качеству, русскому языку или выбранной озвучке')
 known_ignorable=('episode is not monitored','season is not monitored','series is not monitored','full season','season pack','one or more episodes','episode was not requested')
 blocked=[r for r in reasons if not r.lower().startswith(known_ignorable)]
 seed=row.get('seeders');seed=seed if isinstance(seed,int) else 0
 if seed<1:allowed=False;reasons.append('Нет подтверждённых сидов')
 if blocked:allowed=False
 rank=0
 for index,quality in enumerate(profile['items']):
  if quality.get('quality',{}).get('id')==qinfo.get('id') or any(x.get('quality',{}).get('id')==qinfo.get('id') for x in quality.get('items',[])):rank=index
 res={'matchesSeries':matches_series,'qualityRank':rank,'title':title,'indexer':row.get('indexer') or row.get('indexerName') or 'Трекер','seeders':seed,'leechers':row.get('leechers'),'downloads':row.get('downloadCount'),'published':row.get('publishDate'),'size':row.get('size',0),'quality':qinfo.get('name') or 'Не определено','resolution':qinfo.get('resolution') or 0,'voices':voices or (['Язык не подтверждён'] if veto else ['Другая русская озвучка']),'seasons':season_hints(title) or ([row['seasonNumber']] if row.get('seasonNumber') else []),'score':score,'allowed':allowed,'rejections':reasons}
 return res,row

def supports_tv(indexer):
 categories=(indexer.get('capabilities') or {}).get('categories')
 if categories is None:return True
 def walk(items):
  return any(5000<=x.get('id',0)<6000 or walk(x.get('subCategories',[])) for x in items)
 return walk(categories)

def waiting_summary(items,errors):
 reasons=list(dict.fromkeys(reason for item in items for reason in item['display'].get('rejections',[])))
 detail='; '.join(reasons[:2]) if reasons else 'раздач нужного сериала и сезона не найдено'
 return ('Ожидаем раздачу: найдено '+str(len(items))+' вариантов, подходящих 0. '+detail+
         ('. Не получены ответы от части источников ('+str(len(errors))+')' if errors else '')+
         '. Повторная проверка через 15 минут; в Transmission ничего не передано')

def search(m,key,e,on_progress=None):
 if not e.get('sonarrId'):raise UserError('Нумерация MyShows и Sonarr не сопоставлена однозначно')
 series=m.api('series/'+str(e['seriesId']));series['_myshowsTitle']=e['seriesTitle'];series['_airYear']=(e.get('airDate') or '')[:4];profile=m.api('qualityprofile/'+str(series['qualityProfileId']));errors=[];seen=set();private=[];public=[]
 started=time.time();deadline=started+65;sources=[]
 try:sources=[x for x in indexer_api(m,'indexer') if x.get('enable',True) and supports_tv(x)][:40]
 except Exception:pass
 pool=concurrent.futures.ThreadPoolExecutor(max_workers=16)
 tasks={pool.submit(sonarr_search,m,e):('sonarr','Sonarr')}
 if sources:
  for indexer,query in indexer_queries(sources,title_queries(series,e['seriesTitle'])):
   tasks[pool.submit(indexer_api,m,'search?type=search&indexerIds='+str(int(indexer['id']))+'&query='+urllib.parse.quote(query))]=('prowlarr',str(indexer.get('name','Трекер'))[:100])
 else:tasks[pool.submit(indexer_api,m,'search?type=search&query='+urllib.parse.quote(series['title']))]=('prowlarr','Prowlarr')
 total=len(tasks);completed=0;last_publish=0
 def publish():
  nonlocal last_publish
  req=read(m.STATE/'requests'/(key+'.json'),{})
  progress_key=key
  if e.get('batchJobs'):
   alive=False
   for child,eid in e['batchJobs']:
    try:check_cancelled(m,child,eid);alive=True;progress_key=child
    except UserError:pass
   if not alive:raise UserError('Заказ отменён')
  elif req.get('op')=='download':check_cancelled(m,key,e['id'])
  elapsed=time.time()-started;estimate=max(0,elapsed/max(1,completed)*(total-completed)) if completed else None
  atomic(m.WORKER_STATE/'release-cache'/(key+'.json'),{'episode':e,'created':time.time(),'items':private},0o600)
  report(m,progress_key,'running','Поиск: ответили '+str(completed)+' из '+str(total)+' источников · найдено '+str(len(public))+', подходят '+str(sum(bool(x.get('allowed')) for x in public)),rows=public,warnings=errors,episode=e,search={'completed':completed,'total':total,'elapsed':round(elapsed),'eta':round(estimate) if estimate is not None else None,'active':[v[1] for v in tasks.values()][:6]})
  for child,eid in e.get('batchJobs',[]):
   if child==progress_key:continue
   try:check_cancelled(m,child,eid)
   except UserError:continue
   report(m,child,'running','Общий поиск раздачи сезона: ответили '+str(completed)+' из '+str(total)+' источников')
  if on_progress:on_progress(public,errors,{'completed':completed,'total':total,'eta':round(estimate) if estimate is not None else None})
  last_publish=time.time()
 try:
  publish()
  while tasks:
   if time.time()>=deadline:
    errors.append('Лимит поиска 65 секунд: не дождались '+str(len(tasks))+' ответов; используем полученные результаты');break
   done,_=concurrent.futures.wait(tasks,timeout=1,return_when=concurrent.futures.FIRST_COMPLETED)
   if not done:
    publish();continue
   for future in done:
    source,name=tasks.pop(future);completed+=1
    try:raw=future.result()
    except Exception:errors.append('Источник '+name+' не ответил');publish();continue
    for row in raw[:700]:
     if len(private)>=700 or time.time()>=deadline:break
     hints=season_hints(row.get('title',''))
     if hints and e['season'] not in hints:continue
     if not row.get('downloadUrl') and not row.get('magnetUrl'):continue
     identity=(row.get('guid'),row.get('indexerId'),row.get('title'))
     if identity in seen:continue
     seen.add(identity)
     try:
      display,item=normalize(m,row,profile,source,series)
      if not display['matchesSeries']:continue
      numbers=item.get('episodeNumbers') or []
      if numbers and not e.get('seasonSearch') and e['number'] not in numbers:continue
      if numbers and item.get('seasonNumber') not in (None,0,e['season']):continue
      if display['seasons'] and e['season'] not in display['seasons']:continue
      tag=hashlib.sha256((str(row.get('guid',''))+'|'+str(row.get('indexerId',''))+'|'+str(row.get('downloadUrl',''))+'|'+row.get('title','')).encode()).hexdigest()[:20];display['key']=key+':'+tag;private.append({'tag':tag,'row':item,'display':display});public.append(display)
      if time.time()-last_publish>.5:publish()
     except Exception:continue
    publish()
 finally:pool.shutdown(wait=False,cancel_futures=True)
 return public,errors

# All external torrent credentials stay inside trusted indexer download proxies.
def validate_endpoint(url):
 """Exact scheme/host/port/path-prefix match against configured trusted endpoints.

 Implicit default ports are honored (https->443, http->80). Dot-segment
 escapes and credentials are rejected. Redirect targets are re-validated by
 the caller before each request, so a trusted proxy cannot be used as an
 open redirect to arbitrary hosts.
 """
 u=urllib.parse.urlsplit(url)
 if u.scheme not in ('http','https') or u.username or u.password:raise UserError('Источник не выдал безопасную локальную ссылку на торрент')
 port=u.port or (443 if u.scheme=='https' else 80)
 allowed=rt.indexer_endpoints().get((u.hostname or '').lower())
 if not allowed or port not in allowed['ports']:raise UserError('Источник не выдал безопасную локальную ссылку на торрент')
 if not any((u.path or '/').startswith(prefix) for prefix in allowed['prefixes']):raise UserError('Источник не выдал безопасную локальную ссылку на торрент')
 if '..' in u.path:raise UserError('Источник не выдал безопасную локальную ссылку на торрент')
 return u
def _tls_verify(url):
 u=urllib.parse.urlsplit(url);port=u.port or (443 if u.scheme=='https' else 80)
 allowed=rt.indexer_endpoints().get((u.hostname or '').lower()) or {}
 flags=allowed.get('tls',{}).get((u.scheme,port)) or [True]
 return flags[0]
def safe_download(m,url):
 for _ in range(4):
  if url.startswith('magnet:?'):return None,sanitize_magnet(url)
  validate_endpoint(url)
  r=requests.get(url,allow_redirects=False,stream=True,timeout=(5,60),verify=_tls_verify(url))
  if r.status_code in (301,302,303,307,308):
   url=urllib.parse.urljoin(url,r.headers.get('Location',''));r.close();continue
  r.raise_for_status();data=bytearray()
  for chunk in r.iter_content(65536):
   data.extend(chunk)
   if len(data)>10*1024*1024:r.close();raise UserError('Слишком большой torrent-файл')
  r.close();validate_metainfo(bytes(data));return bytes(data),None
 raise UserError('Слишком много перенаправлений источника')
def sanitize_magnet(url):
 q=urllib.parse.parse_qs(urllib.parse.urlsplit(url).query);xt=next((x for x in q.get('xt',[]) if re.fullmatch(r'urn:btih:(?:[a-fA-F0-9]{40}|[A-Z2-7]{32})',x)),None)
 if not xt:raise UserError('Неподдерживаемый magnet')
 values=[('xt',xt)];values.extend(('dn',x[:256]) for x in q.get('dn',[])[:1])
 for tr in q.get('tr',[])[:40]:
  u=urllib.parse.urlsplit(tr)
  if u.scheme not in ('http','https','udp') or not u.hostname or u.username or u.password:continue
  host=u.hostname.lower()
  if host=='localhost' or host.endswith(('.local','.internal')):continue
  try:
   if not ipaddress.ip_address(host).is_global:continue
  except ValueError:pass
  values.append(('tr',tr))
 return 'magnet:?'+urllib.parse.urlencode(values)

def validate_metainfo(data):
 pos=0
 def decode(depth=0):
  nonlocal pos
  if depth>30 or pos>=len(data):raise UserError('Некорректный torrent-файл')
  ch=data[pos:pos+1]
  if ch==b'i':
   pos+=1;end=data.index(b'e',pos);n=int(data[pos:end]);pos=end+1;return n
  if ch in (b'l',b'd'):
   pos+=1;items=[];d={}
   while pos<len(data) and data[pos:pos+1]!=b'e':
    if ch==b'l':items.append(decode(depth+1))
    else:k=decode(depth+1);d[k]=decode(depth+1)
    if len(items)+len(d)>20000:raise UserError('Слишком много файлов')
   pos+=1;return items if ch==b'l' else d
  end=data.index(b':',pos);n=int(data[pos:end]);pos=end+1
  if n<0 or pos+n>len(data):raise UserError('Некорректный torrent-файл')
  out=data[pos:pos+n];pos+=n;return out
 try:
  meta=decode();info=meta[b'info']
  if b'l' in info.get(b'attr',b''):raise UserError('Симлинки в torrent запрещены')
  if b'file tree' in info and b'files' not in info:raise UserError('Torrent v2 без совместимого списка файлов пока не поддерживается')
  files=info.get(b'files') or [{b'path':[info.get(b'name',b'')]}]
  for f in files:
   if b'l' in f.get(b'attr',b''):raise UserError('Симлинки в torrent запрещены')
   for part in [info.get(b'name',b'')]+f.get(b'path',[]):
    if not part or part in (b'.',b'..') or any(x in part for x in [b'/',b'\\',b'\0']):raise UserError('Небезопасный путь в torrent')
  return meta
 except UserError:raise
 except Exception:raise UserError('Источник не вернул корректный torrent-файл')

def metadata_only(m,magnet):
 with tempfile.TemporaryDirectory(prefix='series-metadata-') as d:
  r=subprocess.run(['aria2c','--input-file=-','--bt-metadata-only=true','--bt-save-metadata=true','--follow-torrent=false','--seed-time=0','--stop=90','--summary-interval=0','--console-log-level=error','--quiet=true','--dir='+d],input=magnet+'\n',capture_output=True,text=True,timeout=100)
  torrents=list(Path(d).glob('*.torrent'))
  if not torrents:raise UserError('Не удалось получить метаданные magnet без загрузки видео. Попробуйте другую раздачу')
  data=torrents[0].read_bytes();validate_metainfo(data);return data

def clean_file_path(name):
 if not isinstance(name,str) or len(name)>4096 or name.startswith(('/','\\')) or '\\' in name or '\0' in name:raise UserError('Небезопасные имена файлов')
 parts=name.split('/')
 if any(x in ('','.','..') for x in parts):raise UserError('Небезопасные имена файлов')
 return name

def file_episode(path,release_season=None):
 s=path.casefold();filename=s.rsplit('/',1)[-1]
 for pattern in [r'(?<![a-z0-9])s(\d{1,2})[ ._-]*e(\d{1,3})(?!\d)',r'(?<!\d)(\d{1,2})x(\d{1,3})(?!\d)']:
  hit=re.search(pattern,filename)
  if hit:
   after=filename[hit.end():]
   if re.match(r'(?:[ ._-]*e\d|[-–]\d|[ ._-]+\d{1,3}(?!\d))',after):return (-1,-1)
   return int(hit[1]),int(hit[2])
 seasons=season_hints('/'.join(s.split('/')[:-1]));season=seasons[-1] if seasons else release_season
 hit=re.match(r'^(?:эпизод|серия)?[ ._-]*(\d{1,3})(?:[ ._-]|\.[a-z])',filename)
 if season and hit:return season,int(hit[1])
 return None

def select_files(files,e,row):
 videos=[];hint=row.get('seasonNumber') or None
 for i,f in enumerate(files):
  p=clean_file_path(f['name']);ext=Path(p).suffix.casefold()
  if f.get('length',0)<=0 or ext not in VIDEO or re.search(r'(?i)(?:^|/)(?:sample|trailer)(?:[ ./_-]|$)',p):continue
  videos.append((i,p,file_episode(p,hint)))
 matched=[x for x in videos if x[2]==(e['season'],e['number'])]
 if not matched and len(videos)==1 and row.get('episodeNumbers')==[e['number']] and row.get('seasonNumber')==e['season'] and not row.get('fullSeason'):
  # Do not apply the fallback if explicit numbering points at another episode.
  if videos[0][2] is None:matched=videos
 if len(matched)!=1:raise UserError('Нельзя однозначно выделить ровно одну серию в этой раздаче. Торрент оставлен остановленным')
 video=matched[0];wanted=[video[0]];stem=str(Path(video[1]).with_suffix('')).casefold()
 for i,f in enumerate(files):
  p=clean_file_path(f['name'])
  if Path(p).suffix.casefold() in AUX and (file_episode(p,hint)==(e['season'],e['number']) or (file_episode(p,hint) is None and any(str(Path(p).with_suffix('')).casefold().startswith(stem+sep) for sep in ['.',' ','_','-']))):wanted.append(i)
 return wanted,video[0]

def preserve_replaced_file(m,current,e):
 """Keep a verified copy on the writable torrent volume before replacement."""
 mp=rt.media_paths();raw=(current.get('episodeFile') or {}).get('path','')
 prefix=mp['sonarr_tv'].rstrip('/')+'/'
 if not raw.startswith(prefix):raise UserError('Путь текущего файла не подтверждён; замена остановлена')
 path=Path(mp['shows_host'])/raw.removeprefix(prefix)
 try:
  with m.parent_fd(path,mp['shows_host']) as (fd,name):
   original=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
   try:
    info=os.fstat(original)
    if not __import__('stat').S_ISREG(info.st_mode):raise ValueError('Not a regular file')
    with m.parent_fd(Path(mp['torrent_host'])/'.dubs-replacement-backups',mp['torrent_host']) as (root,dirname):
     try:os.mkdir(dirname,0o700,dir_fd=root)
     except FileExistsError:pass
     dest=os.open(dirname,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=root)
     try:
      backup=str(e['sonarrId'])+'-'+str(e['_upgradeFileId'])
      # An existing backup is never trusted or overwritten: use a unique name.
      tmp=backup+'-'+secrets.token_hex(8)
      out=os.open(tmp,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600,dir_fd=dest)
      try:
       with os.fdopen(out,'wb') as target:
        while True:
         chunk=os.read(original,1024*1024)
         if not chunk:break
         target.write(chunk)
        target.flush();os.fsync(target.fileno())
       after=os.fstat(original);saved=os.stat(tmp,dir_fd=dest,follow_symlinks=False)
       if (info.st_size,info.st_mtime_ns)!=(after.st_size,after.st_mtime_ns) or saved.st_size!=info.st_size:raise ValueError('Backup mismatch')
      except BaseException:
       try:os.unlink(tmp,dir_fd=dest)
       except OSError:pass
       raise
     finally:os.close(dest)
   finally:os.close(original)
 except (OSError,ValueError) as exc:
  logging.warning('Replacement backup failed: %s',type(exc).__name__)
  raise UserError('Не удалось сохранить исходный файл перед заменой; импорт остановлен')

def request_download(m,key,e,item,metainfo=None,start=True):
 row=item['row'];display=item['display']
 if not display['allowed'] and not e.get('_manualSelection'):raise UserError('Раздача не соответствует обязательным условиям языка, озвучки, качества или сидов')
 if not e.get('sonarrId'):raise UserError('Эпизод не сопоставлен с Sonarr')
 current=m.api('episode/'+str(e['sonarrId']))
 if current.get('hasFile') and not e.get('_upgradeFileId'):return {'state':'done','message':'Эта серия уже есть в медиатеке'}
 if e.get('_upgradeFileId') and current.get('episodeFileId')!=e['_upgradeFileId']:raise UserError('Файл серии уже изменился; повторная замена отменена')
 if e.get('_upgradeFileId'):
  if not e.get('_manualReplace') and not upgrades.replacement_allowed(m,e,current,row.get('title',''),row.get('quality',{})):raise UserError('Эта раздача больше не улучшает действующие предпочтения')
  sr=m.api('series/'+str(e['seriesId']));sr['_myshowsTitle']=e['seriesTitle'];sr['_airYear']=(e.get('airDate') or '')[:4];profile=m.api('qualityprofile/'+str(sr['qualityProfileId']))
  checked,row=normalize(m,row,profile,'prowlarr',sr)
  if not checked['allowed'] and not e.get('_manualSelection'):raise UserError('Раздача не прошла повторную проверку текущих предпочтений')
 report(m,key,'running','Подходящая раздача найдена: получаем торрент и проверяем файлы серии',episode=e)
 if metainfo is None:
  data,magnet=safe_download(m,row.get('downloadUrl') or row.get('magnetUrl',''))
  if magnet:data=metadata_only(m,magnet)
 else:data=metainfo
 with DOWNLOAD_LOCK:
  check_cancelled(m,key,e['id'])
  managed=read(m.WORKER_STATE/'managed-downloads.json',{})
  for owned in managed.values():
   if any(x['episode']['id']==e['id'] and x['state'] not in ('done','cancelled') for x in owned['episodes']):return {'state':'downloading','message':'Эта серия уже загружается в панели; новая копия не добавлена','hash':owned['hash']}
  mp=rt.media_paths()
  added=m.trpc('torrent-add',{'metainfo':base64.b64encode(data).decode(),'paused':True,'download-dir':mp['downloads']+'/complete/tv/Panel/'+key})
  duplicate=added.get('torrent-duplicate');torrent=added.get('torrent-added') or duplicate
  if not torrent:raise UserError('Transmission не принял торрент')
  h=torrent['hashString'].lower();tid=torrent['id'];old=managed.get(h)
  if e.get('_upgradeFileId') and old and any(x['episode']['id']==e['id'] for x in old['episodes']):raise UserError('Эта копия уже использовалась для серии; выбираем другую раздачу')
  if duplicate and not old:raise UserError('Эта раздача уже есть в Transmission. Её текущие файлы не изменены; выберите другую')
  m.trpc('torrent-stop',{'ids':[tid]})
  info=m.trpc('torrent-get',{'ids':[tid],'fields':['id','hashString','files','fileStats','downloadDir']})['torrents'][0]
  wanted,video=select_files(info['files'],e,row)
  previous=set(old.get('wanted',[])) if old else set()
  if old:
   actual={i for i,s in enumerate(info['fileStats']) if s['wanted']}
   if actual-previous:raise UserError('Выбор файлов этой раздачи изменён вручную; панель не будет его перезаписывать')
  allwanted=sorted(previous|set(wanted))
  fs=os.statvfs(mp['torrent_host']);free=fs.f_bavail*fs.f_frsize;need=sum(info['files'][i]['length']-info['fileStats'][i].get('bytesCompleted',0) for i in allwanted)
  current_jobs=m.trpc('torrent-get',{'fields':['hashString','status','leftUntilDone']})['torrents'];reserved=sum(t.get('leftUntilDone',0) for t in current_jobs if t.get('status') in (3,4) and t.get('hashString','').lower()!=h)
  if need+reserved+2*1024**3>free:raise UserError('Недостаточно места с учётом других загрузок; сохранён резерв 2 ГБ. Торрент не запущен')
  m.trpc('torrent-set',{'ids':[tid],'files-unwanted':list(range(len(info['files'])))})
  m.trpc('torrent-set',{'ids':[tid],'files-wanted':allwanted})
  check=m.trpc('torrent-get',{'ids':[tid],'fields':['fileStats']})['torrents'][0]
  if {i for i,x in enumerate(check['fileStats']) if x['wanted']}!=set(allwanted):raise UserError('Transmission не подтвердил точный выбор файлов; торрент не запущен')
  if current.get('monitored'):m.api('episode/monitor','PUT',{'episodeIds':[e['sonarrId']],'monitored':False})
  owned=old or {'hash':h,'wanted':[],'episodes':[]};owned['wanted']=allwanted
  # The job key is the generation: a record that already finished or was
  # cancelled never blocks a NEW member, so re-downloading the same torrent
  # after cancellation announces again instead of being suppressed forever.
  if not any(x['episode']['id']==e['id'] and x['state'] not in ('done','cancelled') for x in owned['episodes']):
   # announceStart defers the "download started" event until the monitor
   # OBSERVES transfer progress (bytes>0 or rate>0) on the selected files —
   # torrent-start acceptance alone is not a transfer.
   owned['episodes'].append({'job':key,'episode':e,'video':video,'wanted':wanted,'state':'downloading','announceStart':True,'restoreMonitor':e.get('restoreMonitor',current.get('monitored',False)),'quality':row.get('quality'),'title':row.get('title')})
  if not start:owned['startPending']=time.time()
  managed[h]=owned;atomic(m.WORKER_STATE/'managed-downloads.json',managed,0o600)
  if start:m.trpc('torrent-start',{'ids':[tid]})
 return {'state':'downloading','message':'Загружаются только заказанные серии и их субтитры','hash':h}

def schedule_change(m,eid,value):
 with CACHE_LOCK:
  scheduled=read(m.WORKER_STATE/'scheduled-downloads.json',{})
  if value is None:scheduled.pop(str(eid),None)
  else:scheduled[str(eid)]=value
  atomic(m.WORKER_STATE/'scheduled-downloads.json',scheduled,0o600)
  atomic(m.WORKER_STATE/'scheduled-public.json',{k:{'job':v['job'],'notBefore':v.get('notBefore')} for k,v in scheduled.items()})

def check_cancelled(m,key,eid):
 stamp=read(m.WORKER_STATE/'cancelled-episodes.json',{}).get(str(eid),0)
 req=read(m.STATE/'requests'/(key+'.json'),read(m.WORKER_STATE/'upgrade-requests'/(key+'.json'),{})) if stamp else {}
 if stamp and req.get('created',0)<=stamp:raise UserError('Заказ отменён')

def cancel_episode(m,eid):
 with DOWNLOAD_LOCK:
  stamps=read(m.WORKER_STATE/'cancelled-episodes.json',{});stamps[str(eid)]=time.time();atomic(m.WORKER_STATE/'cancelled-episodes.json',stamps,0o600)
  schedule_change(m,eid,None)
  managed=read(m.WORKER_STATE/'managed-downloads.json',{})
  for h,entry in managed.items():
   cancelled=[x for x in entry['episodes'] if x['episode']['id']==eid and x['state'] not in ('done','cancelled')]
   if not cancelled:continue
   # An import already submitted to Sonarr cannot safely be interrupted.
   if any(x.get('command') for x in cancelled):raise UserError('Импорт уже начался; дождитесь добавления в медиатеку')
   keep=set(i for x in entry['episodes'] if x not in cancelled and x['state']!='cancelled' for i in x['wanted'])
   drop=set(i for x in cancelled for i in x['wanted'])-keep
   if drop:m.trpc('torrent-set',{'ids':[h],'files-unwanted':sorted(drop)})
   if not keep:m.trpc('torrent-stop',{'ids':[h]})
   for x in cancelled:x['state']='cancelled';report(m,x['job'],'cancelled','Заказ отменён; скачанные данные сохранены')
   entry['wanted']=sorted(keep)
  atomic(m.WORKER_STATE/'managed-downloads.json',managed,0o600)
  for path in (m.STATE/'requests').glob('*.json'):
   req=read(path,{})
   if req.get('op')=='download' and req.get('episode')==eid and req.get('created',0)<=stamps[str(eid)]:report(m,path.stem,'cancelled','Заказ отменён; скачанные данные сохранены')

def process(m,key,job):
 try:
  report(m,key,'running','Выполняем задание…');op=job['op'];eid=job['episode']
  if op=='refresh':refresh(m);report(m,key,'done','Данные MyShows обновлены');return
  if op=='cancel':cancel_episode(m,eid);report(m,key,'done','Заказ отменён; файлы не удалены');return
  e=episode(m,eid)
  if op=='download':check_cancelled(m,key,eid)
  if op in ('watch_all','watch_season'):
   items=[x for x in released_group(m,eid)['episodes'] if not x.get('future') and (op!='watch_season' or x.get('season')==e.get('season'))]
   completed=[]
   try:
    for item in items:
     current=episode(m,item['id'])
     if current['future']:continue
     v3(m,'manage.CheckEpisode',{'id':item['id']});completed.append(item['id'])
     report(m,key,'running','Отмечено '+str(len(completed))+' из '+str(len(items)),watched=completed)
   except Exception:
    report(m,key,'error','Не все отметки переданы. Обновите список и повторите',watched=completed);return
   refresh(m,True);report(m,key,'done','Отмечены все вышедшие серии',watched=completed);return
  if op in ('search_all','search_season'):
   items=[x for x in released_group(m,eid)['episodes'] if not x.get('future') and (op!='search_season' or x.get('season')==e.get('season'))]
   rows=[];errors=[]
   for season in sorted({x['season'] for x in items}):
    members=[x for x in items if x['season']==season];sample=dict(episode(m,members[0]['id']))
    sample['seasonSearch']=op=='search_season' or len(members)>1;sample['bulkIds']={str(x['id']):x['number'] for x in members}
    child=secrets.token_hex(16)
    def publish_bulk(found,warnings,progress):report(m,key,'running','Поиск сезона '+str(season),rows=rows+found,warnings=errors+warnings,search=progress)
    found,warnings=search(m,child,sample,publish_bulk)
    rows.extend(found);errors.extend(warnings)
    report(m,key,'running','Поиск сезонов: '+str(season),rows=rows,warnings=errors)
   report(m,key,'done','Найдено раздач: '+str(len(rows)),rows=rows,warnings=errors);return
  if op=='watch':
   if e['future']:raise UserError('Эта серия ещё не вышла по данным MyShows')
   v3(m,'manage.CheckEpisode',{'id':eid});refresh(m,True);report(m,key,'done','Отмечено просмотренным в MyShows');return
  if op=='search':
   rows,errors=search(m,key,e);report(m,key,'done','Найдено раздач: '+str(len(rows)),rows=rows,warnings=errors,episode=e);return
  if op=='download':
   if e['future'] and not job.get('selection'):
    schedule_change(m,eid,{'episode':e,'job':key,'last':0,'notBefore':utcdate(e['airDate']),'restoreMonitor':False});report(m,key,'waiting','Запланировано: поиск начнётся после выхода серии '+str(e.get('airDate',''))[:10]);return
   if not e.get('sonarrId'):
    schedule_change(m,eid,{'episode':e,'job':key,'last':time.time(),'restoreMonitor':False});report(m,key,'waiting','Заказ сохранён: ожидаем сопоставления эпизода с медиатекой');return
   current=m.api('episode/'+str(e['sonarrId'])) if e.get('sonarrId') else {}
   scheduled=read(m.WORKER_STATE/'scheduled-downloads.json',{});e['restoreMonitor']=scheduled.get(str(eid),{}).get('restoreMonitor',current.get('monitored',False))
   selection=job.get('selection','')
   if current.get('hasFile') and not selection:
    schedule_change(m,eid,None);report(m,key,'done','Эта серия уже есть в медиатеке');return
   if selection:
    cachekey,number=selection.split(':');cache=read(m.WORKER_STATE/'release-cache'/(cachekey+'.json'),{})
    if (cache.get('episode',{}).get('id')!=eid and str(eid) not in cache.get('episode',{}).get('bulkIds',{})) or cache.get('episode',{}).get('seriesId')!=e['seriesId'] or time.time()-cache.get('created',0)>1800:raise UserError('Результаты поиска устарели; выполните поиск снова')
    item=next((x for x in cache.get('items',[]) if x.get('tag')==number),None)
    if item is None:raise UserError('Раздача не найдена; повторите поиск')
    e['_manualSelection']=True
    if current.get('hasFile'):
     if not current.get('episodeFileId'):raise UserError('Не удалось проверить существующий файл; замена остановлена')
     e['_upgradeFileId']=current['episodeFileId'];e['_manualReplace']=True
    # Re-evaluate profile at click time; title/URL still come only from the trusted server cache.
    sr=m.api('series/'+str(e['seriesId']));sr['_myshowsTitle']=e['seriesTitle'];sr['_airYear']=(e.get('airDate') or '')[:4];profile=m.api('qualityprofile/'+str(sr['qualityProfileId']));display,row=normalize(m,item['row'],profile,'sonarr' if item['row'].get('customFormats') else 'prowlarr',sr);item={'row':row,'display':display}
    status=request_download(m,key,e,item);schedule_change(m,eid,None);report(m,key,**status);return
   rows,errors=search(m,key,e);check_cancelled(m,key,eid);cache=read(m.WORKER_STATE/'release-cache'/(key+'.json'),{})
   candidates=[x for x in cache.get('items',[]) if x['display']['allowed']]
   candidates.sort(key=lambda x:(x['display']['qualityRank'],x['display']['score'],x['display']['seeders']),reverse=True)
   if not candidates:
    summary=waiting_summary(cache.get('items',[]),errors)
    scheduled={'episode':e,'job':key,'last':time.time(),'restoreMonitor':e.get('restoreMonitor',False)}
    if e.get('sonarrId'):m.api('episode/monitor','PUT',{'episodeIds':[e['sonarrId']],'monitored':False})
    schedule_change(m,eid,scheduled);report(m,key,'waiting',summary,searchSummary={'found':len(cache.get('items',[])),'eligible':0,'sourceErrors':len(errors)});return
   failures=[]
   for item in candidates[:8]:
    try:status=request_download(m,key,e,item);schedule_change(m,eid,None);report(m,key,**status);return
    except UserError as exc:failures.append(str(exc))
   raise UserError(failures[-1] if failures else 'Нет подходящих раздач')
 except UserError as exc:report(m,key,'cancelled' if str(exc)=='Заказ отменён' else 'error',str(exc))
 except Exception as exc:logging.warning('Episode operation failed: %s',type(exc).__name__);report(m,key,'error','Сервис временно не выполнил задание. Повторите позже')


def process_batch(m,members):
 pending={}
 for key,job in members:
  try:
   e=episode(m,job['episode']);check_cancelled(m,key,e['id'])
   if e['future'] or not e.get('sonarrId'):process(m,key,job);continue
   current=m.api('episode/'+str(e['sonarrId']))
   if current.get('hasFile'):report(m,key,'done','Эта серия уже есть в медиатеке');continue
   e['restoreMonitor']=current.get('monitored',False);pending[key]=(job,e)
   report(m,key,'running','Ищем общую раздачу сезона для заказанных серий')
  except UserError as exc:report(m,key,'cancelled' if str(exc)=='Заказ отменён' else 'error',str(exc))
  except Exception:report(m,key,'error','Не удалось проверить серию перед заказом')
 if not pending:return
 leader=next(iter(pending));sample=dict(pending[leader][1]);sample['seasonSearch']=True;sample['batchJobs']=[(k,e['id']) for k,(_,e) in pending.items()]
 # Never share searches across series/seasons, even with malformed batch files.
 if any((e['seriesId'],e['season'])!=(sample['seriesId'],sample['season']) for _,e in pending.values()):
  for key,(job,_) in pending.items():process(m,key,job)
  return
 try:
  search(m,leader,sample)
  cache=read(m.WORKER_STATE/'release-cache'/(leader+'.json'),{})
  candidates=[x for x in cache.get('items',[]) if x['display']['allowed'] and (x['row'].get('fullSeason') or len(x['row'].get('episodeNumbers',[]))!=1)]
  candidates.sort(key=lambda x:(x['display']['qualityRank'],x['display']['score'],x['display']['seeders']),reverse=True)
  for item in candidates[:8]:
   if not pending:break
   for key,(_,e) in list(pending.items()):
    try:check_cancelled(m,key,e['id']);report(m,key,'running','Раздача сезона найдена: проверяем файлы заказанных серий')
    except UserError:report(m,key,'cancelled','Заказ отменён');pending.pop(key)
   if not pending:break
   try:
    raw,magnet=safe_download(m,item['row'].get('downloadUrl') or item['row'].get('magnetUrl',''))
    if magnet:raw=metadata_only(m,magnet)
   except Exception:continue
   hashes=set()
   for key,(job,e) in list(pending.items()):
    numbers=item['row'].get('episodeNumbers') or []
    if numbers and not item['row'].get('fullSeason') and e['number'] not in numbers:continue
    try:
     status=request_download(m,key,e,item,metainfo=raw,start=False)
     if status.get('hash'):hashes.add(status['hash'])
     schedule_change(m,e['id'],None);report(m,key,**status);pending.pop(key)
    except UserError as exc:
     if str(exc)=='Заказ отменён':report(m,key,'cancelled',str(exc));pending.pop(key)
    except Exception:logging.warning('Batch episode selection failed')
   # All selected episode files are enabled before starting the shared torrent.
   # Announcements are deferred: each member sets announceStart and the monitor
   # emits only once real transfer progress is observed on that member.
   with DOWNLOAD_LOCK:
    managed=read(m.WORKER_STATE/'managed-downloads.json',{});started=[]
    for h in hashes:
     owned=managed.get(h,{})
     if owned.get('wanted') and any(x['state'] not in ('cancelled','done') for x in owned.get('episodes',[])):
      started.append(h)
    if started:
     for h in started:
      m.trpc('torrent-start',{'ids':[h]})
      managed[h].pop('startPending',None)
     atomic(m.WORKER_STATE/'managed-downloads.json',managed,0o600)
 except UserError as exc:
  if str(exc)=='Заказ отменён':
   for key in pending:report(m,key,'cancelled',str(exc))
   return
 except Exception:logging.warning('Season search failed; using episode fallback')
 # Packs can omit episodes: use the normal individual policy only for leftovers.
 for key,(job,e) in pending.items():process(m,key,job)

def monitor(m):
 with DOWNLOAD_LOCK:
  managed=read(m.WORKER_STATE/'managed-downloads.json',{})
  if not managed:return
  torrents=m.trpc('torrent-get',{'fields':['id','hashString','files','fileStats','downloadDir','error','errorString','rateDownload','eta','status']})['torrents'];lookup={x['hashString'].lower():x for x in torrents}
  changed=False
  for h,entry in managed.items():
   t=lookup.get(h)
   if not t:continue
   # Resume only an interrupted panel batch, never a torrent paused by the user.
   if entry.get('startPending') and time.time()-entry['startPending']>=180 and not t.get('error') and entry.get('wanted') and any(x.get('state')=='downloading' for x in entry.get('episodes',[])):
    if t.get('status')==0:m.trpc('torrent-start',{'ids':[h]})
    entry.pop('startPending',None);changed=True
   for requested in entry['episodes']:
    if requested['state'] in ('done','cancelled'):continue
    e=requested['episode'];key=requested['job']
    if t.get('error'):report(m,key,'paused','Transmission сообщил ошибку загрузки; данные не удалены');continue
    if requested.get('command'):
     actual=m.api('episode/'+str(e['sonarrId']))
     # Sonarr can fail in its tracked-download post-processing after a successful copy.
     # The episode's registered file is authoritative, not the command's final status.
     if actual.get('hasFile') and actual.get('episodeFileId') and actual.get('episodeFileId')!=e.get('_upgradeFileId') and (actual.get('episodeFile') or {}).get('size',0)>0:
      requested['state']='done';requested['importedFileId']=actual['episodeFileId'];changed=True;report(m,key,'done','Серия скачана и добавлена в медиатеку',percent=100)
      notifications.emit_episode('library_ready',e,key)
      # The previous localMedia facts (possibly of a replaced file) must not
      # read as verified until the next refresh re-checks the library.
      notifications.invalidate_local_media([e])
      if requested.get('restoreMonitor'):m.api('episode/monitor','PUT',{'episodeIds':[e['sonarrId']],'monitored':True})
      continue
     command=m.api('command/'+str(requested['command']))
     if command.get('status') in ('failed','completed'):
      if requested.get('importAttempts',0)<3 and time.time()-requested.get('importAt',0)>=60:
       requested.pop('command',None);changed=True
      else:
       report(m,key,'error','Файл скачан, но импорт пока не завершён; исходные данные сохранены')
       continue
     else:continue
    total=sum(t['files'][i]['length'] for i in requested['wanted']);completed=sum(t['fileStats'][i]['bytesCompleted'] for i in requested['wanted'])
    if completed<total:
     speed=t.get('rateDownload',0);eta=round((total-completed)/speed) if speed>0 else None
     report(m,key,'downloading','Загрузка серии' if speed else 'Ожидаем пиров / загрузка приостановлена',percent=round(100*completed/max(1,total),1),rate=speed,eta=eta,downloaded=completed,total=total)
     # The deferred "download started" fires only on OBSERVED transfer
     # progress (bytes>0 or rate>0) for this episode's selected files —
     # queue acceptance alone never announces.
     if requested.get('announceStart') and completed>0:
      requested.pop('announceStart',None)
      changed=True
      notifications.emit_episode('download_started',e,key)
     continue
    # The episode's exact wanted files completed — not a whole season pack.
    # A transfer finished between monitor passes is still an observed start
    # (bytes>0): announce it before the completion event, never both skipped.
    if total<=0:continue
    if requested.pop('announceStart',None):
     notifications.emit_episode('download_started',e,key)
     changed=True
    notifications.emit_episode('torrent_complete',e,key)
    if e.get('_upgradeFileId'):
     if not upgrades.still_unwatched(m,e):
      requested['state']='cancelled';changed=True;report(m,key,'cancelled','Улучшение отменено: серия уже просмотрена');continue
     actual=m.api('episode/'+str(e['sonarrId']))
     if (actual.get('episodeFileId')!=e['_upgradeFileId'] or (not e.get('_manualReplace') and not upgrades.replacement_allowed(m,e,actual,requested.get('title',''),requested.get('quality',{})))):
      requested['state']='cancelled';changed=True;report(m,key,'cancelled','Улучшение отменено: файл серии уже изменился');continue
    f=t['files'][requested['video']];raw=t['downloadDir'].rstrip('/')+'/'+clean_file_path(f['name'])
    mp=rt.media_paths()
    if not raw.startswith(mp['downloads']+'/complete/tv/Panel/'):report(m,key,'error','Папка загрузки изменилась; импорт остановлен');continue
    path=mp['sonarr_torrents']+'/'+raw.removeprefix(mp['downloads']+'/');preview=m.api('manualimport?folder='+urllib.parse.quote(path.rsplit('/',1)[0])+'&filterExistingFiles=false');resource=next((x for x in preview if x.get('path')==path),None)
    if not resource:report(m,key,'error','Файл скачан, но Sonarr не распознал его для импорта');continue
    if e.get('_manualReplace') and not requested.get('backupMade'):
     try:
      preserve_replaced_file(m,actual,e);requested['backupMade']=True;atomic(m.WORKER_STATE/'managed-downloads.json',managed,0o600)
     except UserError as exc:report(m,key,'error',str(exc));continue
    imported={'path':path,'seriesId':e['seriesId'],'episodeIds':[e['sonarrId']],'quality':requested['quality'] if e.get('_upgradeFileId') else resource.get('quality') or requested['quality'],'languages':resource.get('languages') or ([] if e.get('_manualSelection') else [{'id':11,'name':'Russian'}]),'releaseGroup':resource.get('releaseGroup') or '', 'indexerFlags':0}
    command=m.api('command','POST',{'name':'ManualImport','files':[imported],'importMode':'copy'});requested['command']=command['id'];requested['importAttempts']=requested.get('importAttempts',0)+1;requested['importAt']=time.time();requested['state']='importing';changed=True;report(m,key,'importing','Серия скачана. Добавляем в Jellyfin через Sonarr')
  if changed:atomic(m.WORKER_STATE/'managed-downloads.json',managed,0o600)

def run(m):
 # The journal (epoch/cursor) must exist before any worker activity so native
 # clients can register against a stable cursor immediately; initialization
 # never requires upstream authentication and never raises into the loop.
 notifications.journal_init()
 for path in (m.STATE/'requests').glob('*.json'):
  status=m.WORKER_STATE/'jobs'/path.name
  if read(status,{}).get('state') in ('queued','running'):status.unlink(missing_ok=True)
 active={};last_refresh=0;last_monitor=0;last_retry=0;last_daily=0;last_observe=0;last_order=0;last_merge=time.time();merge_pool=concurrent.futures.ThreadPoolExecutor(max_workers=1);merge_future=None
 while True:
  now=time.time()
  if now-last_order>=30:
   studio_order.reconcile(m);last_order=now
  for key,future in list(active.items()):
   if future.done():
    try:future.result()
    except Exception:logging.warning('Episode worker task failed')
    active.pop(key,None)
  if now-last_merge>=60 and (merge_future is None or merge_future.done()):
   merge_future=merge_pool.submit(tracker_merge.run_one,sys.modules[__name__],m);last_merge=now
  # Bounded observer for downloads the panel did not start (Sonarr automatic
  # search / lists / Jellyseerr): read-only Sonarr queue pass, its own slot.
  if now-last_observe>=60 and 'notifications-observe' not in active and len(set(active.values()))<2:
   active['notifications-observe']=POOL.submit(notifications.observe_sonarr,m);last_observe=now
  if now-last_daily>=60 and len(set(active.values()))<2:
   if 'daily-upgrades' not in active and upgrades.due(read(m.WORKER_STATE/'daily-upgrades.json',{})):
    active['daily-upgrades']=POOL.submit(upgrades.run_daily,m,sys.modules[__name__])
   last_daily=now
  if now-last_refresh>=schedule_settings.current()['sync_minutes']*60 and len(set(active.values()))<2:
   active['refresh']=POOL.submit(refresh,m);last_refresh=now
  for path in sorted((m.STATE/'requests').glob('*.json')):
   key=path.stem
   if not re.fullmatch(r'[0-9a-f]{32}',key) or key in active:continue
   if (m.WORKER_STATE/'jobs'/path.name).exists():continue
   job=read(path,{})
   if set(job)-{'op','episode','selection','created','batch'} or not {'op','episode','selection','created'}<=set(job) or ('batch' in job and (not isinstance(job['batch'],str) or not re.fullmatch(r'[0-9a-f]{32}-[0-9]{1,3}',job['batch']) or job.get('op')!='download' or job.get('selection'))) or job['op'] not in ('refresh','watch','search','download','cancel','watch_all','search_all','watch_season','search_season') or not isinstance(job['episode'],int) or not isinstance(job['selection'],str):report(m,key,'error','Некорректное задание');continue
   if job['selection'] and not re.fullmatch(r'[0-9a-f]{32}:[0-9a-f]{20}',job['selection']):report(m,key,'error','Некорректная раздача');continue
   if job.get('batch') and not (m.STATE/'requests'/(job['batch'].rsplit('-',1)[0]+'.ready')).exists() and now-job['created']<60:continue
   cached=read(m.WORKER_STATE/'myshows-episodes.json',{}).get('episodes',{}).get(str(job['episode']),{})
   quick=job['op']=='cancel' or (job['op']=='download' and not job['selection'] and (utcdate(cached.get('airDate')) or 0)>now)
   if quick:process(m,key,job)
   elif len(set(active.values()))<2:
    members=[]
    if job.get('batch'):
     for other in (m.STATE/'requests').glob('*.json'):
      child=read(other,{})
      if child.get('batch')==job['batch'] and child.get('op')=='download' and not child.get('selection') and set(child)=={'op','episode','selection','created','batch'} and isinstance(child.get('episode'),int) and other.stem not in active and not (m.WORKER_STATE/'jobs'/other.name).exists():members.append((other.stem,child))
    if members:
     future=POOL.submit(process_batch,m,members)
     for child,_ in members:active[child]=future
    else:active[key]=POOL.submit(process,m,key,job)
  if now-last_monitor>=15:
   try:monitor(m)
   except Exception as exc:logging.warning('Download monitor failed: %s',type(exc).__name__)
   last_monitor=now
  if now-last_retry>=60 and len(set(active.values()))<2:
   with CACHE_LOCK:
    scheduled=read(m.WORKER_STATE/'scheduled-downloads.json',{})
    for eid,item in scheduled.items():
     if now>=item.get('notBefore',0) and now-item['last']>=900 and item['job'] not in active and len(set(active.values()))<2:
      active[item['job']]=POOL.submit(process,m,item['job'],{'op':'download','episode':int(eid),'selection':''});item['last']=now
    if scheduled:atomic(m.WORKER_STATE/'scheduled-downloads.json',scheduled,0o600)
    last_retry=now
  if int(now)%300<2:
   for p in (m.WORKER_STATE/'release-cache').glob('*.json'):
    if now-p.stat().st_mtime>3600:p.unlink(missing_ok=True)
  time.sleep(1)
