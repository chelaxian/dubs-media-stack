"""Read-only TV metadata -> credential-free Jellyfin deep links."""
import json,re,threading,urllib.parse
from pathlib import Path
import requests
import runtime as rt
LOCK=threading.Lock()
def get_links(m):
 cfg=rt.connection('jellyfin');base=cfg['base_url']
 auth=lambda:'MediaBrowser Client="DubsMetadata", Device="Local worker", DeviceId="dubs-metadata-worker", Version="1.0", Token="'+cfg['token']+'"'
 def get(path,params=None):
  r=requests.get(base+'/'+path,params=params,headers={'Authorization':auth()},timeout=(3,15))
  if r.status_code==401:
   raise RuntimeError('Jellyfin metadata reader requires reconnection')
  r.raise_for_status();return r.json()
 shows=get('Items',{'UserId':cfg['userId'],'IncludeItemTypes':'Series','Recursive':'true','Fields':'ProviderIds','Limit':5000})['Items'];sonarr=[x['series'] for x in m.watching()];series_map={}
 for show in shows:
  ids={k.lower():str(v).lower() for k,v in show.get('ProviderIds',{}).items()};matches=[]
  for sr in sonarr:
   if (ids.get('tvdb')==str(sr['tvdbId']) or sr.get('imdbId') and ids.get('imdb')==str(sr['imdbId']).lower()):matches.append(sr['tvdbId'])
  if len(set(matches))==1:series_map[show['Id']]=matches[0]
 items=get('Items',{'UserId':cfg['userId'],'IncludeItemTypes':'Episode','Recursive':'true','Limit':10000})['Items'];links={}
 for item in items:
  sid=series_map.get(item.get('SeriesId'));season=item.get('ParentIndexNumber');number=item.get('IndexNumber');iid=item.get('Id')
  if sid is None or season is None or number is None or not re.fullmatch('[a-fA-F0-9]{32}',iid or ''):continue
  params={'id':iid}
  if re.fullmatch('[a-fA-F0-9]{32}',cfg.get('serverId','')):params['serverId']=cfg['serverId']
  end=item.get('IndexNumberEnd') or number
  if not isinstance(end,int) or not number<=end<=number+20:end=number
  for n in range(number,end+1):links[f'{sid}:{season}:{n}']=cfg['public'].rstrip('/')+'/web/#/details?'+urllib.parse.urlencode(params)
 return links
