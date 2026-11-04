"""Bounded HTTP, persistent throttling and revocable owner sessions. No upstream secrets."""
import os,base64,hashlib,hmac,http.client,ipaddress,json,logging,re,secrets,sqlite3,threading,time
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from http.cookies import SimpleCookie
from urllib.parse import urlsplit
import runtime as rt
def _security_config():
 cfg=rt.load_json(rt.config_root()/'security.json',required=True)
 cfg.setdefault('trusted_proxies',[]);cfg.setdefault('allowed_networks',['127.0.0.1/32','::1/128'])
 cfg.setdefault('allowed_origins',[]);cfg.setdefault('public_hosts',[])
 cfg.setdefault('owner_names',[]);cfg.setdefault('owner_ids',[])
 return cfg
CONFIG=_security_config()
DB=rt.state_root()/'security.sqlite3';GUARD=threading.RLock()
KEY=bytes.fromhex(CONFIG['signing_key'])
NATIVE_COOKIE_AGE=31536000
DEVICE_TTL=180*86400;DEVICE_MAX=8;DEVICE_TOKEN_AGE=None

def native_family(agent):
 match=re.search(r'(?:^| )DubsNative/[0-9.]+ (Android|iOS)(?: |$)',agent or '')
 if not match:return ''
 return 'native:'+match[1]+(':TV' if re.search(r'(?:^| )DubsTV/[0-9.]+(?: |$)',agent) else '')

def agent_binding(agent,persistent=False):return native_family(agent) if persistent else agent[:512]
def cookie_age(agent):return NATIVE_COOKIE_AGE if native_family(agent) else 28800
def connect():
 c=sqlite3.connect(DB,timeout=5);c.execute('PRAGMA journal_mode=WAL');c.execute('CREATE TABLE IF NOT EXISTS rate (bucket TEXT, k TEXT, at REAL)');c.execute('CREATE INDEX IF NOT EXISTS rate_idx ON rate(bucket,k,at)');c.execute('CREATE TABLE IF NOT EXISTS sessions (sid TEXT PRIMARY KEY, user TEXT, created REAL, seen REAL, csrf TEXT, agent TEXT)');c.execute('CREATE TABLE IF NOT EXISTS nonces (nonce TEXT PRIMARY KEY, at REAL)')
 os.chmod(DB,0o600)
 c.execute('CREATE TABLE IF NOT EXISTS devices (device_id TEXT PRIMARY KEY, user TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE, platform TEXT NOT NULL, created REAL NOT NULL, last_seen REAL NOT NULL, expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0)')
 if 'persistent' not in [r[1] for r in c.execute('PRAGMA table_info(sessions)')]:c.execute('ALTER TABLE sessions ADD COLUMN persistent INTEGER NOT NULL DEFAULT 0');c.commit()
 return c

def hashed(s):return hmac.new(KEY,s.encode(),hashlib.sha256).hexdigest()
def consume(limits):
 now=time.time()
 with GUARD,connect() as c:
  c.execute('BEGIN IMMEDIATE');c.execute('DELETE FROM rate WHERE at<?',(now-900,));c.execute('DELETE FROM nonces WHERE at<?',(now-600,))
  for bucket,key,limit,window in limits:
   n=c.execute('SELECT COUNT(*) FROM rate WHERE bucket=? AND k=? AND at>?',(bucket,hashed(key),now-window)).fetchone()[0]
   if n>=limit:return False
  for bucket,key,limit,window in limits:c.execute('INSERT INTO rate VALUES(?,?,?)',(bucket,hashed(key),now))
 return True

def nonce():
 payload=str(int(time.time()))+'.'+secrets.token_urlsafe(24)
 return payload+'.'+hashed(payload)
def use_nonce(value,cookie):
 if not isinstance(value,str) or not re.fullmatch(r'[0-9]{10,12}\.[A-Za-z0-9_-]{32}\.[0-9a-f]{64}',value) or not isinstance(cookie,str) or not cookie.isascii() or not hmac.compare_digest(value,cookie):return False
 try:stamp,random,signature=value.split('.');valid=0<=time.time()-int(stamp)<=600 and hmac.compare_digest(signature,hashed(stamp+'.'+random))
 except (ValueError,TypeError):return False
 if not valid:return False
 with GUARD,connect() as c:
  try:c.execute('INSERT INTO nonces VALUES(?,?)',(hashed(value),time.time()))
  except sqlite3.IntegrityError:return False
 return True

def new_session(userid,agent,previous=None):
 sid=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32);now=time.time()
 with GUARD,connect() as c:
  c.execute('DELETE FROM sessions WHERE persistent=0 AND (created<? OR seen<?)',(now-28800,now-1800))
  if previous:c.execute('DELETE FROM sessions WHERE sid=?',(hashed(previous),))
  if c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]>=32:c.execute('DELETE FROM sessions WHERE sid=(SELECT sid FROM sessions ORDER BY seen LIMIT 1)')
  persistent=bool(native_family(agent))
  c.execute('INSERT INTO sessions(sid,user,created,seen,csrf,agent,persistent) VALUES(?,?,?,?,?,?,?)',(hashed(sid),userid,now,now,csrf,hashed(agent_binding(agent,persistent)),int(persistent)))
 return sid,csrf

