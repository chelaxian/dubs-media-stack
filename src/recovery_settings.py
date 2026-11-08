"""Owner-configured bounded intervals for panel download recovery."""
import json,os,secrets,stat
import runtime as rt

NAME='recovery-settings.json'
FIELDS={
 'search_retry_min':(15,5,1440),
 'search_deadline_sec':(120,30,300),
 'pause_grace_min':(2,1,120),
 'stall_min':(15,5,1440),
 'start_backoff_min':(60,10,1440),
 'indexer_recheck_min':(60,30,1440),
 'cleanup_min':(30,5,1440),
 'verify_retry_min':(5,1,1440),
 'start_retry_sec':(60,30,600),
 'start_attempts':(3,1,10),
}
def defaults():return {key:spec[0] for key,spec in FIELDS.items()}
def validate(value):
 if not isinstance(value,dict) or set(value)!=set(FIELDS):raise ValueError('Invalid recovery fields')
 for key,(_,low,high) in FIELDS.items():
  if type(value[key]) is not int or not low<=value[key]<=high:raise ValueError('Invalid recovery interval')
 return value
def current():
 try:
  fd=os.open(rt.state_root()/NAME,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
  with os.fdopen(fd) as f:
   st=os.fstat(f.fileno())
   if not stat.S_ISREG(st.st_mode) or st.st_size>2048:raise ValueError('Invalid recovery file')
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
