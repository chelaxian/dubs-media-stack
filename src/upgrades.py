"""Daily, non-destructive upgrades of downloaded, MyShows-unwatched episodes."""
import datetime,re,secrets,time,logging
from zoneinfo import ZoneInfo
import runtime as rt

def _tz():
 try:return ZoneInfo(rt.timezone_name())
 except Exception:return ZoneInfo('Europe/Moscow')
TZ=_tz()

def due(state,now=None):
 hour=rt.settings()['daily_upgrade_hour']
 now=now or datetime.datetime.now(TZ)
 return now.hour>=hour and state.get('day')!=now.date().isoformat()

def voice_rank(m,sid,title,formats=()):
 prefs=m.load('preferences.json',{}).get(str(sid),{})
 order=list(range(len(m.STUDIOS)))
 selected=rt.resolve_studio(m.STUDIOS,prefs.get('studio'))
 if selected is not None:order.remove(selected);order.insert(0,selected)
 names={x.get('name','').split(' — ',1)[-1] for x in formats}
 hits=[i for i in order if i!=len(m.STUDIOS)-1 and re.search(m.STUDIOS[i]['pattern'],title) or m.STUDIOS[i]['name'] in names]
 # The 'other Russian' studio can itself be a selected first choice.
 other=len(m.STUDIOS)-1
 if not hits:hits=[other]
 if prefs.get('mode')=='strict' and selected is not None and selected not in hits:return None
 return min(order.index(i) for i in hits)

def resolution(quality):
 q=quality.get('quality',quality) or {}
 return q.get('resolution') or (2160 if '2160' in q.get('name','') else 1080 if '1080' in q.get('name','') else 0)

def better(candidate,current):
 # Improve at least one dimension, never trade a better voice for more pixels.
 return candidate[0]>=current[0] and candidate[1]<=current[1] and candidate!=current

def current_rank(m,e,current):
 ef=current.get('episodeFile') or m.api('episodefile/'+str(current['episodeFileId']))
 title=ef.get('originalFilePath') or ef.get('sceneName') or ef.get('relativePath','')
 history=m.api('history/series?seriesId='+str(e['seriesId']))
 imported=next((x for x in history if x.get('eventType')=='downloadFolderImported' and str(x.get('data',{}).get('fileId'))==str(current['episodeFileId'])),None)
 if imported:
  title+=' '+imported.get('sourceTitle','')
  download_id=imported.get('downloadId')
  if download_id:
   for record in history:
    if record.get('eventType')=='grabbed' and record.get('downloadId')==download_id:title+=' '+record.get('sourceTitle','')
  # Manual imports lose the release title in Sonarr; recover only from our
  # exact recorded torrent source directory for THIS registered file import.
  import episodes as ep
  managed=ep.read(m.WORKER_STATE/'managed-downloads.json',{})
  dropped=imported.get('data',{}).get('droppedPath','')
  for owned in managed.values():
   members=owned.get('episodes',[])
   root=members[0].get('job') if members else None
   for member in members:
    if member['episode'].get('sonarrId')!=e['sonarrId'] or member.get('state')!='done':continue
    if member.get('importedFileId')==current['episodeFileId'] or (root and '/Panel/'+root+'/' in dropped):title+=' '+member.get('title','')

 return resolution(ef.get('quality',{})),voice_rank(m,e['seriesId'],title,ef.get('customFormats',[]))

def still_unwatched(m,e):
 token=m.load('myshows-auth.json',{}).get('token')
 if not token:raise RuntimeError('MyShows authentication missing')
 watched=m.myrpc('profile.Episodes',{'showId':e['showId']},token)
 detail=m.myrpc('shows.GetById',{'showId':e['showId'],'withEpisodes':True},token)
 rows=[x for x in detail.get('episodes',[]) if x.get('id')==e['id']]
 if len(rows)!=1 or (rows[0].get('seasonNumber'),rows[0].get('episodeNumber'))!=(e['season'],e['number']):raise RuntimeError('Episode identity mismatch')
 return not m.match_watched(detail,watched,[{'id':e['sonarrId'],'seasonNumber':e['season'],'episodeNumber':e['number']}])

