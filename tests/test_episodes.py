import sys,tempfile,types,unittest,os,json,time
from pathlib import Path
from unittest.mock import patch,Mock
import _dubs_env  # offline roots; src/automations on sys.path
import episodes as ep
E={'id':123,'season':2,'number':13,'sonarrId':42,'seriesId':21,'future':False,'seriesTitle':'Show'}
ROW={'seasonNumber':2,'episodeNumbers':[],'fullSeason':True,'downloadUrl':'http://127.0.0.1:9696/1/download'}
def files(names):return [{'name':p,'length':100} for p in names]
class Tests(unittest.TestCase):
 def test_numbered_season_pack(self):self.assertEqual(ep.select_files(files(['Show/S02E12.mkv','Show/S02E13.mkv','Show/S02E14.mkv']),E,ROW),([1],1))
 def test_all_seasons_pack(self):self.assertEqual(ep.select_files(files(['Show/Season 01/13.mkv','Show/Season 02/13.mkv','Show/Season 03/13.mkv']),E,{'seasonNumber':1}),([1],1))
 def test_subtitles_only_same_episode(self):
  result=ep.select_files(files(['Show/S02E13.mkv','Show/S02E13.rus.ass','Show/S02E14.ass','Show/S02E130.ass']),E,ROW);self.assertEqual(result,([0,1],0))
 def test_multi_episode_video_rejected(self):
  for title in ['Show/S02E13E14.mkv','Show/S02E13-14.mkv','Show/S02E13.14.mkv']:
   with self.subTest(title=title),self.assertRaises(ep.UserError):ep.select_files(files([title]),E,{**ROW,'fullSeason':False,'episodeNumbers':[13]})
 def test_wrong_episode_rejected(self):
  with self.assertRaises(ep.UserError):ep.select_files(files(['Show/S02E14.mkv']),E,{**ROW,'episodeNumbers':[13],'fullSeason':False})
 def test_ambiguous_duplicate_versions_rejected(self):
  with self.assertRaises(ep.UserError):ep.select_files(files(['Show/S02E13.1080p.mkv','Show/S02E13.2160p.mkv']),E,ROW)
 def test_unknown_single_file_only_with_exact_release(self):
  self.assertEqual(ep.select_files(files(['Show/video.mkv']),E,{'seasonNumber':2,'episodeNumbers':[13],'fullSeason':False}),([0],0))
  with self.assertRaises(ep.UserError):ep.select_files(files(['Show/video.mkv']),E,ROW)
 def test_traversal(self):
  for path in ['../file.mkv','x/../file.mkv','/root/file.mkv','x\\evil.mkv','x\0.mkv']:
   with self.subTest(path=path),self.assertRaises(ep.UserError):ep.clean_file_path(path)
 def test_mag_unsafe_webseeds_removed(self):
  out=ep.sanitize_magnet('magnet:?xt=urn:btih:'+'a'*40+'&xs=http://127.0.0.1/secret&ws=http://localhost/&tr=http://127.0.0.1/x');self.assertNotIn('127.0.0.1',out);self.assertNotIn('xs=',out);self.assertNotIn('ws=',out)
 def test_download_url_ssrf(self):
  for url in ['file:///etc/passwd','http://169.254.169.254/x','http://localhost:22/x','https://evil.example/torrent']:
   with self.subTest(url=url),self.assertRaises(ep.UserError):ep.safe_download(None,url)
 def test_download_retries_transient_indexer_error(self):
  busy=Mock(status_code=503);ok=Mock(status_code=302,headers={'Location':'magnet:?xt=urn:btih:'+'a'*40})
  with patch.object(ep,'validate_endpoint'),patch.object(ep,'_tls_verify',return_value=True),patch.object(ep.requests,'get',side_effect=[busy,ok]) as get,patch.object(ep.time,'sleep'):
   data,magnet=ep.safe_download(None,'http://localhost/trusted')
  self.assertIsNone(data);self.assertIn('urn%3Abtih',magnet);self.assertEqual(get.call_count,2);busy.close.assert_called_once()
 def test_magnet_cache_verifies_exact_hash_and_preserves_trackers(self):
  import hashlib
  meta={b'info':{b'name':b'episode.mkv',b'length':10}}
  data=ep.encode_bencode(meta);digest=hashlib.sha1(ep.encode_bencode(meta[b'info'])).hexdigest()
  magnet='magnet:?xt=urn:btih:'+digest+'&tr=udp%3A%2F%2Ftracker.example%3A80%2Fannounce'
  def fetch(cmd,**kw):
   Path(cmd[cmd.index('--output')+1]).write_bytes(data);return types.SimpleNamespace(returncode=0)
  with patch.object(ep.subprocess,'run',side_effect=fetch),patch.object(ep.rt,'settings',return_value={'proxy':''}):
   result=ep.validate_metainfo(ep.cached_magnet_metadata(magnet))
   self.assertEqual(result[b'info'],meta[b'info']);self.assertEqual(result[b'announce'],b'udp://tracker.example:80/announce')
   with self.assertRaises(ValueError):ep.cached_magnet_metadata(magnet.replace(digest,'a'*40))
 def test_magnet_metadata_uses_verified_fallback_on_timeout(self):
  import subprocess
  magnet='magnet:?xt=urn:btih:'+'a'*40
  with patch.object(ep.subprocess,'run',side_effect=subprocess.TimeoutExpired('aria2c',100)),patch.object(ep,'cached_magnet_metadata',return_value=b'verified') as cache:
   self.assertEqual(ep.metadata_only(None,magnet),b'verified');cache.assert_called_once_with(magnet)
 def setUp(self):
  statvfs=patch.object(ep.os,'statvfs',return_value=types.SimpleNamespace(f_bavail=10**7,f_frsize=4096))
  statvfs.start();self.addCleanup(statvfs.stop)
 def fixture(self,root,duplicate=False,broken_flags=False,names=None):
  # offline storage: enough free space, no real mount
  vfs=patch.object(ep.os,'statvfs',return_value=types.SimpleNamespace(f_bavail=10**7,f_frsize=4096));vfs.start();self.addCleanup(vfs.stop)
  mp=patch.object(ep.rt,'media_paths',return_value={'shows_host':str(root/'shows'),'torrent_host':str(root/'torrents'),'sonarr_tv':'/tv','sonarr_torrents':'/torrents','downloads':'/downloads'});mp.start();self.addCleanup(mp.stop)
  (root/'jobs').mkdir();(root/'release-cache').mkdir();fs=files(names or ['Show/S02E12.mkv','Show/S02E13.mkv','Show/S02E14.mkv']);wanted=[True]*len(fs);calls=[]
  def rpc(method,args):
   calls.append((method,args))
   if method=='torrent-add':return {('torrent-duplicate' if duplicate else 'torrent-added'):{'id':99,'hashString':'a'*40}}
   if method=='torrent-get':return {'torrents':[{'files':fs,'fileStats':[{'wanted':x} for x in wanted],'downloadDir':'/downloads/complete/tv/Panel/test'}]}
   if method=='torrent-set' and not broken_flags:
    for i in args.get('files-unwanted',[]):wanted[i]=False
    for i in args.get('files-wanted',[]):wanted[i]=True
   return {}
  m=types.SimpleNamespace(WORKER_STATE=root,trpc=rpc,api=Mock(return_value={'hasFile':False,'monitored':False}));return m,calls
 def test_paused_add_then_exact_files_then_start(self):
  with tempfile.TemporaryDirectory() as d,patch.object(ep,'safe_download',return_value=(b'fixture',None)):
   m,calls=self.fixture(Path(d));result=ep.request_download(m,'f'*32,E,{'row':ROW,'display':{'allowed':True}});self.assertEqual(result['state'],'downloading');self.assertTrue(calls[0][1]['paused']);start=next(i for i,c in enumerate(calls) if c[0]=='torrent-start');sets=[c for c in calls[:start] if c[0]=='torrent-set'];self.assertEqual(sets[0][1]['files-unwanted'],[0,1,2]);self.assertEqual(sets[1][1]['files-wanted'],[1])
 def test_start_intent_survives_failed_start_rpc(self):
  with tempfile.TemporaryDirectory() as d,patch.object(ep,'safe_download',return_value=(b'fixture',None)):
   root=Path(d);m,calls=self.fixture(root);rpc=m.trpc
   def fail_start(method,args):
    if method=='torrent-start':raise RuntimeError('interrupted')
    return rpc(method,args)
   m.trpc=fail_start
   with self.assertRaises(RuntimeError):ep.request_download(m,'f'*32,E,{'row':ROW,'display':{'allowed':True}})
   entry=ep.read(root/'managed-downloads.json',{})['a'*40]
   self.assertTrue(entry['startPending']);self.assertEqual(entry['wanted'],[1]);self.assertEqual(entry['episodes'][0]['state'],'downloading')
 def test_wrong_flags_never_starts(self):
  with tempfile.TemporaryDirectory() as d,patch.object(ep,'safe_download',return_value=(b'fixture',None)):
   m,calls=self.fixture(Path(d),broken_flags=True)
   with self.assertRaises(ep.UserError):ep.request_download(m,'f'*32,E,{'row':ROW,'display':{'allowed':True}})
   self.assertNotIn('torrent-start',[c[0] for c in calls])
 def test_existing_unmanaged_torrent_not_changed(self):
  with tempfile.TemporaryDirectory() as d,patch.object(ep,'safe_download',return_value=(b'fixture',None)):
   m,calls=self.fixture(Path(d),duplicate=True)
   with self.assertRaises(ep.UserError):ep.request_download(m,'f'*32,E,{'row':ROW,'display':{'allowed':True}})
   self.assertEqual([c[0] for c in calls],['torrent-add'])
 def test_ambiguous_pack_never_starts(self):
  with tempfile.TemporaryDirectory() as d,patch.object(ep,'safe_download',return_value=(b'fixture',None)):
   m,calls=self.fixture(Path(d),names=['Show/S02E12.mkv','Show/S02E14.mkv'])
   with self.assertRaises(ep.UserError):ep.request_download(m,'f'*32,E,{'row':ROW,'display':{'allowed':True}})
   self.assertNotIn('torrent-start',[c[0] for c in calls])
 def test_criteria_not_bypassable(self):
  with self.assertRaises(ep.UserError):ep.request_download(None,'f'*32,E,{'row':ROW,'display':{'allowed':False}})
 def test_selection_foreign_episode_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'jobs').mkdir();(root/'release-cache').mkdir();key='a'*32;ep.atomic(root/'release-cache'/(key+'.json'),{'episode':{'id':999},'created':time.time(),'items':[]},0o600);m=types.SimpleNamespace(WORKER_STATE=root,api=Mock(return_value={'hasFile':False,'monitored':False}))
   with patch.object(ep,'episode',return_value=E):ep.process(m,'b'*32,{'op':'download','episode':123,'selection':key+':0'})
   self.assertEqual(ep.result(m,'b'*32)['state'],'error')
 def test_watched_write_uses_exact_myshows_id(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'jobs').mkdir();m=types.SimpleNamespace(WORKER_STATE=root)
   with patch.object(ep,'episode',return_value=E),patch.object(ep,'v3') as rpc,patch.object(ep,'refresh'):
    ep.process(m,'c'*32,{'op':'watch','episode':123,'selection':''});rpc.assert_called_once_with(m,'manage.CheckEpisode',{'id':123})
 def test_future_mark_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'jobs').mkdir();m=types.SimpleNamespace(WORKER_STATE=root)
   with patch.object(ep,'episode',return_value={**E,'future':True}),patch.object(ep,'v3') as rpc:
    ep.process(m,'c'*32,{'op':'watch','episode':123,'selection':''});rpc.assert_not_called();self.assertEqual(ep.result(m,'c'*32)['state'],'error')
 def test_future_plan_never_searches_before_airdate(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'jobs').mkdir();m=types.SimpleNamespace(WORKER_STATE=root,api=Mock())
   import datetime
   future=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(days=3)).isoformat()
   with patch.object(ep,'episode',return_value={**E,'future':True,'airDate':future}),patch.object(ep,'search') as search:
    ep.process(m,'d'*32,{'op':'download','episode':123,'selection':''});search.assert_not_called();m.api.assert_not_called()
   scheduled=ep.read(root/'scheduled-downloads.json',{});self.assertGreater(scheduled['123']['notBefore'],time.time());self.assertEqual(ep.result(m,'d'*32)['state'],'waiting')
 def test_season_range_in_root_not_confused_with_video(self):
  self.assertEqual(ep.select_files(files(['Show.S02E01-14/Show.S02E13.mkv']),E,ROW),([0],0))
 def test_remake_requires_exact_year(self):
  parsed={'parsedEpisodeInfo':{'seriesTitle':'Scrubs'},'series':None};expected={'id':43,'title':'Scrubs (2026)','year':2026}
  self.assertTrue(ep.series_match(parsed,'Scrubs S02E06 [2026]',expected));self.assertFalse(ep.series_match(parsed,'Scrubs S02E06 [2001]',expected))
 def test_jobs_id_traversal(self):
  with self.assertRaises(ValueError):ep.result(None,'../myshows-auth')
 def test_atomic_permissions(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'state';old=os.umask(0o077)
   try:ep.atomic(p,{'ok':True})
   finally:os.umask(old)
   self.assertEqual(p.stat().st_mode&0o777,0o640)
if __name__=='__main__':unittest.main()
