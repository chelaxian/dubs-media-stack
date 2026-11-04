"""Credential-free, allowlisted player links for an owned cached episode."""
import re,urllib.parse,uuid
from pathlib import Path
import runtime as rt

def links(m,episode_id,user):
 e=m.episodes.read(m.WORKER_STATE/'myshows-episodes.json',{}).get('episodes',{}).get(str(episode_id))
 if not e:raise ValueError('Unknown episode')
 url=urllib.parse.urlsplit(e.get('jellyfinUrl') or '')
 public=urllib.parse.urlsplit(rt.settings()['jellyfin_url'])
 if not public.hostname or url.scheme!='https' or url.hostname!=public.hostname:raise ValueError('Not in Jellyfin')
 q=urllib.parse.parse_qs(url.fragment.partition('?')[2]);item=q.get('id',[''])[0];server=q.get('serverId',[''])[0]
 if not all(re.fullmatch('[a-fA-F0-9]{32}',v or '') for v in [item,server,user]):raise ValueError('Invalid library identifiers')
 result={'swiftfin':f'swiftfin://{server}/{user}/item/{item}','fladder':'fladder://details?'+urllib.parse.urlencode({'id':item,'userId':user,'serverId':server}),'streamyfin':'streamyfin://items/page?'+urllib.parse.urlencode({'id':item}),'findroid':'jellyfin://item/'+str(uuid.UUID(hex=item)),'browser':e['jellyfinUrl']}
 try:tmdb=m.api('series/'+str(e['seriesId'])).get('tmdbId')
 except Exception:tmdb=None
 if isinstance(tmdb,int) and tmdb>0 and isinstance(e.get('season'),int) and isinstance(e.get('number'),int):result['infuse']=f'infuse://series/{tmdb}-{e["season"]}-{e["number"]}'
 return result
