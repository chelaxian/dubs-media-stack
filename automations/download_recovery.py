#!/usr/bin/python3
"""Recover only active Transmission jobs tracked by Sonarr/Radarr. Never delete data."""
import json,time,os,fcntl,logging,re,sys,requests,xml.etree.ElementTree as ET
from pathlib import Path
SRC=os.environ.get('DUBS_SRC',str(Path(__file__).resolve().parents[1]/'src'))
sys.path.insert(0,SRC)
import runtime as rt
STATE=Path(os.environ.get('DUBS_RECOVERY_STATE',str(Path(rt.state_root().parent)/'media-download-recovery'/'state.json')))
logging.basicConfig(level=logging.INFO,format='%(levelname)s %(message)s')
log=logging.getLogger('recovery')

def stalled_action(t,previous,now):
    # Count observed active time only; do not count power-off, timer gaps or manual pauses.
    total=t.get('downloadedEver',0)
    if t['status'] not in (3,4) or t.get('leftUntilDone',0)==0 or t.get('error',0):
        return dict(bytes=total,checked=now,idle=0,announced=False),'observe'
    if not previous or previous.get('bytes')!=total or t.get('rateDownload',0)>0:
        return dict(bytes=total,checked=now,idle=0,announced=False),'observe'
    p=dict(previous);p['idle']=p.get('idle',0)+min(max(now-p.get('checked',now),0),360);p['checked']=now
    if p['idle']>=4500:return p,'replace'
    if p['idle']>=3600 and not p.get('announced'):p['announced']=True;return p,'announce'
    return p,'observe'

def fits(r,scope):
    reasons=r.get('rejections',[])
    blocking=[x for x in reasons if not re.search(r'already.*download|already.*queue|in.*download queue',x,re.I)]
    if blocking or (r.get('rejected') and not reasons):return False
    if scope['app']=='radarr':return r.get('mappedMovieId',scope['media'])==scope['media']
    if r.get('mappedSeriesId')!=scope['media']:return False
    season=r.get('mappedSeasonNumber',r.get('seasonNumber'))
    if season!=scope['season']:return False
    nums=set(r.get('mappedEpisodeNumbers') or r.get('episodeNumbers') or [])
    if r.get('fullSeason'):return scope['full']
    return bool(nums) and nums.issubset(set(scope['numbers']))

class Arr:
    def __init__(self,app):
        cfg=rt.connection(app);self.app=app
        self.base=cfg['base_url'].rstrip('/')+'/api/v3/';self.s=requests.Session();self.s.headers['X-Api-Key']=cfg['key']
    def call(self,path,method='GET',data=None,params=None):
        r=self.s.request(method,self.base+path,json=data,params=params,timeout=240);r.raise_for_status();return r.json() if r.content else None
class Transmission:
    def __init__(self):
        # Reuse configured Sonarr client credentials; no duplicate secret file.
        a=Arr('sonarr');c=next(x for x in a.call('downloadclient') if x['implementation']=='Transmission' and x.get('enable'))
        f={x['name']:x.get('value') for x in c['fields']};self.s=requests.Session()
        credentials=rt.connection('transmission')
        self.s.auth=(credentials['user'],credentials['password'])
        base=credentials['base_url']
        if '/transmission/rpc' in base:self.url=base
        else:self.url=f'http://{f["host"]}:{f["port"]}/transmission/rpc'
    def call(self,method,args):
        payload={'method':method,'arguments':args};r=self.s.post(self.url,json=payload,timeout=25)
        if r.status_code==409:self.s.headers['X-Transmission-Session-Id']=r.headers['X-Transmission-Session-Id'];r=self.s.post(self.url,json=payload,timeout=25)
        r.raise_for_status();x=r.json()
        if x['result']!='success':raise RuntimeError('Transmission RPC failed')
        return x['arguments']

def get_scope(a,rows):
    if a.app=='radarr':
        mid=rows[0].get('movieId')
        if not mid or not a.call(f'movie/{mid}').get('monitored'):return None
        return {'app':a.app,'media':mid,'key':f'radarr:{mid}'}
    sid=rows[0].get('seriesId')
    if not sid or not a.call(f'series/{sid}').get('monitored'):return None
    all_eps=a.call('episode',params={'seriesId':sid});ids={r.get('episodeId') for r in rows}
    selected=[e for e in all_eps if e['id'] in ids and e['monitored'] and not e['hasFile']]
    seasons={e['seasonNumber'] for e in selected}
    # Never replace mixed-season or unknown jobs automatically.
    if len(seasons)!=1 or not selected:return None
    sn=next(iter(seasons));nums=sorted(e['episodeNumber'] for e in selected)
    full=set(nums)=={e['episodeNumber'] for e in all_eps if e['seasonNumber']==sn}
    return {'app':a.app,'media':sid,'season':sn,'numbers':nums,'ids':[e['id'] for e in selected],'full':full,'key':f'sonarr:{sid}:{sn}:'+','.join(map(str,nums))}

def fail_scope(a,scope):
    if a.app=='sonarr':a.call('episode/monitor','PUT',{'episodeIds':scope['ids'],'monitored':False})
    else:
        x=a.call(f'movie/{scope["media"]}');x['monitored']=False;a.call(f'movie/{scope["media"]}','PUT',x)

