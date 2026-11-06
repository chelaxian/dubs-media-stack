"""Order persistence, legacy adoption, owned-only score application and retry."""
import copy,json,os,re,subprocess,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import _dubs_env
import runtime as rt
import studio_order as so
import upgrades

STUDIOS=json.loads((_dubs_env.REPO/'studios.json').read_text())

class Sonarr:
 def __init__(self,legacy=True,catchall=False):
  self.calls=[];self.fail=False;self.ignore=False
  names=list(so.owned_formats(STUDIOS))
  self.formats=[{'id':i+1,'name':n} for i,n in enumerate(names) if catchall or i<13]
  items=[dict(format=f['id'],name=f['name'],score=100*2**(12-i) if i<13 else 0) for i,f in enumerate(self.formats)]
  items += [{'format':50,'name':rt.settings()['veto_format'],'score':-10000000},
            {'format':51,'name':'Русская дорожка / RU','score':100},
            {'format':52,'name':'Выбор сериала — LostFilm','score':0},
            {'format':53,'name':'User-owned special','score':733}]
  def profile(i,name):return {'id':i,'name':name,'minFormatScore':100,'items':[{'id':7,'allowed':True,'items':[{'quality':{'id':9},'allowed':True}]}], 'cutoff':7,'upgradeAllowed':True,'formatItems':copy.deepcopy(items)}
  self.profiles=[profile(1,'Any'),profile(2,'HD-1080p'),profile(3,'Dubs RU 2160p user copy')]
  self.profiles.append(profile(7,'4K / 1080p — RU preferred' if legacy else 'Dubs RU 2160p'))
  if not legacy:self.profiles.append(profile(17,'Dubs RU 1080p'))
  for i,mode in [(8,'приоритет'),(9,'только')]:
   p=profile(i,'Сериал / база 7 / LostFilm / '+mode)
   if mode=='только':p['minFormatScore']=1000001
   next(x for x in p['formatItems'] if x['format']==52)['score']=1000000
   self.profiles.append(p)
  self.profiles.append(profile(10,'Сериал / база 1 / LostFilm / только'))
  self.profiles.append(profile(11,'Сериал / база 7 / Unknown / только'))
 def api(self,path,method='GET',data=None):
  self.calls.append((path,method,copy.deepcopy(data)))
  if method=='PUT' and path.startswith('customformat/'):
   if self.fail:raise RuntimeError('Upstream unavailable')
   if not self.ignore:self.formats=[copy.deepcopy(data) if f['id']==data['id'] else f for f in self.formats]
   return copy.deepcopy(data)
  if method=='PUT':
   if self.fail:raise RuntimeError('Upstream unavailable')
   if not self.ignore:
    self.profiles=[copy.deepcopy(data) if p['id']==data['id'] else p for p in self.profiles]
   return copy.deepcopy(data)
  if path=='customformat' and method=='POST':
   f=dict(data,id=80);self.formats.append(f)
   for p in self.profiles:p['formatItems'].append({'format':80,'name':f['name'],'score':0})
   return copy.deepcopy(f)
  if path=='customformat':return copy.deepcopy(self.formats)
  if path=='qualityprofile':return copy.deepcopy(self.profiles)
  if path.startswith('qualityprofile/'):return copy.deepcopy(next(p for p in self.profiles if p['id']==int(path.split('/')[1])))
  raise AssertionError('Unexpected API: '+path)

class OrderTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  self.web=self.root/'web';self.worker=self.root/'worker';self.web.mkdir();self.worker.mkdir()
  self.env=patch.dict(os.environ,{'SERIES_CONTROL_STATE_ROOT':str(self.web),'SERIES_CONTROL_WORKER_ROOT':str(self.worker)});self.env.start()
  self.arr=Sonarr();self.m=SimpleNamespace(STUDIOS=STUDIOS,ROLE='operations',api=self.arr.api,load=lambda *a:{})
  self.names=[s['name'] for s in STUDIOS]
 def tearDown(self):self.env.stop();self.tmp.cleanup()
 def test_validation_rejects_all_non_permutations(self):
  for value in [None,{},'x',self.names[:-1],self.names+['x'],self.names[:-1]+[self.names[0]],['unknown']+self.names[1:],[123]+self.names[1:],[['x']]+self.names[1:],['x'*201]+self.names[1:]]:
   with self.subTest(value=str(value)[:50]),self.assertRaises(ValueError):so.submit(STUDIOS,value)
  self.assertFalse((self.web/so.REQUEST).exists())
 def test_initial_actual_order_not_historic_default_no_mutations(self):
  base=next(p for p in self.arr.profiles if p['id']==7)
  next(i for i in base['formatItems'] if i['format']==13)['score']=600000
  so.reconcile(self.m)
  self.assertEqual(so.effective(STUDIOS)[0],STUDIOS[12]['name'])
  self.assertEqual(so.view(STUDIOS)['state'],'applied')
  self.assertTrue(all(method=='GET' for _,method,_ in self.arr.calls))
 def test_legacy_and_portable_only_owned_scores_and_safety_preserved(self):
  for legacy in [True,False]:
   (self.worker/so.STATUS).unlink(missing_ok=True)
   self.arr=Sonarr(legacy);self.m.api=self.arr.api
   before=copy.deepcopy(self.arr.profiles)
   order=self.names.copy();order[0],order[1]=order[1],order[0]
   result=so.submit(STUDIOS,order);self.assertEqual(result['state'],'pending')
   self.assertEqual(so.effective(STUDIOS),self.names)
   so.reconcile(self.m);self.assertEqual(so.view(STUDIOS)['state'],'applied')
   for old,new in zip(before,self.arr.profiles):
    if old['id'] not in ({7,8,9} if legacy else {7,17,8,9}):self.assertEqual(old,new);continue
    for key in old:
     if key!='formatItems':self.assertEqual(old[key],new[key])
    for a,b in zip(old['formatItems'],new['formatItems']):
     if a['format']>=50:self.assertEqual(a,b)
   self.assertEqual(so.effective(STUDIOS),order)
   # The only mutations were profile PUTs, no searches, series updates or downloads.
   self.assertTrue(all(path.startswith('qualityprofile/') for path,method,_ in self.arr.calls if method!='GET'))
 def test_portable_existing_catchall_reorders_without_creation(self):
  self.arr=Sonarr(legacy=False,catchall=True);self.m.api=self.arr.api
  order=[self.names[-1]]+self.names[:-1];so.submit(STUDIOS,order);so.reconcile(self.m)
  self.assertEqual(so.view(STUDIOS)['state'],'applied')
  self.assertFalse(any(method=='POST' for _,method,_ in self.arr.calls))
  for p in so.owned_profiles(self.arr.profiles,STUDIOS):
   self.assertEqual(next(i['score'] for i in p['formatItems'] if i['format']==14),409600)
 def test_upstream_failure_and_false_success_retry(self):
  order=self.names[::-1];so.submit(STUDIOS,order)
  self.arr.fail=True;so.reconcile(self.m)
  self.assertEqual(so.view(STUDIOS)['state'],'error');self.assertEqual(so.effective(STUDIOS),self.names)
  self.arr.fail=False;self.arr.ignore=True;so.reconcile(self.m)
  self.assertEqual(so.view(STUDIOS)['state'],'error')
  self.arr.ignore=False;so.reconcile(self.m)
  self.assertEqual(so.view(STUDIOS)['state'],'applied');self.assertEqual(so.effective(STUDIOS),order)
  self.assertEqual(len([c for c in self.arr.calls if c[0]=='customformat' and c[1]=='POST']),1)
 def test_missing_named_format_fails_without_profile_mutation(self):
  self.arr.formats.pop(0);so.submit(STUDIOS,self.names[::-1]);so.reconcile(self.m)
  self.assertEqual(so.view(STUDIOS)['state'],'error')
  self.assertFalse(any(method=='PUT' for _,method,_ in self.arr.calls))
 def test_missing_catchall_is_optional_at_bottom_created_when_moved(self):
  so.submit(STUDIOS,self.names);so.reconcile(self.m)
  self.assertEqual(so.view(STUDIOS)['state'],'applied')
  self.assertFalse(any(method=='POST' for _,method,_ in self.arr.calls))
  order=[self.names[-1]]+self.names[:-1];so.submit(STUDIOS,order);so.reconcile(self.m)
  self.assertEqual(so.effective(STUDIOS),order)
  new=next(f for f in self.arr.formats if f['id']==80)
  self.assertEqual(new['specifications'],so.catchall_payload(STUDIOS)['specifications'])
  self.assertEqual(upgrades.voice_rank(self.m,1,'Unknown Russian'),0)
  self.assertEqual(upgrades.voice_rank(self.m,1,'LostFilm'),2)
  self.m.load=lambda *a:{'1':{'studio':'LostFilm','mode':'strict'}}
  self.assertEqual(upgrades.voice_rank(self.m,1,'LostFilm'),0)
  self.assertIsNone(upgrades.voice_rank(self.m,1,'Unknown Russian'))
  self.m.load=lambda *a:{'1':{'studio':13,'mode':'strict'}}
  self.assertEqual(upgrades.voice_rank(self.m,1,'Unknown Russian'),0)
  self.assertIsNone(upgrades.voice_rank(self.m,1,'LostFilm'))
 def test_cross_process_and_cached_catalog_legacy_index(self):
  cached=copy.deepcopy(STUDIOS);order=self.names.copy();order.insert(0,order.pop(5))
  so.submit(STUDIOS,order);so.reconcile(self.m)
  # A fresh interpreter reads confirmed state, not a process cache.
  code="import json,studio_order; print(json.dumps(studio_order.effective(json.load(open('studios.json'))),ensure_ascii=False))"
  env=dict(os.environ,PYTHONPATH=str(_dubs_env.SRC))
  self.assertEqual(json.loads(subprocess.check_output([sys.executable,'-c',code],cwd=_dubs_env.REPO,env=env)),order)
  self.assertEqual(cached,STUDIOS)
  self.m.load=lambda *a:{'1':{'studio':1,'mode':'fallback'}}
  self.assertEqual(upgrades.voice_rank(self.m,1,'LostFilm'),0)
  self.assertEqual(upgrades.voice_rank(self.m,1,'AlexFilm'),1)
 def test_applied_scores_drive_panel_and_keep_russian_quality_and_strict(self):
  import episodes
  order=[self.names[-1]]+self.names[:-1];so.submit(STUDIOS,order);so.reconcile(self.m)
  base=next(p for p in self.arr.profiles if p['id']==7)
  strict=next(p for p in self.arr.profiles if p['id']==9)
  def row(title,formats,qid=9):
   return {'title':title,'customFormats':[{'id':f,'name':next(i['name'] for i in base['formatItems'] if i['format']==f)} for f in formats], 'seeders':10,'quality':{'quality':{'id':qid,'name':'WEBDL-1080p','resolution':1080}}}
  other,_=episodes.normalize(self.m,row('Unknown Russian',[80,51]),base,'sonarr')
  lost,_=episodes.normalize(self.m,row('LostFilm',[2,51]),base,'sonarr')
  self.assertTrue(other['allowed']);self.assertGreater(other['score'],lost['score'])
  english,_=episodes.normalize(self.m,row('Unknown English',[80,50]),base,'sonarr')
  self.assertFalse(english['allowed'])
  wrong_quality,_=episodes.normalize(self.m,row('Unknown Russian',[80,51],qid=1),base,'sonarr')
  self.assertFalse(wrong_quality['allowed'])
  rejected,_=episodes.normalize(self.m,row('Unknown Russian',[80,51]),strict,'sonarr')
  preferred,_=episodes.normalize(self.m,row('LostFilm',[2,51,52]),strict,'sonarr')
  self.assertFalse(rejected['allowed']);self.assertTrue(preferred['allowed'])
  self.assertGreater(preferred['score'],other['score'])
 def test_applied_policy_is_reconfirmed_and_repairs_owned_drift(self):
  order=self.names[::-1];so.submit(STUDIOS,order);so.reconcile(self.m)
  next(i for p in self.arr.profiles if p['id']==7 for i in p['formatItems'] if i['format']==2)['score']=0
  so.reconcile(self.m)
  self.assertEqual(so.view(STUDIOS)['state'],'applied')
  self.assertEqual(next(i['score'] for p in self.arr.profiles if p['id']==7 for i in p['formatItems'] if i['format']==2),100)
 def test_catchall_excludes_every_named_studio_and_requires_russian(self):
  specs=so.catchall_payload(STUDIOS)['specifications']
  exclusions=[s for s in specs if s['negate'] and s['required']]
  self.assertEqual(len(exclusions),13)
  self.assertEqual({s['fields'][0]['value'] for s in exclusions},{s['pattern'] for s in STUDIOS if s['name']!=so.CATCHALL})
  def matches(title,russian=False):
   def hit(s):
    if s['implementation']=='LanguageSpecification':value=russian
    else:value=bool(re.search(s['fields'][0]['value'],title))
    return not value if s['negate'] else value
   return all(hit(s) for s in specs if s['required']) and any(hit(s) for s in specs if not s['required'])
  for title in ['LostFilm RUS','Red Head Sound RUS','Кураж-Бамбей RUS','sndk RUS','DMS RUS','NovaFilm RUS','ColdFilm RUS']:
   self.assertFalse(matches(title,True),title+' must never receive catchall boost')
  self.assertTrue(matches('Unknown MVO'))
  self.assertTrue(matches('Unknown',True))
  self.assertFalse(matches('Unknown English'))
 def test_pure_plan_has_no_io_and_initial_discovery_has_no_mutations(self):
  before=copy.deepcopy((self.arr.profiles,self.arr.formats))
  discovered=so.plan(STUDIOS,self.arr.profiles,self.arr.formats)
  self.assertFalse(discovered['profiles']);self.assertIsNone(discovered['createCatchall'])
  order=self.names[::-1];planned=so.plan(STUDIOS,self.arr.profiles,self.arr.formats,order)
  self.assertEqual(set(planned['profileIds']),{7,8,9})
  self.assertEqual(planned['scoreByName'][so.CATCHALL],409600)
  self.assertIsNotNone(planned['createCatchall'])
  self.assertEqual(before,(self.arr.profiles,self.arr.formats));self.assertEqual(self.arr.calls,[])
  so.reconcile(self.m)
  self.assertTrue(all(method=='GET' for _,method,_ in self.arr.calls))
 def test_full_profile_readback_corruption_never_reports_applied(self):
  for field in ['items','minFormatScore','veto','bonus','user','upgradeUntilFormatScore']:
   self.arr=Sonarr();api=self.arr.api
   def corrupt(path,method='GET',data=None):
    value=api(path,method,data)
    if method=='GET' and path.startswith('qualityprofile/'):
     if field=='items':value['items'][0]['allowed']=False
     elif field=='minFormatScore':value['minFormatScore']=0
     elif field=='upgradeUntilFormatScore':value[field]=123
     else:
      fid={'veto':50,'bonus':52,'user':53}[field]
      next(i for i in value['formatItems'] if i['format']==fid)['score']=123
    return value
   self.m.api=corrupt
   so.submit(STUDIOS,self.names[::-1]);so.reconcile(self.m)
   self.assertEqual(so.view(STUDIOS)['state'],'error',field)
   self.assertEqual(so.effective(STUDIOS),self.names)
 def test_existing_broad_catchall_is_repaired_and_read_back(self):
  self.arr=Sonarr(catchall=True);self.m.api=self.arr.api
  broad=next(f for f in self.arr.formats if f['id']==14)
  broad['specifications']=[{'name':'Broad','implementation':'ReleaseTitleSpecification','fields':[{'name':'value','value':'.*'}]}]
  so.submit(STUDIOS,self.names[::-1]);so.reconcile(self.m)
  self.assertEqual(so.view(STUDIOS)['state'],'applied')
  self.assertTrue(any(path=='customformat/14' and method=='PUT' for path,method,_ in self.arr.calls))
  current=next(f for f in self.arr.formats if f['id']==14)
  so.verify_catchall(so.catchall_payload(STUDIOS),current)
 def test_wrong_role_never_calls_sonarr(self):
  self.m.ROLE='web'
  with self.assertRaises(PermissionError):so.reconcile(self.m)
  self.assertEqual(self.arr.calls,[])
 def test_safety_veto_fail_closed(self):
  next(i for i in self.arr.profiles[3]['formatItems'] if i['format']==50)['score']=0
  so.submit(STUDIOS,self.names[::-1]);so.reconcile(self.m)
  self.assertEqual(so.view(STUDIOS)['state'],'error')
  self.assertFalse(any(method=='PUT' for _,method,_ in self.arr.calls))

if __name__=='__main__':unittest.main()
