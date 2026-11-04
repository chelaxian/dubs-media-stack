"""Login transport regression: inaccessible metadata identity, no redirect forwarding."""
import json,tempfile,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from email.message import Message
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import Mock,patch
import requests
import _dubs_env
from _dubs_env import load_script
import runtime as rt
m=load_script('notifications_login_control',_dubs_env.SRC/'control.py')

class TransportConfigTests(unittest.TestCase):
 def test_explicit_public_transport_never_reads_private_connection(self):
  with patch.object(rt,'settings',return_value={'jellyfin_api_url':'https://media.example/jellyfin/'}),patch.object(rt,'connection',side_effect=PermissionError) as private:
   self.assertEqual(rt.jellyfin_api_base(),'https://media.example/jellyfin');private.assert_not_called()
 def test_empty_default_uses_actual_remote_connection(self):
  for config in ({},{'jellyfin_api_url':''}):
   with patch.object(rt,'settings',return_value=config),patch.object(rt,'connection',return_value={'base_url':'https://remote.example/jf/'}) as private:
    self.assertEqual(rt.jellyfin_api_base(),'https://remote.example/jf');private.assert_called_once_with('jellyfin')
 def test_bad_explicit_base_rejected_without_secret_fallback(self):
  for base in ('ftp://media.example','https://user:secret@media.example','https://media.example?key=x','https://media.example#part','https://media.example?','https://media.example#','//media.example','https://','https://media.example:bad','https://media.example:0','https://media.example:65536',' https://media.example','https://media.example/\npath','https://media.example\\evil','https://%65xample.com',42,False):
   with self.subTest(base=base),patch.object(rt,'settings',return_value={'jellyfin_api_url':base}),patch.object(rt,'connection',side_effect=PermissionError) as private:
    with self.assertRaises(ValueError):rt.jellyfin_api_base()
    private.assert_not_called()
 def test_generated_transport_default_empty_and_adopted_value_preserved(self):
  from installer import configgen
  self.assertEqual(configgen.CONFIG_EXAMPLE['jellyfin_api_url'],'')
  with tempfile.TemporaryDirectory() as directory:
   path=Path(directory)/'config.json';path.write_text(json.dumps({'jellyfin_api_url':'https://adopted.example/jf'}))
   configgen.generate(Path(directory))
   self.assertEqual(json.loads(path.read_text())['jellyfin_api_url'],'https://adopted.example/jf')

class LoginHTTPTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  self.calls=[];self.redirect_auth=False;self.owner='owner-id';self.admin=True
  outer=self
  class Upstream(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_POST(self):
    body=self.rfile.read(int(self.headers.get('Content-Length','0')))
    outer.calls.append((self.path,dict(self.headers),body))
    if self.path.endswith('/Sessions/Logout') or outer.redirect_auth:
     self.send_response(307);self.send_header('Location',outer.trap_url+'/sink');self.end_headers();return
    self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
    self.wfile.write(json.dumps({'User':{'Id':outer.owner,'Policy':{'IsAdministrator':outer.admin}},'AccessToken':'ephemeral-test-token'}).encode())
  self.traps=[]
  class Trap(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_POST(self):outer.traps.append(True);self.send_response(500);self.end_headers()
  self.servers=[]
  for handler in (Trap,Upstream):
   server=ThreadingHTTPServer(('127.0.0.1',0),handler);threading.Thread(target=server.serve_forever,daemon=True).start();self.servers.append(server)
  self.trap_url='http://localhost:%d'%self.servers[0].server_port
  self.base='http://127.0.0.1:%d/jf'%self.servers[1].server_port
  self.http=requests.Session();self.http.trust_env=False
  self.patches=[patch.object(rt,'settings',return_value={'jellyfin_api_url':self.base,'mobile':{}}),
   patch.object(rt,'connection',side_effect=PermissionError('metadata file inaccessible')),
   patch.object(m.sec,'DB',self.root/'security.sqlite3'),
   patch.dict(m.sec.CONFIG,{'owner_names':['owner'],'owner_ids':['owner-id']}),
   patch.object(m.requests,'post',wraps=self.http.post),patch.object(m.time,'sleep')]
  for item in self.patches:item.start()
 def tearDown(self):
  for item in reversed(self.patches):item.stop()
  self.http.close()
  for server in self.servers:server.shutdown();server.server_close()
  self.tmp.cleanup()
 def login(self,nonce_valid=True):
  nonce=m.sec.nonce();headers=Message();headers['Cookie']='series_login='+nonce;headers['User-Agent']='offline-browser'
  handler=SimpleNamespace(headers=headers,security_context={'client':'127.0.0.1','secure':False},send_error=Mock(),send_response=Mock(),send_header=Mock(),end_headers=Mock(),cookie=lambda name,value,age:name+'='+value)
  m.Handler.login(handler,{'nonce':nonce if nonce_valid else 'invalid','username':'owner','password':'test-password'})
  return handler
 def test_login_logout_work_with_inaccessible_secret_file_and_logout_redirect_blocked(self):
  handler=self.login();handler.send_response.assert_called_once_with(303)
  self.assertEqual([x[0] for x in self.calls],['/jf/Users/AuthenticateByName','/jf/Sessions/Logout'])
  self.assertEqual(json.loads(self.calls[0][2]),{'Username':'owner','Pw':'test-password'})
  self.assertEqual(self.calls[1][1]['X-Emby-Token'],'ephemeral-test-token')
  self.assertEqual(self.traps,[])
  rt.connection.assert_not_called()
  for call in m.requests.post.call_args_list:self.assertIs(call.kwargs['allow_redirects'],False);self.assertNotIn('verify',call.kwargs)
 def test_auth_redirect_never_forwards_password(self):
  self.redirect_auth=True;handler=self.login();handler.send_error.assert_called_once_with(401)
  self.assertEqual(len(self.calls),1);self.assertEqual(self.traps,[])
 def test_owner_admin_nonce_and_rate_gates_remain(self):
  self.login(False).send_error.assert_called_once_with(403);self.assertEqual(self.calls,[])
  self.owner='other-owner';self.login().send_error.assert_called_once_with(401)
  self.owner='owner-id';self.admin=False;self.login().send_error.assert_called_once_with(401)
  with patch.object(m.sec,'consume',return_value=False):self.login().send_error.assert_called_once_with(429)

if __name__=='__main__':unittest.main()