def candidate_rank(m,e,item):
 d=item['display']
 return resolution(item['row'].get('quality',{})),voice_rank(m,e['seriesId'],d['title'])

def replacement_allowed(m,e,current,title,quality):
 if current.get('episodeFileId')!=e.get('_upgradeFileId'):return False
 old=current_rank(m,e,current)
 rank=(resolution(quality),voice_rank(m,e['seriesId'],title))
 return rank[1] is not None and (old[1] is None or better(rank,old))

def upgrade_one(m,ep,e):
 current=m.api('episode/'+str(e['sonarrId']))
 if not current.get('hasFile'):return 'missing'
 before=current_rank(m,e,current)
 if before[0]>=2160 and before[1]==0:return 'maximum'
 if not still_unwatched(m,e):return 'watched'
 managed=ep.read(m.WORKER_STATE/'managed-downloads.json',{})
 if any(x['episode']['id']==e['id'] and x['state'] not in ('done','cancelled') for v in managed.values() for x in v['episodes']):return 'busy'
 key=secrets.token_hex(16);e=dict(e,_upgradeFileId=current['episodeFileId'])
 requests=m.WORKER_STATE/'upgrade-requests';requests.mkdir(exist_ok=True)
 ep.atomic(requests/(key+'.json'),{'created':time.time(),'episode':e['id']},0o600)
 ep.search(m,key,e)
 cache=ep.read(m.WORKER_STATE/'release-cache'/(key+'.json'),{})
 candidates=[]
 for item in cache.get('items',[]):
  rank=candidate_rank(m,e,item)
  if not item['display']['allowed'] or rank[1] is None:continue
  if before[1] is None or better(rank,before):candidates.append(item)
 candidates.sort(key=lambda x:(candidate_rank(m,e,x)[0],-candidate_rank(m,e,x)[1],x['display']['seeders']),reverse=True)
 if not candidates:
  ep.report(m,key,'done','Ежедневная проверка: улучшений пока нет');return 'no_better_release'
 for item in candidates[:8]:
  try:
   latest=m.api('episode/'+str(e['sonarrId']))
   if latest.get('episodeFileId')!=e['_upgradeFileId'] or not still_unwatched(m,e):return 'changed'
   status=ep.request_download(m,key,e,item);ep.report(m,key,**status);return 'downloading'
  except ep.UserError:continue
 ep.report(m,key,'error','Ежедневная проверка: подходящие раздачи не удалось запустить; текущая серия сохранена')
 return 'unavailable'

def run_daily(m,ep,dry=False):
 path=m.WORKER_STATE/'daily-upgrades.json';state=ep.read(path,{})
 if not dry and not due(state):return
 ep.refresh(m,True)
 snapshot=ep.read(m.WORKER_STATE/'myshows-episodes.json',{})
 if snapshot.get('error') or time.time()-snapshot.get('updated',0)>300:raise RuntimeError('Fresh MyShows snapshot required')
 day=datetime.datetime.now(TZ).date().isoformat()
 result={'day':day,'started':time.time(),'timezone':str(TZ),'results':[]}
 if not dry:ep.atomic(path,result,0o600)
 for group in snapshot.get('released',[]):
  for e in group['episodes']:
   if not e.get('hasFile') or not e.get('sonarrId') or e.get('future'):continue
   try:
    if dry:
     current=m.api('episode/'+str(e['sonarrId']));rank=current_rank(m,e,current)
     status='maximum' if rank[0]>=2160 and rank[1]==0 else 'needs_daily_check'
    else:status=upgrade_one(m,ep,e)
   except Exception as exc:
    logging.warning('Daily upgrade episode %s: %s',e['id'],type(exc).__name__);status='error_existing_file_preserved'
   result['results'].append({'episode':e['id'],'series':e['seriesTitle'],'season':e['season'],'number':e['number'],'status':status})
   if not dry:ep.atomic(path,result,0o600)
 result['finished']=time.time()
 if not dry:ep.atomic(path,result,0o600)
 return result
