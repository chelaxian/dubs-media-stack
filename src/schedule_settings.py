"""Owner-managed scheduler preferences, read afresh by every service role."""
import json,os,re,secrets,stat
import runtime as rt
NAME='schedule-settings.json'
def defaults():return {'sync_minutes':30,'upgrade_time':f"{rt.settings()['daily_upgrade_hour']:02}:00",'upgrade_interval_hours':24}
def validate(value):
 if not isinstance(value,dict) or set(value)!=set(defaults()):raise ValueError('Invalid schedule fields')
 if type(value['sync_minutes']) is not int or not 5<=value['sync_minutes']<=1440:raise ValueError('Sync interval must be 5–1440 minutes')
 if type(value['upgrade_interval_hours']) is not int or not 1<=value['upgrade_interval_hours']<=168:raise ValueError('Upgrade interval must be 1–168 hours')
 if not isinstance(value['upgrade_time'],str) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]',value['upgrade_time']):raise ValueError('Invalid time')
 return value

def current():
 try:
  fd=os.open(rt.state_root()/NAME,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
  with os.fdopen(fd) as f:
   st=os.fstat(f.fileno())
   if not stat.S_ISREG(st.st_mode) or st.st_size>2048:raise ValueError('Invalid schedule file')
   return validate(json.load(f))
 except (OSError,ValueError):return defaults()
def update(value):
 validate(value);path=rt.state_root()/NAME;tmp=path.with_name('.'+NAME+'.'+secrets.token_hex(8))
 fd=os.open(tmp,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o640)
 try:
  with os.fdopen(fd,'w') as f:os.fchmod(f.fileno(),0o640);json.dump(value,f);f.flush();os.fsync(f.fileno())
  os.replace(tmp,path)
 finally:tmp.unlink(missing_ok=True)
 return current()
