#!/usr/bin/python3
"""Retry only monitored, aired episodes from the past 48h; no historical backlog."""
import json,time,sys,datetime,os
from pathlib import Path
import requests
SRC=os.environ.get('DUBS_SRC',str(Path(__file__).resolve().parents[1]/'src'))
sys.path.insert(0,SRC)
import runtime as rt
cfg=rt.connection('sonarr');base=cfg['base_url']+'/api/v3';s=requests.Session();s.headers['X-Api-Key']=cfg['key']
state=Path(os.environ.get('DUBS_RECENT_STATE',str(rt.state_root().parent/'media-recent-episodes'/'state.json')));state.parent.mkdir(parents=True,exist_ok=True)
def get(path):r=s.get(base+'/'+path,timeout=30);r.raise_for_status();return r.json()
now=time.time();previous=json.loads(state.read_text()) if state.exists() else {};selected=[]
if any(x.get('name')=='EpisodeSearch' and x.get('status') in ['queued','started'] for x in get('command')):print('Episode search already running');sys.exit(0)
for series in get('series'):
 if not series.get('monitored'):continue
 for ep in get('episode?seriesId='+str(series['id'])):
  if not ep.get('monitored') or ep.get('hasFile') or not ep.get('airDateUtc'):continue
  aired=datetime.datetime.fromisoformat(ep['airDateUtc'].replace('Z','+00:00')).timestamp()
  if 0<=now-aired<=48*3600 and now-previous.get(str(ep['id']),0)>6*3600:selected.append((aired,ep['id']))
ids=[x[1] for x in sorted(selected,reverse=True)[:10]];print('Recent monitored episodes eligible for retry:',len(ids))
if ids and '--dry-run' not in sys.argv:
 r=s.post(base+'/command',json={'name':'EpisodeSearch','episodeIds':ids},timeout=30);r.raise_for_status()
 for eid in ids:previous[str(eid)]=now
 state.write_text(json.dumps({k:v for k,v in previous.items() if now-v<7*86400}))
