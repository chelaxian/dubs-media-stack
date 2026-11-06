"""Real local HTTP: owner session, Origin, CSRF, exact permutation and limits."""
import json,unittest
from unittest.mock import patch
import test_security as legacy
import studio_order as so

class OrderAPI(unittest.TestCase):
 login=legacy.SecurityTests.login
 csrf=legacy.SecurityTests.csrf
 def setUp(self):
  original=legacy.sec.BoundedServer.get_request
  def accept(server):
   sock,address=original(server);return sock,('127.0.0.1',address[1])
  self.transport=patch.object(legacy.sec.BoundedServer,'get_request',accept);self.transport.start()
  legacy.SecurityTests.setUp(self)
  self.client.trust_env=False
  self.web=self.root/'web';self.worker=self.root/'worker';self.web.mkdir();self.worker.mkdir()
  self.roots=[patch.object(so.rt,'state_root',return_value=self.web),patch.object(so.rt,'worker_root',return_value=self.worker)]
  for p in self.roots:p.start()
  self.names=[s['name'] for s in legacy.m.STUDIOS]
  self.login()
 def tearDown(self):
  for p in self.roots:p.stop()
  legacy.SecurityTests.tearDown(self);self.transport.stop()
 def post(self,**kwargs):
  data=dict(csrf=self.csrf(),order=json.dumps(self.names));data.update(kwargs)
  return self.client.post(self.url+'/api/studio-order',data=data)
 def test_owner_origin_csrf_and_scoped_or_nonowner_rejected(self):
  self.assertEqual(self.post(csrf='bad').status_code,403)
  self.assertEqual(self.client.post(self.url+'/api/studio-order',data={'csrf':self.csrf(),'order':json.dumps(self.names)},headers={'Origin':'https://evil.example'}).status_code,403)
  import requests
  client=requests.Session();client.trust_env=False
  self.assertEqual(client.get(self.url+'/api/studio-order').status_code,401)
  _,token=legacy.sec.register_device(legacy.sec.CONFIG['owner_ids'][0],'android')
  self.assertEqual(client.get(self.url+'/api/studio-order',headers={'Authorization':'Bearer '+token}).status_code,401)
  self.assertEqual(client.post(self.url+'/api/studio-order',data={'csrf':self.csrf(),'order':json.dumps(self.names)},headers={'Origin':self.url,'Authorization':'Bearer '+'x'*43}).status_code,403)
  sid,csrf=legacy.sec.new_session('not-owner',self.client.headers['User-Agent'])
  self.client.cookies.clear();self.client.cookies.set('series_session',sid)
  self.assertEqual(self.post(csrf=csrf).status_code,403)
  self.assertFalse((self.web/so.REQUEST).exists())
 def test_permutation_unknown_duplicate_missing_overlong_fields_and_duplicates(self):
  with patch.object(legacy.sec,'consume',return_value=True):
   values=[self.names[:-1],self.names+['x'],self.names[:-1]+[self.names[0]],['unknown']+self.names[1:],['x'*201]+self.names[1:],{'pattern':'.*'},None]
   for value in values:
    self.assertEqual(self.post(order=json.dumps(value)).status_code,400,value)
   self.assertEqual(self.post(order='x'*4097).status_code,400)
   self.assertEqual(self.post(score='100').status_code,400)
   response=self.client.post(self.url+'/api/studio-order',data=[('csrf',self.csrf()),('order',json.dumps(self.names)),('order',json.dumps(self.names))])
   self.assertEqual(response.status_code,400)
   self.assertFalse((self.web/so.REQUEST).exists())
 def test_save_is_pending_no_sonarr_calls_and_rate_limit(self):
  with patch.object(legacy.m,'api') as api:
   for i in range(6):
    response=self.post(order=json.dumps(self.names[::-1]));self.assertEqual(response.status_code,202)
    self.assertEqual(response.json()['state'],'pending')
   self.assertEqual(self.post().status_code,429)
   response=self.client.get(self.url+'/api/studio-order')
   self.assertEqual(response.json()['order'],self.names[::-1]);self.assertEqual(response.json()['effectiveOrder'],self.names)
   api.assert_not_called()
  self.assertEqual((self.web/so.REQUEST).stat().st_mode & 0o777,0o640)

if __name__=='__main__':unittest.main()
