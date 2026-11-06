"""Authenticated notification API tests with isolated state and local HTTP only."""
import unittest
from unittest.mock import patch
import test_security as legacy
import notifications as n

class NotificationAPITests(unittest.TestCase):
 login=legacy.SecurityTests.login
 csrf=legacy.SecurityTests.csrf
 def setUp(self):
  # The execution environment may transport local TCP through a private peer.
  # Normalize only this offline server's accepted loopback test connection.
  original=legacy.sec.BoundedServer.get_request
  def accept(server):
   sock,address=original(server);return sock,('127.0.0.1',address[1])
  self.transport=patch.object(legacy.sec.BoundedServer,'get_request',accept);self.transport.start()
  legacy.SecurityTests.setUp(self)
  self.journal=patch.object(n,'DB',self.root/'events.sqlite3');self.journal.start();n.journal_init()
  self.settings=patch.object(n,'SETTINGS_PATH',self.root/'settings.json');self.settings.start()
  self.login()
 def tearDown(self):
  self.settings.stop();self.journal.stop();legacy.SecurityTests.tearDown(self);self.transport.stop()
 def register(self):
  r=self.client.post(self.url+'/api/notifications/devices',data={'csrf':self.csrf(),'platform':'android'})
  self.assertEqual(r.status_code,200);return r.json()
 def test_registration_tail_feed_only_scope_revoke_and_private_payload(self):
  n.emit('new_episode','old','old',dedupe='old')
  device=self.register();self.assertEqual(device['cursor'],1)
  self.assertRegex(device['instanceId'],r'^[0-9a-f]{32}$')
  import requests
  client=requests.Session();client.trust_env=False
  headers={'Authorization':'Bearer '+device['token']}
  n.emit('new_episode','title http://192.0.2.1/secret','/mnt/private/file',77,4,'new')
  r=client.get(self.url+'/api/notifications/events?after=1&limit=1',headers=headers)
  self.assertEqual(r.status_code,200);self.assertEqual(len(r.json()['events']),1)
  self.assertNotIn('192.0.2',r.text);self.assertNotIn('/mnt',r.text)
  for route in ('/api/session','/api/episodes','/api/notifications/settings','/api/notifications/devices','/api/notifications/history'):
   self.assertEqual(client.get(self.url+route,headers=headers).status_code,401,route)
  self.assertEqual(client.post(self.url+'/api/action',headers=dict(headers,Origin=self.url),data={'csrf':'x','op':'refresh','episode':'0','selection':''}).status_code,403)
  rows=self.client.get(self.url+'/api/notifications/devices').json()
  self.assertNotIn(device['token'],str(rows));self.assertNotIn('token',str(rows))
  r=self.client.post(self.url+'/api/notifications/revoke',data={'csrf':self.csrf(),'deviceId':device['deviceId']})
  self.assertEqual(r.status_code,200)
  self.assertEqual(client.get(self.url+'/api/notifications/events',headers=headers).status_code,401)
 def test_settings_history_queries_and_csrf(self):
  data={'csrf':self.csrf(),'enabled':'false','types':'["new_episode"]','show_titles':'true'}
  r=self.client.post(self.url+'/api/notifications/settings',data=data)
  self.assertEqual(r.json(),{'enabled':False,'types':['new_episode'],'showTitles':True})
  self.assertEqual(self.client.post(self.url+'/api/notifications/settings',data=dict(data,csrf='bad')).status_code,403)
  n.emit('new_episode','one','m',dedupe='one');n.emit('new_episode','two','m',dedupe='two')
  r=self.client.get(self.url+'/api/notifications/history?before=3&limit=1')
  self.assertEqual(r.status_code,200);self.assertEqual(r.json()['events'][0]['id'],2)
  for query in ('after=1&after=2','after=1_0','after=-1','after=','limit=101','limit=0','extra=1'):
   self.assertEqual(self.client.get(self.url+'/api/notifications/events?'+query).status_code,400,query)
 def test_uninitialized_journal_registration_fails_without_token(self):
  n.DB.unlink()
  r=self.client.post(self.url+'/api/notifications/devices',data={'csrf':self.csrf(),'platform':'ios'})
  self.assertEqual(r.status_code,503)

if __name__=='__main__':unittest.main()
