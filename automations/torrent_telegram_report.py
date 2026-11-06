#!/usr/bin/env python3
"""Read-only report for two requested downloads, delivered to the configured owner.

Run on the PVE host. Secrets and the Telegram recipient remain in their own
containers. No torrent, router, media or scheduler settings are changed here.
"""
import fcntl,json,os,subprocess,sys,time
from pathlib import Path

STATE=Path('/var/lib/dubs-torrent-report')
INTERVAL=6*3600
CASA_CODE=r'''import json,sys
sys.path.insert(0,"/opt/dubs-releases/20261104-202629/src")
import control
h="b52bf67472b7d1ee9315f275f7de43371e34278e"
rows=control.trpc("torrent-get",{"ids":[h],"fields":["status","downloadedEver","rateDownload","peersConnected","peersSendingToUs","trackerStats","errorString"]})["torrents"]
t=rows[0] if rows else None
if t:
 stats=t.pop("trackerStats");t["seeds"]=max([x.get("seederCount",0) or 0 for x in stats]+[0]);t["trackersOK"]=sum(bool(x.get("lastAnnounceSucceeded")) for x in stats);t["error"]=bool(t.pop("errorString", ""))
e=control.api("episode/354")
print("DUBS_REPORT_JSON="+json.dumps({"monkeys":t,"onepieceReady":bool(e.get("hasFile") and e.get("episodeFileId"))}))'''
SEND_CODE=r'''import json,os,pathlib,subprocess,sys
cfg=json.loads(pathlib.Path("/root/.openclaw/openclaw.json").read_text());t=cfg.get("channels",{}).get("telegram",{});a=t.get("allowFrom",[])
if not t.get("enabled") or len(a)!=1 or not str(a[0]).isdigit() or int(a[0])<=0:raise SystemExit("Personal Telegram recipient is not uniquely configured")
msg=sys.stdin.read(2000)
r=subprocess.run(["openclaw","message","send","--channel","telegram","--target",str(a[0]),"--message",msg,"--json"],capture_output=True,text=True,timeout=90,env={**os.environ,'PATH':'/usr/local/bin:/usr/bin:/bin'})
if r.returncode:raise SystemExit("Telegram delivery failed")
try:
 result=json.loads(r.stdout)
 if result.get("error") or result.get("success") is False:raise ValueError()
except (ValueError,AttributeError):raise SystemExit("Telegram delivery response was not confirmed")
print("delivery confirmed")'''

def snapshot():
 p=subprocess.run(['pct','exec','120','--','python3','-c',CASA_CODE],capture_output=True,text=True,timeout=60,check=True)
 line=next((x for x in p.stdout.splitlines() if x.startswith('DUBS_REPORT_JSON=')),None)
 if not line:raise RuntimeError('CasaOS did not return status')
 return json.loads(line.split('=',1)[1])

def category(t):
 if not t:return 'missing'
 if t.get('error'):return 'error'
 if t.get('status')==0:return 'paused'
 if t.get('downloadedEver',0)>0:return 'progress'
 if t.get('peersSendingToUs',0)>0 or t.get('rateDownload',0)>0:return 'connected'
 return 'waiting'

def message(data):
 t=data['monkeys'];cat=category(t)
 one='файл импортирован в Sonarr' if data['onepieceReady'] else 'импорт пока не подтверждён'
 lines=['Dubs · статус загрузок','Ван-Пис S02E01: '+one+'.']
 if cat=='missing':lines.append('12 обезьян S03: торрент не найден в Transmission; требуется проверка.')
 elif cat=='error':lines.append('12 обезьян S03: Transmission сообщает об ошибке; требуется проверка клиента.')
 elif cat=='paused':lines.append('12 обезьян S03: торрент остановлен; автоматический запуск не выполнялся.')
 else:
  lines.append('12 обезьян S03: скачано %.1f МиБ, пиров %d, отдающих %d, сидов по трекерам %d.'%(t.get('downloadedEver',0)/1048576,t.get('peersConnected',0),t.get('peersSendingToUs',0),t.get('seeds',0)))
  if cat=='waiting':lines.append('Вероятная причина: нет доступных раздающих; это предположение, не доказательство блокировки.')
  elif cat=='progress':lines.append('Данные были получены; проверьте текущий прогресс в Dubs.')
 return '\n'.join(lines)

def main():
 STATE.mkdir(mode=0o700,parents=True,exist_ok=True)
 with (STATE/'lock').open('w') as lock:
  try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:return
  data=snapshot();now=time.time();path=STATE/'state.json'
  try:old=json.loads(path.read_text())
  except (OSError,ValueError):old={}
  signature={'monkeys':category(data['monkeys']),'onepieceReady':data['onepieceReady']}
  if signature==old.get('signature') and now-old.get('sentAt',0)<INTERVAL:return
  p=subprocess.run(['pct','exec','121','--','python3','-c',SEND_CODE],input=message(data),text=True,capture_output=True,timeout=110)
  if p.returncode:raise RuntimeError('Telegram report delivery failed')
  tmp=STATE/'state.tmp';fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
  with os.fdopen(fd,'w') as f:json.dump({'signature':signature,'sentAt':now},f);f.flush();os.fsync(f.fileno())
  os.replace(tmp,path);print('Telegram delivery confirmed')
if __name__=='__main__':
 try:main()
 except Exception as exc:print('Dubs report error:',type(exc).__name__,file=sys.stderr);sys.exit(1)
