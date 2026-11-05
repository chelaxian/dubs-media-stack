"""Shared owner preferences; cleanup and preference saves serialize per deletion."""
import contextlib
import fcntl
import json
import os
import secrets
import stat
import runtime as rt

NAME='watch-settings.json'
LOCK='watch-settings.lock'
FIELDS={'confirm_watch','delete_watched'}


def validate(value):
 if not isinstance(value,dict) or set(value)!=FIELDS or any(type(v) is not bool for v in value.values()):
  raise ValueError('Invalid watched preferences')
 return value


def current():
 try:
  fd=os.open(rt.state_root()/NAME,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
  with os.fdopen(fd) as f:
   st=os.fstat(f.fileno())
   if not stat.S_ISREG(st.st_mode) or st.st_size>2048:raise ValueError('Invalid settings file')
   return validate(json.load(f))
 except FileNotFoundError:
  return {'confirm_watch':False,'delete_watched':bool(rt.settings()['cleanup_enable'])}
 except (ValueError,OSError):
  return {'confirm_watch':True,'delete_watched':False}


def ensure_lock():
 try:
  fd=os.open(rt.state_root()/LOCK,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o640)
 except FileExistsError:return
 with os.fdopen(fd,'wb') as f:os.fchmod(f.fileno(),0o640)


@contextlib.contextmanager
def policy_lock():
 ensure_lock()
 fd=os.open(rt.state_root()/LOCK,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
 try:
  if not stat.S_ISREG(os.fstat(fd).st_mode):raise ValueError('Invalid policy lock')
  fcntl.flock(fd,fcntl.LOCK_EX)
  yield
 finally:os.close(fd)


@contextlib.contextmanager
def deletion_guard():
 with policy_lock():yield rt.cleanup_enabled()


def update(value):
 validate(value)
 with policy_lock():
  path=rt.state_root()/NAME;tmp=path.with_name('.'+NAME+'.'+secrets.token_hex(8))
  fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o640)
  try:
   with os.fdopen(fd,'w') as f:
    os.fchmod(f.fileno(),0o640);json.dump(value,f);f.flush();os.fsync(f.fileno())
   os.replace(tmp,path)
  finally:tmp.unlink(missing_ok=True)
 return current()
