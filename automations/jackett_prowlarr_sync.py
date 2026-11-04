#!/usr/bin/python3
"""Sync configured Jackett indexers into Prowlarr as Torznab entries.
Only indexers already configured by the operator inside Jackett are synced;
nothing is auto-enabled. Credentials come from config files, never arguments.
"""
import requests,json,fcntl,sys,time,os,xml.etree.ElementTree as ET
SRC=os.environ.get('DUBS_SRC',os.path.join(os.path.dirname(os.path.abspath(__file__)),'..','src'))
sys.path.insert(0,SRC)
import runtime as rt
SYNC_PROFILE='Dubs'
MINIMUM_SEEDERS=3
TIME_BUDGET=180

def open_lock(state_parent):
 """Exclusive run lock; None means another instance is already running."""
 path=os.path.join(state_parent,'jackett-prowlarr-sync.lock')
 lock=open(path,'w')
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:lock.close();return None
 return lock

def jackett_indexers(j,jbase,key):
 """Configured Torznab indexers from the Jackett catalogue (API-key protected)."""
 try:
  r=j.get(jbase+'/api/v2.0/indexers/all/results/torznab/api',
          params={'t':'indexers','configured':'true','apikey':key},timeout=20)
 except requests.RequestException as exc:
  # Never log the request URL: it carries the catalogue apikey.
  raise RuntimeError('Jackett catalogue unreachable (%s)'%type(exc).__name__) from exc
 if r.status_code != 200:
    raise RuntimeError('Jackett catalogue request failed (HTTP '+str(r.status_code)+')')
 root=ET.fromstring(r.content)
 if root.tag != 'indexers':
    raise RuntimeError('Unexpected Jackett catalogue response')
 return [{'id':x.attrib['id'],'name':x.findtext('title') or x.attrib['id']}
         for x in root.findall('indexer') if x.attrib.get('configured') == 'true']

def ensure_sync_profile(p,pbase,name=SYNC_PROFILE,minimum_seeders=MINIMUM_SEEDERS):
 """Id of the managed indexer (sync) profile (name=SYNC_PROFILE).

 The bootstrap-created profile is selected by NAME; a missing profile is
 created and a drifted minimumSeeders is repaired in place. The operator's
 'Standard' profile and any unrelated entries are never modified.
 """
 r=p.get(pbase+'indexerprofile',timeout=20);r.raise_for_status()
 existing=next((x for x in r.json() if x.get('name')==name),None)
 if existing and existing.get('minimumSeeders')==minimum_seeders:return existing['id']
 payload=dict(existing) if existing else {'name':name,'enableRss':True,
  'enableAutomaticSearch':True,'enableInteractiveSearch':True}
 payload['minimumSeeders']=minimum_seeders
 if existing:r=p.put(pbase+'indexerprofile/%d'%existing['id'],json=payload,timeout=20)
 else:r=p.post(pbase+'indexerprofile',json=payload,timeout=20)
 r.raise_for_status()
 return r.json()['id']

def sync_indexers(indexers,p,pbase,jbase,jkey,schema,profile_id,existing,deadline=None):
 """Add missing Jackett indexers to Prowlarr using the managed profile id."""
 added=[]
 if deadline is None:deadline=time.monotonic()+TIME_BUDGET
 for i in indexers:
  name=i['name']+' (Jackett)'
  if name in existing:continue
  if time.monotonic() >= deadline-5:
   print('Time budget reached; remaining sources will retry next run',flush=True);break
  x=json.loads(json.dumps(schema));x.pop('id',None);x['name']=name;x['appProfileId']=profile_id;x['enable']=True;x['tags']=[]
  values={'baseUrl':jbase+'/api/v2.0/indexers/'+i['id']+'/results/torznab','apiPath':'/api','apiKey':jkey,'torrentBaseSettings.appMinimumSeeders':MINIMUM_SEEDERS}
  for f in x['fields']:
   if f['name'] in values:f['value']=values[f['name']]
  try:
   r=p.post(pbase+'indexer/test',json=x,timeout=min(45,max(1,deadline-time.monotonic())))
  except requests.RequestException as exc:
   print('Test unavailable:',name,type(exc).__name__,flush=True);continue
  if not r.ok:print('Test failed:',name,r.status_code);continue
  try:
   r=p.post(pbase+'indexer',json=x,timeout=20)
  except requests.RequestException as exc:
   print('Save unavailable:',name,type(exc).__name__,flush=True);continue
  if not r.ok:
   # Status only: the response body may echo submitted credentials.
   print('Save failed:',name,r.status_code);continue
  added.append(name);print('Connected:',name)
 return added

def existing_names(p,pbase):
 r=p.get(pbase+'indexer',timeout=20);r.raise_for_status();return {x['name'] for x in r.json()}

def safe_main():
 state_parent=os.environ.get('DUBS_AUTOMATION_STATE','/run')
 lock=open_lock(state_parent)
 if lock is None:sys.exit(0)
 try:
  cfg=rt.connection('jackett');pconn=rt.connection('prowlarr')
  jbase=cfg['base_url'].rstrip('/');pbase=pconn['base_url']+'/api/v1/'
  # The administrative API requires a browser login. Use the API-key protected
  # Torznab catalogue so that dashboard authentication can stay enabled.
  j=requests.Session()
  indexers=jackett_indexers(j,jbase,cfg['key'])
  p=requests.Session();p.headers['X-Api-Key']=pconn['key']
  r=p.get(pbase+'indexer/schema',timeout=20);r.raise_for_status()
  schema=next(x for x in r.json() if x['implementation']=='Torznab')
  profile_id=ensure_sync_profile(p,pbase)
  existing=existing_names(p,pbase)
  deadline=time.monotonic()+TIME_BUDGET
  added=sync_indexers(indexers,p,pbase,jbase,cfg['key'],schema,profile_id,existing,deadline)
  if added:
   r=p.post(pbase+'command',json={'name':'ApplicationIndexerSync'},timeout=20);r.raise_for_status()
  print('Configured in Jackett:',len(indexers),'new connections:',len(added))
 finally:
  try:lock.close()
  except OSError:pass

if __name__=='__main__':
 safe_main()