def session(sid,agent):
 if not sid or not re.fullmatch(r'[A-Za-z0-9_-]{43}',sid):return None
 now=time.time()
 with GUARD,connect() as c:
  row=c.execute('SELECT user,created,seen,csrf,agent,persistent FROM sessions WHERE sid=?',(hashed(sid),)).fetchone()
  if not row:return None
  persistent=bool(row[5])
  if (not persistent and (now-row[1]>28800 or now-row[2]>1800)) or not hmac.compare_digest(row[4],hashed(agent_binding(agent,persistent))):
   c.execute('DELETE FROM sessions WHERE sid=?',(hashed(sid),));return None
  if not persistent and native_family(agent):
   persistent=True;c.execute('UPDATE sessions SET persistent=1,agent=? WHERE sid=?',(hashed(agent_binding(agent,True)),hashed(sid)))
  c.execute('UPDATE sessions SET seen=? WHERE sid=?',(now,hashed(sid)))
 return {'user':row[0],'csrf':row[3],'persistent':persistent}
def logout(sid):
 if sid:
  with GUARD,connect() as c:c.execute('DELETE FROM sessions WHERE sid=?',(hashed(sid),))
def cookies(handler,name):
 try:
  c=SimpleCookie();c.load(handler.headers.get('Cookie',''));return c[name].value if name in c else None
 except Exception:return None

# --- Notification device tokens: opaque, hashed at rest, feed-read scope ONLY.
# The raw token is returned exactly once at registration and never logged.
def register_device(user,platform):
 token=secrets.token_urlsafe(32);now=time.time()
 with GUARD,connect() as c:
  c.execute('DELETE FROM devices WHERE revoked=1 OR expires<?',(now,))
  rows=c.execute('SELECT device_id FROM devices WHERE user=? AND revoked=0 ORDER BY last_seen DESC',(user,)).fetchall()
  for old in rows[DEVICE_MAX-1:]:c.execute('UPDATE devices SET revoked=1 WHERE device_id=?',(old[0],))
  device_id=secrets.token_hex(16)
  c.execute('INSERT INTO devices(device_id,user,token_hash,platform,created,last_seen,expires,revoked) VALUES(?,?,?,?,?,?,?,0)',
            (device_id,user,hashed(token),platform,now,now,now+DEVICE_TTL))
 return device_id,token

def device_token_auth(token):
 """Verify a scoped feed-read token; anything else (sessions, CSRF, POSTs)
 never consults this, so the token cannot authenticate any other route."""
 if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}',token):return None
 now=time.time()
 with GUARD,connect() as c:
  row=c.execute('SELECT device_id,user,platform,expires,revoked FROM devices WHERE token_hash=?',(hashed(token),)).fetchone()
  if not row or row[4] or row[3]<now:return None
  c.execute('UPDATE devices SET last_seen=? WHERE device_id=?',(now,row[0]))
 return {'deviceId':row[0],'user':row[1],'platform':row[2]}

def device_rows(user):
 with GUARD,connect() as c:
  rows=c.execute('SELECT device_id,platform,last_seen FROM devices WHERE user=? AND revoked=0 ORDER BY last_seen DESC',(user,)).fetchall()
 return [{'deviceId':r[0],'platform':r[1],'lastSeen':float(r[2])} for r in rows]

def revoke_device(user,device_id):
 if not isinstance(device_id,str) or not re.fullmatch(r'[0-9a-f]{32}',device_id):return False
 with GUARD,connect() as c:
  return c.execute('UPDATE devices SET revoked=1 WHERE device_id=? AND user=? AND revoked=0',(device_id,user)).rowcount==1

def canonical_origin(origin):
 try:
  u=urlsplit(origin)
  if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.path not in ('','/') or u.query or u.fragment:return None
  port=u.port
  return u.scheme+'://'+u.hostname.lower()+(':'+str(port) if port and port!=({'https':443,'http':80}[u.scheme]) else '')
 except (ValueError,TypeError):return None

def context(handler):
 peer=ipaddress.ip_address(handler.client_address[0]);trusted=str(peer) in CONFIG['trusted_proxies']
 if not any(peer in ipaddress.ip_network(n) for n in CONFIG['allowed_networks']):return None
 hosts=handler.headers.get_all('Host',[])
 if len(hosts)!=1 or re.search(r'[\s/@\\#?]',hosts[0]):return None
 host=hosts[0].lower();proto='http';client=str(peer)
 if trusted:
  values=[]
  for name in ['X-Forwarded-Proto','X-Forwarded-Scheme']:
   h=handler.headers.get_all(name,[])
   if len(h)>1:return None
   if h:values.append(h[0])
  if not values or any(v not in ('http','https') for v in values) or len(set(values))!=1:return None
  proto=values[0]
  real=handler.headers.get_all('X-Real-IP',[])
  if len(real)!=1:return None
  try:client=str(ipaddress.ip_address(real[0]))
  except ValueError:return None
 origin=canonical_origin(proto+'://'+host)
 if origin not in CONFIG['allowed_origins']:return None
 if host.removesuffix(':443') in CONFIG['public_hosts'] and proto!='https':return None
 return {'origin':origin,'secure':proto=='https','client':client}
