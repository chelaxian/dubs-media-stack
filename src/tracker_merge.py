"""Conservatively discover trackers for identical public torrents already in Transmission."""
import base64,concurrent.futures,hashlib,ipaddress,logging,re,time,urllib.parse

# Fixed allowlist: tracker metainfo and search results are untrusted. This also
# prevents the torrent client from being directed at internal HTTP services.
TRACKER_DOMAINS={'tracker.opentrackr.org','open.demonii.com','open.stealth.si','tracker.torrent.eu.org','tracker.bt4g.com','tracker.qu.ax','tracker.onetracker.net','tracker.pmman.tech','tr3.torrent4me.com','tr3.tor4me.info','tr3.tor2me.info','tr3.zalx2me.com','tr3.zalx4you.com','tr3.kinox2me.com','tr3.kinox4you.com','tr3.zal-4you.com','tr3.zal-2me.com','opentor.net','tracker.theoks.net','tracker.srv00.com','tracker.corpscorp.online','vito-tracker.space','vito-tracker.duckdns.org'}

def safe_tracker(url):
 try:
  u=urllib.parse.urlsplit(url);host=(u.hostname or '').lower().rstrip('.')
  if u.scheme not in ('http','https','udp') or not host or u.username or u.password or host not in TRACKER_DOMAINS:return False
  try:
   if not ipaddress.ip_address(host).is_global:return False
  except ValueError:pass
  return bool(u.port is None or 1<=u.port<=65535)
 except (ValueError,TypeError):return False

def info_slice(data):
 # Hash the exact on-wire bencoded info dictionary, not a reconstructed value.
 def end(pos,depth=0):
  if depth>32 or pos>=len(data):raise ValueError('bad metainfo')
  c=data[pos:pos+1]
  if c==b'i':return data.index(b'e',pos)+1
  if c in (b'l',b'd'):
   pos+=1
   while data[pos:pos+1]!=b'e':pos=end(pos,depth+1)
   return pos+1
  colon=data.index(b':',pos);n=int(data[pos:colon]);finish=colon+1+n
  if n<0 or finish>len(data):raise ValueError('bad string')
  return finish
 if data[:1]!=b'd':raise ValueError('bad dictionary')
 pos=1
 while pos<len(data) and data[pos:pos+1]!=b'e':
  start=pos;pos=end(pos);key=data[start:pos];value=pos;pos=end(pos)
  if key==b'4:info':return data[value:pos]
 raise ValueError('missing info')

def source_metadata(ep,m,row):
 data,magnet=ep.safe_download(m,row.get('downloadUrl') or row.get('magnetUrl',''))
 if magnet:
  q=urllib.parse.parse_qs(urllib.parse.urlsplit(magnet).query);v=q['xt'][0].rsplit(':',1)[-1]
  return (v.lower() if len(v)==40 else base64.b32decode(v.upper()).hex()),None,q.get('tr',[])
 meta=ep.validate_metainfo(data);info=meta[b'info'];trackers=[meta.get(b'announce',b'')]+[t for tier in meta.get(b'announce-list',[]) for t in tier]
 return hashlib.sha1(info_slice(data)).hexdigest(),bool(info.get(b'private')), [t.decode('utf-8') for t in trackers if t]

def query_for(name):
 name=re.sub(r'(?i)\b(?:s\d{1,2}(?:e\d+)?|season\s*\d+|\d{3,4}p|web[-. ]?dl|webrip|h\.?26[45])\b.*','',name)
 name=re.sub(r'[._()\-]+',' ',name).strip()
 return ' '.join(name.split()[:7])[:100]

def merge(ep,m,torrent,rows):
 h=torrent['hashString'].lower()
 if torrent.get('isPrivate') or not re.fullmatch(r'[0-9a-f]{40}',h):return 0
 existing={x['announce'] for x in torrent.get('trackers',[])};added=[]
 matches=[row for row in rows[:500] if not (row.get('infoHash') or '') or (row.get('infoHash') or '').lower()==h]
 matches.sort(key=lambda row:0 if (row.get('infoHash') or '').lower()==h else 1)
 pool=concurrent.futures.ThreadPoolExecutor(max_workers=6)
 futures=[pool.submit(source_metadata,ep,m,row) for row in matches[:12]]
 try:
  for future in concurrent.futures.as_completed(futures,timeout=75):
   try:actual,private,trackers=future.result()
   except Exception:continue
   if actual!=h or private is True:continue
   for tr in trackers:
    if safe_tracker(tr) and tr not in existing:
     existing.add(tr);added.append(tr)
   if len(added)>=40:break
 except concurrent.futures.TimeoutError:pass
 finally:pool.shutdown(wait=False,cancel_futures=True)
 if added:
  m.trpc('torrent-set',{'ids':[h],'trackerAdd':added[:40]})
  if torrent.get('status') not in (0,):m.trpc('torrent-reannounce',{'ids':[h]})
 return len(added[:40])

def run_one(ep,m):
 """One bounded pass, including manually added downloads; never starts/stops them."""
 state=ep.read(m.WORKER_STATE/'tracker-merge.json',{});now=time.time()
 torrents=m.trpc('torrent-get',{'fields':['hashString','name','status','isPrivate','trackers']})['torrents']
 eligible=[t for t in torrents if not t.get('isPrivate') and now-state.get(t.get('hashString','').lower(),0)>=21600]
 if not eligible:return
 eligible.sort(key=lambda t:(t.get('status')!=4,state.get(t['hashString'].lower(),0)));t=eligible[0];h=t['hashString'].lower()
 query=query_for(t.get('name',''))
 if len(query)<3:return
 try:
  sources=[x for x in ep.indexer_api(m,'indexer') if x.get('enable',True)][:40]
  rows=[];successes=0
  if not sources:raise RuntimeError('no active indexers')
  def fetch(source):
   return ep.indexer_api(m,'search?type=search&indexerIds='+str(int(source['id']))+'&query='+urllib.parse.quote(query))
  with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
   futures=[pool.submit(fetch,x) for x in sources]
   for future in concurrent.futures.as_completed(futures):
    try:rows.extend(future.result()[:100]);successes+=1
    except Exception:continue
  if not successes:raise RuntimeError('indexers unavailable')
  count=merge(ep,m,t,rows)
  logging.info('Public torrent tracker discovery completed: %d sources added',count)
  state[h]=now
 except Exception as exc:
  logging.warning('Public torrent tracker discovery failed: %s',type(exc).__name__);state[h]=now-18000
 ep.atomic(m.WORKER_STATE/'tracker-merge.json',state,0o640)