def replace(a,tr,t,rows,scope,state):
    entry=state['scopes'].setdefault(scope['key'],{'attempted':[]});attempted=set(entry['attempted'])
    # Keep series and exact episode selection. Do not substitute a season pack for one episode.
    params={'movieId':scope['media']} if a.app=='radarr' else ({'episodeId':scope['ids'][0]} if len(scope['ids'])==1 else {'seriesId':scope['media'],'seasonNumber':scope['season']})
    releases=a.call('release',params=params)
    candidates=[r for r in releases if fits(r,scope) and r.get('guid') not in attempted and (r.get('infoHash') or '').lower()!=t['hashString'].lower()]
    candidates.sort(key=lambda r:(r.get('qualityWeight',0),r.get('customFormatScore',0),r.get('seeders',0)),reverse=True)
    def retire():
        tr.call('torrent-stop',{'ids':[t['id']]})
        try:a.call(f'queue/{rows[0]["id"]}','DELETE',params={'removeFromClient':'false','blocklist':'true','skipRedownload':'true'})
        except requests.HTTPError as e:
            if e.response.status_code!=404:raise
    for r in candidates:
        guid=r['guid'];entry['attempted'].append(guid);save(state)
        try:
            data={'guid':guid,'indexerId':r['indexerId']}
            if a.app=='sonarr':data['seriesId']=scope['media']
            before={x['hashString'].lower() for x in tr.call('torrent-get',{'fields':['hashString']})['torrents']}
            a.call('release','POST',data)
            after={x['hashString'].lower() for x in tr.call('torrent-get',{'fields':['hashString']})['torrents']}
            if not after-before:
                log.warning('%s duplicate torrent; trying another release',scope['key']);continue
            # A successful grab is only an attempt; next timer runs must see actual bytes.
            retire()
            entry.update(status='testing_replacement',last_title=r['title'],updated=time.time())
            tr.call('torrent-remove',{'ids':[t['id']],'delete-local-data':False})
            log.info('%s replacement submitted: %s',scope['key'],r['title']);return
        except requests.HTTPError as e:
            if e.response.status_code in (400,422):
                log.warning('%s candidate rejected by application; trying next',scope['key']);continue
            entry['attempted'].remove(guid);entry.update(status='service_error',updated=time.time());save(state);raise
        except requests.RequestException:
            # A transport/server error is not proof a release is dead. Retry safely next run.
            entry['attempted'].remove(guid);entry.update(status='service_error',updated=time.time());save(state);raise
    if any(x.get('source')=='IndexerStatusCheck' for x in a.call('health')):
        entry.update(status='waiting_for_indexers',updated=time.time());log.warning('%s indexers unavailable; exhaustion deferred',scope['key']);return
    retire()
    fail_scope(a,scope)
    tr.call('torrent-set',{'ids':[t['id']],'labels':list(dict.fromkeys(t.get('labels',[])+['recovery-failed']))})
    entry.update(status='exhausted',reason='No remaining eligible releases in current search',updated=time.time())
    log.warning('%s exhausted; torrent paused and monitoring disabled',scope['key'])

def save(state):
    tmp=STATE.with_suffix('.tmp');tmp.write_text(json.dumps(state,ensure_ascii=False,indent=2));os.chmod(tmp,0o600);tmp.replace(STATE)
def main():
    STATE.parent.mkdir(parents=True,exist_ok=True)
    lock=open(STATE.parent/'lock','w')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:return
    ok,reason=rt.storage_ready()
    if not ok:
        log.warning('storage not ready (%s); no replacements attempted',reason)
        return
    state=json.loads(STATE.read_text()) if STATE.exists() else {'torrents':{},'scopes':{}}
    tr=Transmission();ts=tr.call('torrent-get',{'fields':['id','hashString','name','status','downloadedEver','rateDownload','leftUntilDone','error','errorString','downloadDir','labels']})['torrents']
    apps=[Arr('sonarr'),Arr('radarr')];queues={}
    for a in apps:
        for row in a.call('queue',params={'pageSize':1000,'includeUnknownSeriesItems':'false'})['records']:
            h=(row.get('downloadId') or '').lower()
            if h:queues.setdefault((a.app,h),[]).append(row)
    for t in ts:
        h=t['hashString'].lower()
        for a in apps:
            rows=queues.get((a.app,h))
            if not rows:continue
            key=a.app+':'+h;previous=state['torrents'].get(key)
            p,action=stalled_action(t,previous,time.time());state['torrents'][key]=p
            if t.get('error'):log.warning('%s client/storage error; no replacement',t['name'])
            if action=='announce':tr.call('torrent-reannounce',{'ids':[t['id']]});log.info('Reannounced %s',t['name'])
            elif action=='replace':
                scope=get_scope(a,rows)
                if scope:
                    # Explicit disk headroom guard on the host-mapped download directory.
                    mp=rt.media_paths();download_path=t['downloadDir']
                    if download_path==mp['downloads'] or download_path.startswith(mp['downloads']+'/'):
                        download_path=mp['torrent_host']+download_path[len(mp['downloads']):]
                    v=os.statvfs(download_path)
                    if v.f_bavail*v.f_frsize<2*1024**3:log.warning('Low disk space; replacement skipped');continue
                    if state['scopes'].get(scope['key'],{}).get('status')!='exhausted':replace(a,tr,t,rows,scope,state)
            elif t.get('rateDownload',0)>0:log.info('Receiving data: %s (%s bytes/s)',t['name'],t['rateDownload'])
    state['last_check']=time.time();save(state)
if __name__=='__main__':
    try:main()
    except Exception as e:
        # Never log request URLs (API keys) or credentials.
        log.error('Check failed (%s); no exhaustion decision',type(e).__name__);raise SystemExit(1)
