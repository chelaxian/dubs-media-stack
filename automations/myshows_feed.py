#!/usr/bin/python3
"""Public MyShows watchlist -> cached Sonarr Custom List. Never downloads media."""
import json,subprocess,time,threading,logging,os,sys
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import requests
SRC=os.environ.get('DUBS_SRC',str(Path(__file__).resolve().parents[1]/'src'))
sys.path.insert(0,SRC)
import runtime as rt
import schedule_settings
STATE=rt.feed_state_root()
lock=threading.Lock()
def save(name,data):
 p=STATE/name;t=p.with_suffix('.tmp');t.write_text(json.dumps(data,ensure_ascii=False,indent=2));os.chmod(t,0o640);os.replace(t,p)
def rpc(method,params):
 cfg=rt.settings()
 cmd=['curl','--fail','-sS','--max-time','35']
 if cfg['proxy']:cmd+=['--socks5-hostname',cfg['proxy']]
 d=subprocess.check_output(cmd+['-H','Content-Type: application/json','--data',json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}),'https://api.myshows.me/v2/rpc/'],timeout=40)
 j=json.loads(d)
 if j.get('error'):raise RuntimeError(str(j['error']))
 return j['result']
def refresh():
 cfg=rt.connection('sonarr');sonarr_base=cfg['base_url'];sonarr_key=cfg['key']
 cachefile=STATE/'mapping.json';cache=json.loads(cachefile.read_text()) if cachefile.exists() else {};result=[];unmatched=[]
 shows=rpc('profile.Shows',{'login':rt.settings()['login']});selected=[s['show'] for s in shows if s.get('watchStatus')=='watching'];save('profile.json',shows)
 for s in selected:
  key=str(s['id']);m=cache.get(key)
  if not m or not m.get('TvdbId'):
   try:
    detail=rpc('shows.GetById',{'showId':s['id'],'withEpisodes':False});time.sleep(2)
    imdb=detail.get('imdbId');imdb=('tt'+str(imdb).removeprefix('tt').zfill(7)) if imdb else None
    term='imdb:'+imdb if imdb else (s.get('titleOriginal') or s['title'])
    r=requests.get(sonarr_base+'/api/v3/series/lookup',params={'term':term},headers={'X-Api-Key':sonarr_key},timeout=30);r.raise_for_status();found=r.json()
    if imdb:found=[x for x in found if x.get('imdbId')==imdb]
    else:found=[x for x in found if x.get('year')==s.get('year') and x.get('title','').casefold()==term.casefold()]
    if len(found)!=1:raise ValueError('no unique verified match')
    x=found[0];m={'TvdbId':x['tvdbId'],'Title':x['title'],'ImdbId':imdb};cache[key]=m;save('mapping.json',cache)
   except Exception as e:
    unmatched.append({'myshowsId':s['id'],'title':s['title'],'reason':str(e)});logging.warning('Unmatched MyShows %s: %s',s['id'],type(e).__name__);continue
  result.append(m)
 if not result and selected:raise RuntimeError('No verified matches; preserving last good list')
 old_path=STATE/'series.json';old_ids={x.get('TvdbId') for x in json.loads(old_path.read_text())} if old_path.exists() else set();save('series.json',result)
 if old_ids!={x.get('TvdbId') for x in result}:
  try:
   r=requests.post(sonarr_base+'/api/v3/command',headers={'X-Api-Key':sonarr_key},json={'name':'ImportListSync'},timeout=15);r.raise_for_status()
  except Exception:logging.exception('Could not trigger Sonarr import; its scheduled sync will retry')
 save('status.json',{'updated':time.strftime('%Y-%m-%dT%H:%M:%S%z'),'login':rt.settings()['login'],'selected':len(selected),'mapped':len(result),'unmatched':unmatched,'direction':'MyShows watching -> Sonarr; future episodes only'});logging.info('Watchlist updated: %d/%d verified shows',len(result),len(selected))
def worker():
 last=0
 while True:
  if time.time()-last>=schedule_settings.current()['sync_minutes']*60:
   try:refresh()
   except Exception:logging.exception('Refresh failed; preserving cached list')
   last=time.time()
  time.sleep(5)
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  # SonarrList requests {baseUrl}/api/v3/series?apikey=... — strip the query.
  path=self.path.split('?',1)[0]
  name={'/series':'series.json','/api/v3/series':'series.json','/health':'status.json'}.get(path)
  p=STATE/name if name else None
  if not p or not p.exists():self.send_error(503 if name else 404);return
  b=p.read_bytes();self.send_response(200);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def log_message(self,*args):pass
def feed_binds():
 """Interfaces to serve the watchlist on: loopback plus, explicitly, the
 Docker bridge gateway IPs so containerized Sonarr can reach the host feed.
 Wildcard binds are refused — the feed exposes a personal watchlist."""
 raw=os.environ.get('DUBS_FEED_BIND','127.0.0.1')
 hosts=[h.strip() for h in raw.split(',') if h.strip()]
 if any(h in ('0.0.0.0','::','*') for h in hosts):
  raise SystemExit('DUBS_FEED_BIND must not contain wildcard binds: the watchlist feed is personal data')
 return hosts
def ensure_startup_cache():
 """Serve a VALID EMPTY feed before the first MyShows refresh completes.

 Sonarr validates the Custom List URL at creation time: a brand-new feed
 must answer /series with [] and /health with 200 immediately, or bootstrap
 wires a connection-refused endpoint. An existing cache is never overwritten.
 """
 STATE.mkdir(parents=True,exist_ok=True)
 series=STATE/'series.json'
 if not series.exists():series.write_text('[]')
 status=STATE/'status.json'
 if not status.exists():
  save('status.json',{'updated':None,'state':'waiting for first MyShows refresh',
                      'selected':0,'mapped':0,'unmatched':[],
                      'direction':'MyShows watching -> Sonarr; future episodes only'})
if __name__=='__main__':
 logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
 ensure_startup_cache()
 threading.Thread(target=worker,daemon=True).start()
 port=int(os.environ.get('DUBS_FEED_PORT','9191'))
 binds=feed_binds()
 servers=[ThreadingHTTPServer((host,port),Handler) for host in binds]
 threads=[threading.Thread(target=s.serve_forever,daemon=True) for s in servers]
 for t in threads:t.start()
 logging.info('Feed serving on %s:%d',','.join(binds),port)
 threads[0].join()
