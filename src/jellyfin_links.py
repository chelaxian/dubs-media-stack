"""Read-only TV metadata -> credential-free Jellyfin deep links.

get_links(m) keeps the old URL-only contract used by the players flow.
get_library(m) extends the same mapping with localMedia file provenance;
the actual logic lives in local_media.py.
"""
import json,re,threading,urllib.parse
from pathlib import Path
import requests
import runtime as rt
LOCK=threading.Lock()
def _headers(cfg):
 return {'Authorization':'MediaBrowser Client="DubsMetadata", Device="Local worker", DeviceId="dubs-metadata-worker", Version="1.0", Token="'+cfg['token']+'"'}

def jf_get(cfg,path,params=None):
 r=requests.get(cfg['base_url']+'/'+path,params=params,headers=_headers(cfg),timeout=(3,15))
 if r.status_code==401:
  raise RuntimeError('Jellyfin metadata reader requires reconnection')
 r.raise_for_status();return r.json()

def _detail_url(cfg,iid):
 public=cfg.get('public','')
 parsed=urllib.parse.urlsplit(public)
 if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:return None
 params={'id':iid}
 if re.fullmatch('[a-fA-F0-9]{32}',cfg.get('serverId','')):params['serverId']=cfg['serverId']
 return cfg['public'].rstrip('/')+'/web/#/details?'+urllib.parse.urlencode(params)

def _episode_range(item):
 number=item.get('IndexNumber');end=item.get('IndexNumberEnd') or number
 if not isinstance(end,int) or not number<=end<=number+20:end=number
 return number,end

def _series_map(shows,sonarr):
 series_map={}
 for show in shows:
  ids={k.lower():str(v).lower() for k,v in show.get('ProviderIds',{}).items()};matches=[]
  for sr in sonarr:
   if (ids.get('tvdb')==str(sr['tvdbId']) or sr.get('imdbId') and ids.get('imdb')==str(sr['imdbId']).lower()):matches.append(sr['tvdbId'])
  if len(set(matches))==1:series_map[show['Id']]=matches[0]
 return series_map

def get_links(m):
 cfg=rt.connection('jellyfin')
 shows=jf_get(cfg,'Items',{'UserId':cfg['userId'],'IncludeItemTypes':'Series','Recursive':'true','Fields':'ProviderIds','Limit':5000})['Items']
 sonarr=[x['series'] for x in m.watching()];series_map=_series_map(shows,sonarr)
 items=jf_get(cfg,'Items',{'UserId':cfg['userId'],'IncludeItemTypes':'Episode','Recursive':'true','Limit':10000})['Items'];links={}
 for item in items:
  sid=series_map.get(item.get('SeriesId'));season=item.get('ParentIndexNumber');number=item.get('IndexNumber');iid=item.get('Id')
  if sid is None or season is None or number is None or not re.fullmatch('[a-fA-F0-9]{32}',iid or ''):continue
  url=_detail_url(cfg,iid);start,end=_episode_range(item)
  for n in range(start,end+1):links[f'{sid}:{season}:{n}']=url
 return links

def get_library(m):
 import local_media
 return local_media.get_library(m)
