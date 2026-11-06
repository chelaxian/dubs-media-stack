"""Web-owned name permutation; only the operations worker writes Sonarr scores.

The catalog remains stable for legacy numeric series preferences. Neither the
browser nor persisted order can supply patterns, format ids, scores or paths.
"""
import copy,json,os,secrets,stat,time
import runtime as rt

REQUEST='studio-order.json'
STATUS='studio-order-status.json'
BASE_NAMES={'Dubs RU 2160p','Dubs RU 1080p','4K / 1080p — RU preferred'}
CATCHALL='Другая русская озвучка'

def validate(order,studios):
 names=[s['name'] for s in studios]
 if (not isinstance(order,list) or len(order)!=len(names) or
     any(not isinstance(n,str) or len(n)>200 for n in order) or
     len(set(order))!=len(names) or set(order)!=set(names)):
  raise ValueError('Требуется полный список известных студий без повторов')
 return order

def read(path):
 try:
  fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
  with os.fdopen(fd) as f:
   st=os.fstat(f.fileno())
   if not stat.S_ISREG(st.st_mode) or st.st_size>8192:raise ValueError('Invalid order file')
   return json.load(f)
 except FileNotFoundError:return {}

def atomic(path,data):
 tmp=path.with_name('.'+path.name+'.'+secrets.token_hex(8))
 fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o640)
 try:
  with os.fdopen(fd,'w') as f:
   os.fchmod(f.fileno(),0o640);json.dump(data,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
  os.replace(tmp,path)
 finally:tmp.unlink(missing_ok=True)

def effective(studios):
 # Read on every use: long-running web/worker processes see confirmed updates.
 try:return validate(read(rt.worker_root()/STATUS).get('order'),studios)
 except (ValueError,OSError):return [s['name'] for s in studios]

def indices(studios):
 names=effective(studios)
 return [next(i for i,s in enumerate(studios) if s['name']==n) for n in names]

def view(studios):
 request=read(rt.state_root()/REQUEST);status=read(rt.worker_root()/STATUS)
 order=effective(studios)
 if request:
  desired=validate(request.get('order'),studios)
  matched=status.get('revision')==request.get('revision')
  state=status.get('state','pending') if matched else 'pending'
 else:desired=order;state=status.get('state','pending')
 return {'order':desired,'effectiveOrder':order,'state':state,
         'message':status.get('message','Ожидает подтверждения Sonarr') if not request or matched else 'Сохранено. Ожидает применения в Sonarr'}

def submit(studios,order):
 validate(order,studios)
 atomic(rt.state_root()/REQUEST,{'order':order,'revision':secrets.token_hex(16)})
 return view(studios)

def owned_formats(studios):
 # Names emitted by installer.bootstrap._ru_format_set, not a prefix match.
 return {'Озвучка %02d — %s'%(i+1,s['name']):s['name'] for i,s in enumerate(studios)}

def owned_profiles(profiles,studios):
 bases=[p for p in profiles if p.get('name') in BASE_NAMES]
 if not bases or len({p['name'] for p in bases})!=len(bases):raise ValueError('Managed profiles missing')
 clones={'Сериал / база %s / %s / %s'%(p['id'],s['name'],mode)
         for p in bases for s in studios for mode in ('только','приоритет')}
 return bases+[p for p in profiles if p.get('name') in clones]

def catchall_payload(studios):
 """Only other Russian audio: all named exclusions AND Russian evidence.

 Sonarr ANDs required specs and ORs the remaining evidence specs. Patterns
 come exclusively from the trusted server catalog, never the submitted order.
 """
 specs=[{'name':'Exclude '+s['name'],'implementation':'ReleaseTitleSpecification',
         'negate':True,'required':True,'fields':[{'name':'value','value':s['pattern']}]}
        for s in studios if s['name']!=CATCHALL]
 # Same generic Russian markers as installer.bootstrap.RU_COMMON. A catchall
 # by exclusion alone would also match English releases; it is not evidence.
 russian=(r'(?is)(?:(?<![a-zа-яё0-9])(?:rus(?:sian|sia)?|ru|рус(?:ск(?:ий|ая|ое|ого)?)?|dvo|mvo'
          r'|newstudio|tvshows|baibako|jaskier|амедиа|перевод\w*|озвучк\w*|дубляж\w*|dubbing|мультираздач\w*)'
          r'(?![a-zа-яё0-9])|(?=[\s\S]*[а-яё])(?=[\s\S]*(?<![a-zа-яё0-9])dub(?![a-zа-яё0-9])))')
 specs += [{'name':'Russian audio title','implementation':'ReleaseTitleSpecification',
            'negate':False,'required':False,'fields':[{'name':'value','value':russian}]},
           {'name':'Russian language','implementation':'LanguageSpecification',
            'negate':False,'required':False,'fields':[{'name':'value','value':11},{'name':'exceptLanguage','value':False}]}]
 return {'name':next(n for n,s in owned_formats(studios).items() if s==CATCHALL),
         'includeCustomFormatWhenRenaming':False,'specifications':specs}

def spec_identity(specs):
 # Sonarr enriches GET specs with UI/schema metadata; compare executable
 # identity, not incidental labels/help text or field/spec ordering.
 return sorted((s['name'],s['implementation'],bool(s.get('negate')),bool(s.get('required')),
                json.dumps(sorted((f['name'],f.get('value')) for f in s.get('fields',[])),sort_keys=True)) for s in specs)

def plan(studios,profiles,formats,order=None,veto_name='ОБЯЗАТЕЛЬНО RU — запрет без русского'):
 """Pure dry plan from supplied API snapshots. No IO, config reads or writes.

 With order=None discovers the current base order and returns no mutations.
 When catchall creation is needed, profile payloads are deferred until Sonarr
 assigns its id; scoreByName/profileIds still describe the complete mapping.
 """
 owned=owned_profiles(profiles,studios)
 for profile in owned:
  items=profile.get('formatItems',[])
  if not isinstance(profile.get('minFormatScore'),int) or profile['minFormatScore']<1:raise ValueError('Russian minimum missing')
  if not any(i.get('name')==veto_name and i.get('score',0)<=-10000000 for i in items):raise ValueError('Russian veto missing')
 ids={};catchall=None
 for fname,name in owned_formats(studios).items():
  hits=[f for f in formats if f.get('name')==fname]
  if not hits and name==CATCHALL:continue
  if len(hits)!=1:raise ValueError('Managed formats missing')
  ids[hits[0]['id']]=name
  if name==CATCHALL:catchall=hits[0]
 for profile in owned:
  if not set(ids)<={i['format'] for i in profile['formatItems']}:raise ValueError('Managed profile incomplete')
 if order is None:
  base=next((p for p in owned if p['name']=='4K / 1080p — RU preferred'),owned[0])
  by_name={ids[i['format']]:i['score'] for i in base['formatItems'] if i['format'] in ids}
  discovered=sorted([s['name'] for s in studios],key=lambda n:-by_name.get(n,0))
  return {'order':discovered,'profileIds':[p['id'] for p in owned],
          'scoreByName':by_name,'profiles':[],'createCatchall':None,'updateCatchall':None}
 order=validate(order,studios)
 scores={name:(0 if rank==len(order)-1 else 100*2**(len(order)-2-rank)) for rank,name in enumerate(order)}
 creation=None;repair=None
 if order[-1]!=CATCHALL:
  expected=catchall_payload(studios)
  if catchall is None:creation=expected
  elif spec_identity(catchall.get('specifications',[]))!=spec_identity(expected['specifications']):
   repair=dict(copy.deepcopy(catchall),**expected)
 payloads=[]
 if creation is None:
  for profile in owned:
   payload=copy.deepcopy(profile)
   for item in payload['formatItems']:
    if item['format'] in ids:item['score']=scores[ids[item['format']]]
   payloads.append(payload)
 return {'order':order,'profileIds':[p['id'] for p in owned], 'scoreByName':scores,
         'profiles':payloads,'createCatchall':creation,'updateCatchall':repair}

def verify_profile(expected,actual):
 # Compare all existing profile fields, including every quality group, RU
 # score, user score, selected bonus and minimum; item ordering is immaterial.
 critical={'minFormatScore','upgradeUntilFormatScore','cutoffFormatScore','minUpgradeFormatScore','items','cutoff','upgradeAllowed'}
 for key in set(expected)|critical:
  value=expected.get(key)
  if key=='formatItems':
   if {i['format']:i for i in actual.get(key,[])}!={i['format']:i for i in value}:raise ValueError('Score verification failed')
  elif actual.get(key)!=value:raise ValueError('Profile verification failed')

def verify_catchall(expected,actual):
 for key in ('name','includeCustomFormatWhenRenaming'):
  if actual.get(key)!=expected.get(key):raise ValueError('Catchall verification failed')
 if spec_identity(actual.get('specifications',[]))!=spec_identity(expected['specifications']):raise ValueError('Catchall verification failed')

def reconcile(m):
 """Operations-only idempotent apply/retry. Startup discovery never mutates Sonarr."""
 if m.ROLE!='operations':raise PermissionError('Operations worker only')
 path=rt.worker_root()/STATUS
 confirmed=effective(m.STUDIOS);revision=None
 try:
  request=read(rt.state_root()/REQUEST);revision=request.get('revision')
  if request and (set(request)!={'order','revision'} or not isinstance(revision,str) or len(revision)!=32):raise ValueError('Invalid order request')
  desired=validate(request.get('order'),m.STUDIOS) if request else None
  profiles=m.api('qualityprofile');formats=m.api('customformat')
  change=plan(m.STUDIOS,profiles,formats,desired,rt.settings()['veto_format'])
  if not request:
   atomic(path,{'order':change['order'],'state':'applied','message':'Применено в Sonarr','checked':time.time()})
   return
  atomic(path,{'order':confirmed,'revision':revision,'state':'pending','message':'Применяется в Sonarr'})
  for key,method in (('createCatchall','POST'),('updateCatchall','PUT')):
   payload=change[key]
   if payload:
    endpoint='customformat'+('/'+str(payload['id']) if method=='PUT' else '')
    m.api(endpoint,method,payload)
    formats=m.api('customformat')
    hits=[f for f in formats if f.get('name')==payload['name']]
    if len(hits)!=1:raise ValueError('Catchall missing after apply')
    verify_catchall(payload,hits[0])
    # Sonarr adds a zero-score new format to all profiles. Refresh snapshots
    # to preserve every unrelated field and score in the owned profile PUTs.
    profiles=m.api('qualityprofile')
    change=plan(m.STUDIOS,profiles,formats,desired,rt.settings()['veto_format'])
    if change['createCatchall'] or change['updateCatchall']:raise ValueError('Catchall not confirmed')
  by_id={p['id']:p for p in profiles}
  for payload in change['profiles']:
   if payload!=by_id[payload['id']]:
    m.api('qualityprofile/'+str(payload['id']),'PUT',payload)
    verify_profile(payload,m.api('qualityprofile/'+str(payload['id'])))
  # Verify the full owned set again: concurrent edits or clones defer success.
  final=owned_profiles(m.api('qualityprofile'),m.STUDIOS)
  if {p['id'] for p in final}!=set(change['profileIds']):raise ValueError('Concurrent profile change')
  expected={p['id']:p for p in change['profiles']}
  for profile in final:verify_profile(expected[profile['id']],profile)
  atomic(path,{'order':desired,'revision':revision,'state':'applied','message':'Применено в Sonarr','checked':time.time()})
 except Exception:
  atomic(path,{'order':confirmed,'revision':revision,'state':'error','message':'Sonarr не подтвердил применение. Автоматический повтор через 30 секунд','checked':time.time()})
