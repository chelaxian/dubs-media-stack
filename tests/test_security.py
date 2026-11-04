import unittest,tempfile,threading,requests,re,copy,socket,time,os,sqlite3
from pathlib import Path
from unittest.mock import patch,Mock
import _dubs_env
from _dubs_env import load_script
rt_mod=_dubs_env.fresh_runtime() and __import__('runtime')
m=load_script('ctrl',_dubs_env.SRC/'control.py');sec=m.sec
class SecurityTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.old_db=sec.DB;self.old_config=copy.deepcopy(sec.CONFIG);sec.DB=self.root/'security.sqlite3';sec.CONFIG=copy.deepcopy(self.old_config);sec.CONFIG['trusted_proxies']=[];sec.CONFIG['owner_names']=['testowner'];sec.CONFIG['owner_ids']=['owner-id-1']
  (self.root/'mobile').mkdir(exist_ok=True);(self.root/'mobile'/'Dubs-1.0.0.apk').write_bytes(b'PK'+b'x'*64)
  self.old_cfg=copy.deepcopy(m.CFG);m.CFG['mobile']=dict(m.CFG.get('mobile') or {});m.CFG['mobile']['dir']=str(self.root/'mobile')
  self.old_art=rt_mod.mobile_artifacts
  _art=patch.object(m.rt,'mobile_artifacts',return_value=[{'file':'Dubs-1.0.0.apk','label':'Android APK'}]);_art.start()
  self.server=sec.BoundedServer(('127.0.0.1',0),m.Handler);self.port=self.server.server_port;self.url=f'http://127.0.0.1:{self.port}';sec.CONFIG['allowed_origins']=[self.url,'https://panel.example.org'];sec.CONFIG['public_hosts']=['panel.example.org'];self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.client=requests.Session();self.client.headers['Origin']=self.url
  self.page=patch.object(m.Handler,'page',return_value='<p>PRIVATE OWNER DATA</p>');self.page.start()
 def tearDown(self):
  self.server.shutdown();self.server.server_close();self.page.stop();sec.DB=self.old_db;sec.CONFIG=self.old_config;m.CFG.clear();m.CFG.update(self.old_cfg);self.tmp.cleanup()
 def login(self,userid=None,admin=True):
  r=self.client.get(self.url);nonce=re.search('name="nonce" value="([^"]+)"',r.text).group(1);reply=Mock(status_code=200);reply.json.return_value={'User':{'Id':userid or sec.CONFIG['owner_ids'][0],'Policy':{'IsAdministrator':admin}}}
  with patch.object(m.requests,'post',return_value=reply):return self.client.post(self.url+'/login',data={'nonce':nonce,'username':'testowner','password':'not-a-real-password'},allow_redirects=False)
 def csrf(self):return sec.session(self.client.cookies.get('series_session'),self.client.headers['User-Agent'])['csrf']
 def test_native_session_survives_idle_restart_and_webview_update(self):
  self.client.headers['User-Agent']='OldWebView DubsNative/1.1 Android DubsTV/1.0'
  r=self.login();self.assertIn('Max-Age=31536000',r.headers['Set-Cookie'])
  with sec.connect() as c:c.execute('UPDATE sessions SET created=?,seen=?',(time.time()-400*86400,time.time()-100*86400))
  self.client.headers['User-Agent']='NewWebView DubsNative/2.0 Android DubsTV/2.0'
  r=self.client.get(self.url);self.assertIn('PRIVATE OWNER DATA',r.text);self.assertIn('Max-Age=31536000',r.headers['Set-Cookie'])
 def test_native_cannot_authenticate_without_credentials_or_cookie(self):
  self.client.headers['User-Agent']='DubsNative/1.1 iOS'
  self.assertNotIn('PRIVATE OWNER DATA',self.client.get(self.url).text)
  self.assertEqual(self.login('other-user').status_code,401)
 def test_native_logout_revokes_persistent_session(self):
  self.client.headers['User-Agent']='DubsNative/1.1 iOS';self.login();sid=self.client.cookies.get('series_session')
  r=self.client.post(self.url+'/logout',data={'csrf':self.csrf()},allow_redirects=False)
  self.assertEqual(r.status_code,303);self.assertIn('Max-Age=0',r.headers['Set-Cookie']);self.assertIsNone(sec.session(sid,self.client.headers['User-Agent']))
 def test_native_cookie_does_not_transfer_to_browser_or_other_platform(self):
  sid,_=sec.new_session('owner','DubsNative/1.1 Android')
  self.assertIsNone(sec.session(sid,'DubsNative/1.1 iOS'))
  sid,_=sec.new_session('owner','DubsNative/1.1 Android')
  self.assertIsNone(sec.session(sid,'browser'))
 def test_new_web_login_does_not_prune_native_sessions(self):
  sid,_=sec.new_session('owner','DubsNative/1.1 iOS')
  with sec.connect() as c:c.execute('UPDATE sessions SET created=1,seen=1')
  sec.new_session('owner','browser');self.assertIsNotNone(sec.session(sid,'New iPhone DubsNative/1.2 iOS'))
 def test_legacy_database_migrates_and_preserves_web_session(self):
  sec.connect().close()
  with sec.connect() as c:
   c.execute('DROP TABLE sessions');c.execute('CREATE TABLE sessions(sid TEXT PRIMARY KEY,user TEXT,created REAL,seen REAL,csrf TEXT,agent TEXT)');c.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)',(sec.hashed('x'*43),'owner',time.time(),time.time(),'csrf',sec.hashed('browser')))
  self.assertIsNotNone(sec.session('x'*43,'browser'))
 def test_existing_valid_native_session_is_upgraded(self):
  agent='OldWebView DubsNative/1.1 Android'
  with sec.connect() as c:c.execute('INSERT INTO sessions(sid,user,created,seen,csrf,agent) VALUES(?,?,?,?,?,?)',(sec.hashed('x'*43),'owner',time.time(),time.time(),'csrf',sec.hashed(agent)))
  self.assertTrue(sec.session('x'*43,agent)['persistent']);self.assertIsNotNone(sec.session('x'*43,'NewWebView DubsNative/2.0 Android'))
 def test_owner_login(self):
  self.assertEqual(self.login().status_code,303);self.assertIn('PRIVATE OWNER DATA',self.client.get(self.url).text)
 def test_wrong_owner_admin_denied(self):self.assertEqual(self.login('other-admin').status_code,401)
 def test_non_admin_denied(self):self.assertEqual(self.login(admin=False).status_code,401)
 def test_unauthenticated_does_not_leak(self):
  for path in ['/','/cover/21.jpg','/etc/passwd','/.git/config','/preferences.json','/security.sqlite3','/myshows-auth.json']:
   r=self.client.get(self.url+path);self.assertNotIn('PRIVATE OWNER DATA',r.text);self.assertNotIn('root:',r.text);self.assertNotIn('Traceback',r.text);self.assertNotIn('Server',r.headers)
 def test_csrf_missing_wrong_and_origin(self):
  self.login()
  for csrf in ['','wrong']:
   self.assertEqual(self.client.post(self.url+'/check',data={'csrf':csrf}).status_code,403)
  self.assertEqual(self.client.post(self.url+'/check',data={'csrf':self.csrf()},headers={'Origin':'https://evil.test'}).status_code,403)
  self.client.headers.pop('Origin');self.assertEqual(self.client.post(self.url+'/check',data={'csrf':self.csrf()}).status_code,403)
 def test_api_session_requires_auth(self):
  self.assertEqual(self.client.get(self.url+'/api/session').status_code,401)
 def test_api_session_returns_current_csrf_no_store(self):
  self.login();r=self.client.get(self.url+'/api/session')
  self.assertEqual(r.status_code,200);self.assertEqual(r.json()['csrf'],self.csrf());self.assertEqual(r.json()['userId'],sec.CONFIG['owner_ids'][0]);self.assertEqual(r.headers['Cache-Control'],'no-store')
 def test_episode_actions_use_current_session_csrf(self):
  self.login();csrf=self.client.get(self.url+'/api/session').json()['csrf']
  with patch.object(m.episodes,'snapshot',return_value={'upcoming':[]}),patch.object(m.episodes,'submit',return_value='a'*32) as submit:
   for op in ('search','download'):
    r=self.client.post(self.url+'/api/action',data={'csrf':csrf,'op':op,'episode':'19239971','selection':''})
    self.assertEqual(r.status_code,202);self.assertEqual(r.json(),{'job':'a'*32})
   self.assertEqual(submit.call_count,2)
 def test_mobile_artifacts_require_owner_session(self):
  r=self.client.get(self.url+'/apps/Dubs-1.0.0.apk',allow_redirects=False);self.assertEqual(r.status_code,303);self.assertEqual(r.headers['Location'],'/login');self.assertNotIn(b'PK',r.content)
 def test_mobile_artifact_path_allowlist(self):
  self.login()
  for name in ['security.sqlite3','../security.sqlite3','Dubs-1.0.0.apk.bak','%2e%2e%2fsecurity.sqlite3']:
   self.assertEqual(self.client.get(self.url+'/apps/'+name).status_code,404)
 def test_mobile_artifact_streaming(self):
  self.login();r=self.client.get(self.url+'/apps/Dubs-1.0.0.apk')
  self.assertEqual(r.status_code,200);self.assertTrue(r.content.startswith(b'PK'));self.assertEqual(int(r.headers['Content-Length']),len(r.content));self.assertIn('attachment;',r.headers['Content-Disposition'])
 def test_download_login_return_is_allowlisted(self):
  self.client.get(self.url+'/apps/Dubs-1.0.0.apk',allow_redirects=False)
  self.assertEqual(self.login().headers['Location'],'/apps/Dubs-1.0.0.apk')
 def test_download_login_return_cannot_redirect_offsite(self):
  self.client.cookies.set('series_download','https://evil.test')
  self.assertEqual(self.login().headers['Location'],'/')
 def test_replay_login_nonce(self):
  r=self.client.get(self.url);n=re.search('name="nonce" value="([^"]+)"',r.text).group(1);data={'nonce':n,'username':'unknown','password':'x'};self.assertEqual(self.client.post(self.url+'/login',data=data).status_code,401);self.assertEqual(self.client.post(self.url+'/login',data=data).status_code,403)
 def test_throttle_persistent(self):
  limits=[('login-ip','client',2,900)];self.assertTrue(sec.consume(limits));self.assertTrue(sec.consume(limits));self.assertFalse(sec.consume(limits))
  # A fresh database connection sees the same throttle state.
  c=sqlite3.connect(sec.DB);self.assertEqual(c.execute('select count(*) from rate').fetchone()[0],2);c.close();self.assertFalse(sec.consume(limits))
 def test_login_throttle_before_upstream(self):
  with patch.object(m.requests,'post') as upstream:
   codes=[]
   for i in range(6):
    r=self.client.get(self.url);n=re.search('name="nonce" value="([^"]+)"',r.text).group(1);codes.append(self.client.post(self.url+'/login',data={'nonce':n,'username':'unknown','password':'x'}).status_code)
   self.assertEqual(codes[-1],429);upstream.assert_not_called()
 def test_spoofed_proxy_headers_ignored(self):
  r=self.client.get(self.url,headers={'Host':'panel.example.org','X-Forwarded-Proto':'https','X-Real-IP':'1.2.3.4'});self.assertEqual(r.status_code,403)
 def test_trusted_proxy_tls_secure_cookie(self):
  sec.CONFIG['trusted_proxies']=['127.0.0.1'];r=self.client.get(self.url,headers={'Host':'panel.example.org','X-Forwarded-Proto':'https','X-Real-IP':'198.51.100.5'});self.assertEqual(r.status_code,200);self.assertIn('Secure',r.headers['Set-Cookie']);self.assertIn('HttpOnly',r.headers['Set-Cookie']);self.assertIn('SameSite=Strict',r.headers['Set-Cookie']);self.assertIn('Strict-Transport-Security',r.headers)
 def test_trusted_proxy_http_domain_rejected(self):
  sec.CONFIG['trusted_proxies']=['127.0.0.1'];r=self.client.get(self.url,headers={'Host':'panel.example.org','X-Forwarded-Proto':'http','X-Real-IP':'198.51.100.5'});self.assertEqual(r.status_code,403)
 def test_unknown_host(self):self.assertEqual(self.client.get(self.url,headers={'Host':'evil.test'}).status_code,403)
 def test_invalid_proxy_ip(self):
  sec.CONFIG['trusted_proxies']=['127.0.0.1'];self.assertEqual(self.client.get(self.url,headers={'Host':'panel.example.org','X-Forwarded-Proto':'https','X-Real-IP':'spoofed'}).status_code,403)
 def test_revoke_logout(self):
  self.login();r=self.client.post(self.url+'/logout',data={'csrf':self.csrf()},allow_redirects=False);self.assertEqual(r.status_code,303);self.assertNotIn('PRIVATE OWNER DATA',self.client.get(self.url).text)
 def test_idle_expiry_and_hash_only(self):
  self.login();sid=self.client.cookies.get('series_session')
  with sec.connect() as c:
   self.assertNotEqual(c.execute('select sid from sessions').fetchone()[0],sid);c.execute('update sessions set seen=?',(time.time()-1801,))
  self.assertNotIn('PRIVATE OWNER DATA',self.client.get(self.url).text)
 def test_session_user_agent_binding(self):
  self.login();self.client.headers['User-Agent']='different';self.assertNotIn('PRIVATE OWNER DATA',self.client.get(self.url).text)
 def test_safari_cross_site_label_with_exact_origin(self):
  self.client.headers['Sec-Fetch-Site']='cross-site'
  self.login()
  self.assertIn('PRIVATE OWNER DATA',self.client.get(self.url).text)
 def test_cross_site_origin_still_rejected(self):
  self.login()
  self.assertEqual(self.client.post(self.url+'/check',data={'csrf':self.csrf()},headers={'Origin':'https://evil.test','Sec-Fetch-Site':'cross-site'}).status_code,403)
 def test_safari_label_does_not_bypass_csrf(self):
  self.login()
  self.assertEqual(self.client.post(self.url+'/check',data={'csrf':'x'*43},headers={'Sec-Fetch-Site':'cross-site'}).status_code,403)
 def test_body_limit(self):self.assertEqual(self.client.post(self.url+'/login',data='x'*17000,headers={'Content-Type':'application/x-www-form-urlencoded'}).status_code,413)
 def test_wrong_content_type(self):self.assertEqual(self.client.post(self.url+'/login',json={'a':'b'}).status_code,415)
 def test_duplicated_form_field(self):
  self.assertEqual(self.client.post(self.url+'/login',data='nonce=a&nonce=b&username=x&password=y',headers={'Content-Type':'application/x-www-form-urlencoded'}).status_code,400)
 def test_extra_privilege_fields_rejected(self):
  self.login();r=self.client.post(self.url+'/preference',data={'csrf':self.csrf(),'series':'21','studio':'12','mode':'strict','role':'admin'});self.assertEqual(r.status_code,400)
 def test_integer_and_mode_validation(self):
  self.login()
  with patch.object(m,'set_preference') as setter:
   for sid,studio,mode in [('1/../../','12','strict'),('21','0;id','strict'),('21','12','admin')]:self.assertEqual(self.client.post(self.url+'/preference',data={'csrf':self.csrf(),'series':sid,'studio':studio,'mode':mode}).status_code,400)
   setter.assert_not_called()
 def test_worker_trigger_has_no_path(self):
  self.login()
  with patch.object(m,'save') as save:
   r=self.client.post(self.url+'/check',data={'csrf':self.csrf()},allow_redirects=False);self.assertEqual(r.status_code,303);self.assertEqual(save.call_args.args[0],'cleanup-request.json');self.assertEqual(set(save.call_args.args[1]),{'requested'})
 def test_web_cannot_run_cleanup(self):
  with self.assertRaises(PermissionError):m.cleanup()
 def test_symlink_and_parent_traversal(self):
  root=self.root/'media';root.mkdir();outside=self.root/'secret';outside.write_text('secret');(root/'link').symlink_to(outside);(root/'dir').symlink_to(self.root,target_is_directory=True)
  for path in [root/'link',root/'dir'/'secret',root/'..'/'secret',root]:
   with self.subTest(path=path),self.assertRaises((ValueError,OSError)):m.safe_path(path,root)
 def test_cover_path_traversal(self):
  self.login()
  for path in ['/cover/..%2fpasswd.jpg','/cover/1/../../etc/passwd','/cover/0001.jpg','/cover/1.jpg%00']:
   self.assertEqual(self.client.get(self.url+path).status_code,404)
 def test_fixed_security_headers(self):
  r=self.client.get(self.url);self.assertIn("style-src 'sha256-",r.headers['Content-Security-Policy']);self.assertEqual(r.headers['Cache-Control'],'no-store');self.assertEqual(r.headers['Referrer-Policy'],'same-origin');self.assertEqual(r.headers['X-Frame-Options'],'DENY');self.assertNotIn('Python',str(r.headers))
 def test_errors_do_not_echo_secrets(self):
  self.login()
  with patch.object(m,'set_preference',side_effect=ValueError('secret-key-and-/private/path')):
   r=self.client.post(self.url+'/preference',data={'csrf':self.csrf(),'series':'21','studio':'12','mode':'strict'});self.assertNotIn('secret-key',r.text);self.assertNotIn('/private/path',r.text)
 def test_oversized_headers(self):
  r=self.client.get(self.url,headers={'X-Large':'x'*17000});self.assertEqual(r.status_code,431)
 def test_long_request_line_rejected(self):
  with socket.create_connection(('127.0.0.1',self.port),timeout=2) as sock:
   sock.sendall(b'GET /'+b'x'*5000+b' HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n');self.assertIn(b'414',sock.recv(100))
 def test_unicode_nonce_and_csrf_rejected(self):
  self.assertFalse(sec.use_nonce('я','я'));self.login();self.assertEqual(self.client.post(self.url+'/check',data={'csrf':'я'}).status_code,403)
 def test_concurrent_connections_bounded(self):
  sockets=[]
  try:
   for i in range(24):sockets.append(socket.create_connection(('127.0.0.1',self.port),timeout=2))
   for i in range(100):
    if self.server.slots._value==0:break
    time.sleep(.01)
   with socket.create_connection(('127.0.0.1',self.port),timeout=2) as last:self.assertIn(b'503',last.recv(100))
  finally:
   for sock in sockets:sock.close()

if __name__=='__main__':unittest.main()