class HeaderReader:
 def __init__(self,reader):self.reader=reader;self.remaining=16384
 def readline(self,size=-1):
  line=self.reader.readline(min(size if size>=0 else 16385,self.remaining+1));self.remaining-=len(line)
  if self.remaining<0:raise http.client.LineTooLong('headers')
  return line
class HardenedHandler(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.0'
 def send_response(self,code,message=None):
  self.log_request(code);self.send_response_only(code,message);self.send_header('Date',self.date_time_string())
 def end_headers(self):
  self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY');self.send_header('Referrer-Policy','same-origin');self.send_header('Permissions-Policy','camera=(), microphone=(), geolocation=(), payment=(), usb=()');self.send_header('Cross-Origin-Opener-Policy','same-origin');self.send_header('Cross-Origin-Resource-Policy','same-origin');self.send_header('X-Robots-Tag','noindex, nofollow, noarchive');self.send_header('Cache-Control','no-store')
  self.send_header('Content-Security-Policy',getattr(self,'csp',"default-src 'none'; frame-ancestors 'none'; base-uri 'none'"))
  if (getattr(self,'security_context',None) or {}).get('secure'):self.send_header('Strict-Transport-Security','max-age=31536000')
  super().end_headers()
 def send_error(self,code,message=None,explain=None):
  if code==403:
   logging.warning('Request denied: method=%s context=%s host=%r origin=%r fetch_site=%r proto=%r scheme=%r',getattr(self,'command',None),getattr(self,'security_context',None) is not None,*[self.headers.get(k,'')[:160] for k in ('Host','Origin','Sec-Fetch-Site','X-Forwarded-Proto','X-Forwarded-Scheme')])
  data=('<!doctype html><meta charset="utf-8"><title>Запрос отклонён</title><p>Запрос отклонён ('+str(code)+').</p>').encode();self.send_response(code);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(data)));self.send_header('Connection','close')
  if code==429:self.send_header('Retry-After','900')
  self.end_headers()
  if getattr(self,'command',None)!='HEAD':
   try:self.wfile.write(data)
   except (BrokenPipeError,ConnectionResetError):pass
  self.close_connection=True
 def parse_request(self):
  if len(self.raw_requestline)>4096:self.request_version='HTTP/1.0';self.command=None;self.requestline='';self.send_error(414);return False
  original=self.rfile;self.rfile=HeaderReader(original)
  try:ok=super().parse_request()
  finally:self.rfile=original
  if not ok:return False
  self.security_context=context(self)
  if self.security_context is None:self.send_error(403);return False
  if self.command not in ('GET','POST'):self.send_error(405);return False
  # Query strings are allowed only for bounded allowlisted routes (the
  # notification feed/history paging contract); fragments stay rejected and
  # every route validates its own parameters strictly.
  if not self.path.startswith('/') or self.path.startswith('//') or len(self.path)>2048 or '#' in self.path:self.send_error(400);return False
  if self.headers.get_all('Transfer-Encoding'):self.send_error(400);return False
  if not consume([('request-ip',self.security_context['client'],300,60),('request-global','all',1500,60)]):self.send_error(429);return False
  return True
 def check_post(self):
  # Safari may label a same-origin POST cross-site after external navigation.
  # Exact Origin and the CSRF / single-use nonce remain mandatory.
  origins=self.headers.get_all('Origin',[])
  if len(origins)!=1 or canonical_origin(origins[0])!=self.security_context['origin']:self.send_error(403);return False
  lengths=self.headers.get_all('Content-Length',[])
  if len(lengths)!=1 or not re.fullmatch(r'[0-9]{1,5}',lengths[0]):self.send_error(400);return False
  if not 0<int(lengths[0])<=16384:self.send_error(413);return False
  types=self.headers.get_all('Content-Type',[])
  if len(types)!=1 or types[0].split(';',1)[0].strip().lower()!='application/x-www-form-urlencoded':self.send_error(415);return False
  return True
 def cookie(self,name,value,age):
  return name+'='+value+'; HttpOnly; SameSite=Strict; Path=/; Max-Age='+str(age)+('; Secure' if self.security_context['secure'] else '')
class BoundedServer(ThreadingHTTPServer):
 daemon_threads=True;request_queue_size=32
 def __init__(self,*args,**kwargs):self.slots=threading.BoundedSemaphore(24);super().__init__(*args,**kwargs)
 def get_request(self):
  sock,addr=super().get_request();sock.settimeout(8);return sock,addr
 def process_request(self,request,client_address):
  if not self.slots.acquire(blocking=False):
   try:request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
   except OSError:pass
   self.shutdown_request(request);return
  try:super().process_request(request,client_address)
  except Exception:self.slots.release();raise
 def process_request_thread(self,*args,**kwargs):
  try:super().process_request_thread(*args,**kwargs)
  finally:self.slots.release()
 def handle_error(self,request,client_address):pass
