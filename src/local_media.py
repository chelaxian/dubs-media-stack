"""Actual local-file provenance for jellyfin_links.get_library(m).

For every watched episode the module answers one question honestly: what file
is registered for it RIGHT NOW, how does it look (video quality) and which
audio tracks does it actually carry (language, explicit dub tag, codec,
channels). Snapshot statuses:

  verified  fresh data from the live API of the named source;
  unknown   this round found nothing actual (no file / no usable streams /
            ambiguous data) and no previous verified snapshot exists;
  stale     a previously verified snapshot exists, but this round could not
            re-verify it (API failure, file deregistered/replaced). Stale
            entries keep their OLD verifiedAt so they are never mistaken
            for a current snapshot.

Honesty rules:
  - Jellyfin dimensions come only from Type=Video streams; embedded cover
    art (EmbeddedImage and friends) never sets width/height;
  - a track's `studio` is set only when its explicit Title matches a
    canonical studios.json pattern; catch-all "bucket" patterns (they match
    arbitrary probe text) never identify anything; more than one distinct
    studio match inside one title stays null; the raw explicit Title is
    kept in `title`, untagged/unknown tracks keep studio null;
  - release names, Sonarr history, profile preferences and Sonarr
    releaseGroup never establish a studio; Sonarr mediaInfo (resolution,
    audioLanguages, audioStreamCount) may only provide summary fallback
    tracks with studio null;
  - Sonarr fallback requires episode.hasFile AND the episodeFileId to still
    be registered for the series; a deregistered id degrades to unknown or,
    with a previous snapshot, stale; zero-length files carry no quality;
  - output contains no tokens, credentials, raw API URLs or filesystem
    paths, and bounded sanitized display strings only.

Network stays bounded and reuses the existing exact rt.connection config:
two Jellyfin requests (series, then Episodes paginated with batched
MediaSources/MediaStreams fields) plus at most two Sonarr requests per
watched series, like episodes.refresh already does.
"""
import json,os,re,secrets,time
from pathlib import Path
import runtime as rt
import jellyfin_links

_PAGE=1000;_MAX_PAGES=10
_MAX_TRACKS=32;_MAX_TITLE=200;_MAX_ENTRIES=50000
_CACHE_NAME='local-media.json'
# Catches "matches everything" bucket patterns (e.g. the "Другая русская
# озвучка" fallback entry in studios.json): a pattern that matches neutral
# probe text cannot identify a specific studio and is never used for tagging.
_PROBE='Neitralnyj obrazec teksta 2024'
_LANGUAGES={'rus':'Русский','eng':'Английский','ukr':'Украинский','jpn':'Японский','kor':'Корейский',
            'zho':'Китайский','chi':'Китайский','ger':'Немецкий','deu':'Немецкий','fre':'Французский',
            'fra':'Французский','spa':'Испанский','ita':'Итальянский','pol':'Польский','cze':'Чешский',
            'heb':'Иврит'}
_CHANNELS={1:'1.0',2:'2.0',6:'5.1',8:'7.1'}
_ITEM_ID=re.compile('[a-fA-F0-9]{32}')
_RES=re.compile(r'(\d{2,5})x(\d{2,5})')

def _text(value):
 """Bounded, control-char-free display text; anything else becomes None."""
 if not isinstance(value,str):return None
 cleaned=' '.join(''.join(c for c in value if c.isprintable() or c.isspace()).split())
 if re.search(r'(?i)(https?://|[/\\]|(?:token|apikey|password|authorization)\s*[:=])',cleaned):return None
 return cleaned[:_MAX_TITLE] or None

def _studios():
 try:
  data=json.loads(rt.studios_path().read_text())
 except (OSError,ValueError):return []
 return [x for x in data if isinstance(x,dict)] if isinstance(data,list) else []

def _canonical_studio(studios,title):
 """Canonical studio name for an explicit track title, else None."""
 title=_text(title)
 if not title:return None
 found=set()
 for entry in studios:
  name=entry.get('name');pattern=entry.get('pattern')
  if not isinstance(name,str) or not isinstance(pattern,str):continue
  try:
   if re.search(pattern,_PROBE):continue
   if re.search(pattern,title):found.add(name)
  except re.error:continue
 return next(iter(found)) if len(found)==1 else None

def _dim(value):
 return value if isinstance(value,int) and 0<value<100000 else None

def _channels(value):
 if isinstance(value,float) and value.is_integer():value=int(value)
 return value if isinstance(value,int) and 0<value<64 else None

def _quality_label(video):
 height=video.get('Height') if isinstance(video,dict) else None
 label=('%dp'%height) if isinstance(height,int) and 0<height<100000 else None
 codec=_text(video.get('Codec')) if isinstance(video,dict) else None
 if codec:label=(label+' '+codec.upper()) if label else codec.upper()
 return label

def _video(streams):
 """Quality facts from the single actual video stream, else None."""
 vids=[s for s in streams if isinstance(s,dict) and s.get('Type')=='Video']
 if not vids:return None
 defaults=[s for s in vids if s.get('IsDefault')]
 pick=defaults[0] if len(defaults)==1 else (vids[0] if len(vids)==1 else None)
 if pick is None:return None
 hdr=pick.get('VideoRangeType') or pick.get('VideoRange')
 if not isinstance(hdr,str) or not hdr.strip() or hdr=='Unknown':hdr=None
 codec=_text(pick.get('Codec'))
 return {'label':_quality_label(pick),'width':_dim(pick.get('Width')),'height':_dim(pick.get('Height')),
         'codec':codec,'hdr':_text(hdr),'source':None}

def _track_label(language,codec,channels,studio):
 parts=[_LANGUAGES.get(language) or language,(codec or '').upper() or None,
        _CHANNELS.get(channels) if channels else None,studio]
 parts=[p for p in parts if p]
 return ' · '.join(parts) if parts else None

def _tracks(streams,studios,origin):
 out=[]
 for s in streams if isinstance(streams,list) else []:
  if not isinstance(s,dict) or s.get('Type')!='Audio':continue
  if len(out)>=_MAX_TRACKS:break
  index=s.get('Index');language=_text(s.get('Language'))
  codec=_text(s.get('Codec'));channels=_channels(s.get('Channels'))
  title=_text(s.get('Title'));studio=_canonical_studio(studios,title)
  out.append({'index':index if isinstance(index,int) else None,'language':language,
              'languageLabel':_LANGUAGES.get(language),'title':title,
              'studio':studio,'codec':codec,'channels':channels,
              'isDefault':s.get('IsDefault') if isinstance(s.get('IsDefault'),bool) else None,
              'label':_track_label(language,codec,channels,studio),
              'source':origin})
 return out

def _signature(quality,tracks):
 """None-safe comparable fingerprint of quality facts and audio tracks."""
 def part(v):return '' if v is None else str(v)
 audio=tuple(sorted(tuple(part(t[f]) for f in ('language','codec','channels','studio','title'))
                    for t in tracks))
 video=tuple(sorted((k,part(v)) for k,v in (quality or {}).items()))
 return video,audio

def _source_variants(item):
 """(streams, mediaSource) of every actual version; item-level fallback."""
 out=[]
 for src in item.get('MediaSources') or []:
  if isinstance(src,dict) and isinstance(src.get('MediaStreams'),list):out.append((src['MediaStreams'],src))
 if 'MediaSources' not in item and isinstance(item.get('MediaStreams'),list):out.append((item['MediaStreams'],{}))
 return out

def _jf_identity(source):
 """INTERNAL file identity (basename/size only, never output)."""
 path=source.get('Path')
 base=str(path).replace('\\','/').rsplit('/',1)[-1] if isinstance(path,str) and path.strip() else None
 base=_text(base)
 size=source.get('Size')
 return {'base':base,'size':size if isinstance(size,int) and size>0 else None}

def _unknown(origin,conflict=False):
 media={'status':'unknown','quality':None,'audioTracks':[],'verifiedAt':None,'source':origin}
 if conflict:media['conflict']=True
 return media

def _item_media(item,studios):
 """Verified snapshot from one Jellyfin item, None, or conflict-unknown."""
 if item.get('IsVirtualItem') or item.get('LocationType') not in (None,'FileSystem'):return None
 variants=_source_variants(item)
 if not variants:return None
 snaps=[]
 for streams,source in variants:
  video=_video(streams)
  if video is None or source.get('Size')==0:return None  # a version without an actual video stream cannot be claimed
  snaps.append((video,_tracks(streams,studios,'jellyfin'),_jf_identity(source)))
 first=snaps[0]
 for other in snaps[1:]:
  if _signature(other[0],other[1])!=_signature(first[0],first[1]):
   return _unknown('jellyfin',conflict=True)  # several conflicting versions: claim none
  if first[2]['base'] is not None and other[2]['base'] is not None and \
     (first[2]['base']!=other[2]['base'] or first[2]['size']!=other[2]['size']):
   return _unknown('jellyfin',conflict=True)  # identical streams but different files
 return {'status':'verified','quality':first[0],'audioTracks':first[1],
         'verifiedAt':time.time(),'source':'jellyfin','identity':first[2]}

def _parse_resolution(raw):
 m=_RES.fullmatch(raw) if isinstance(raw,str) else None
 return (_dim(int(m[1])),_dim(int(m[2]))) if m else (None,None)

def _sonarr_media(epfile):
 """Summary snapshot from a REGISTERED Sonarr episodeFile.

 mediaInfo is actual ffprobe output: dimensions/codec/hdr become quality
 facts (label derived from the resolution string, never from release-derived
 quality names). audioCodec/audioChannels/audioStreamCount are a SUMMARY:
 they may only describe individual tracks when exactly one audio stream
 exists; with several streams the per-track codec/channels/default stay
 null. Languages are assigned by stream position only when the language
 list matches the stream count. Studios stay null: no releaseGroup or
 release name may ever tag one.
 """
 if not isinstance(epfile,dict):return None
 mi=epfile.get('mediaInfo')
 if not isinstance(mi,dict):return None
 size=epfile.get('size')
 if isinstance(size,(int,float)) and size<=0:return None  # zero-length placeholder
 width,height=_parse_resolution(mi.get('resolution'))
 width=_dim(mi.get('width')) or width; height=_dim(mi.get('height')) or height
 codec=_text(mi.get('videoCodec'));hdr=_text(mi.get('videoDynamicRange')) or None
 label=('%dp %s'%(height,codec.upper()) if codec else '%dp'%height) if height else (codec.upper() if codec else None)
 audio_codec=_text(mi.get('audioCodec'));channels=_channels(mi.get('audioChannels'))
 count=mi.get('audioStreamCount');count=count if isinstance(count,int) and 0<count<=_MAX_TRACKS else 0
 raw_languages=mi.get('audioLanguages')
 languages=[_text(x) for x in raw_languages.split('/') if _text(x)] if isinstance(raw_languages,str) else []
 aligned=count==len(languages)  # position-based mapping needs exact alignment
 tracks=[]
 for i in range(count):
  language=languages[i] if aligned else None
  single=count==1  # the summary describes the only stream; several -> unknown
  track_codec=audio_codec if single else None
  track_channels=channels if single else None
  default=True if single else None
  tracks.append({'index':i,'language':language,'languageLabel':_LANGUAGES.get(language),
                 'title':None,'studio':None,'codec':track_codec,'channels':track_channels,
                 'isDefault':default,'label':_track_label(language,track_codec,track_channels,None),
                 'source':'sonarr'})
 return {'status':'verified','quality':{'label':label,'width':width,'height':height,
          'codec':codec,'hdr':hdr,'source':None},
         'audioTracks':tracks,'verifiedAt':time.time(),'source':'sonarr'}

def _episodes(cfg):
 """Bounded batched episode fetch: MediaSources/MediaStreams in one query."""
 items=[]
 for page in range(_MAX_PAGES):
  batch=jellyfin_links.jf_get(cfg,'Items',{'UserId':cfg['userId'],'IncludeItemTypes':'Episode',
   'Recursive':'true','Fields':'ProviderIds,MediaSources','Limit':_PAGE,'StartIndex':page*_PAGE})
  batch=batch.get('Items') or []
  items.extend(batch[:_PAGE])
  if len(batch)<_PAGE:break
 return items

def _load_previous(m):
 try:
  with open(Path(m.WORKER_STATE)/_CACHE_NAME) as f:prev=json.load(f)
  entries=prev.get('entries') if isinstance(prev,dict) else None
  return dict(list(entries.items())[:_MAX_ENTRIES]) if isinstance(entries,dict) else {}
 except (OSError,ValueError):return {}

def _store(m,entries):
 """Best-effort atomic snapshot cache; failures never break the mapping."""
 try:
  p=Path(m.WORKER_STATE)/_CACHE_NAME
  tmp=p.parent/('.'+p.name+'.'+secrets.token_hex(8))
  fd=os.open(tmp,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o640)
  os.fchmod(fd,0o640)
  with os.fdopen(fd,'w') as f:json.dump({'savedAt':time.time(),'entries':entries},f,ensure_ascii=False)
  os.replace(tmp,p)
 except OSError:pass

def _attach_sonarr(m,entries,studios):
 """Sonarr mediaInfo fallback for exact registered hasFile ids only.

 Runs for every watched series (two bounded requests each, like
 episodes.refresh). Creates keys Jellyfin has not scanned yet and fills only
 slots Jellyfin left unclaimed; verified or conflicting Jellyfin data stays.

 Returns (checked, registered): `checked` maps every exactly-numbered
 standard episode key this series reported (with or without a file) so the
 caller can notice REMOVED files; `registered` carries the surviving file
 records (id + internal basename/size identity) for identity validation.
 Series whose requests fail are absent from both: no evidence, no claims.
 """
 checked={};registered={}
 for item in m.watching():
  sr=item['series']
  tvdb=str(sr.get('tvdbId'))
  try:
   eps=m.api('episode?seriesId='+str(sr['id'])) or []
   files={f.get('id'):f for f in (m.api('episodefile?seriesId='+str(sr['id'])) or []) if isinstance(f,dict)}
  except Exception:continue  # Sonarr offline: stale/unknown is decided in the final pass
  byse={}
  for rec in eps:
   if not isinstance(rec,dict):continue
   season=rec.get('seasonNumber');number=rec.get('episodeNumber')
   if not isinstance(season,int) or not isinstance(number,int) or season<=0 or number<=0:continue
   byse.setdefault((season,number),[]).append(rec)
  for (season,number),recs in byse.items():
   key=f'{tvdb}:{season}:{number}'
   checked[key]=True
   if len(recs)!=1:continue  # ambiguous numbering: claim nothing
   rec=recs[0];fid=rec.get('episodeFileId')
   if not rec.get('hasFile') or not isinstance(fid,int) or fid not in files:continue
   epfile=files[fid]
   path=epfile.get('path') or epfile.get('relativePath') or epfile.get('Path')
   registered[key]={'id':fid,'size':epfile.get('size'),
                    'base':_text(str(path).replace('\\','/').rsplit('/',1)[-1])
                            if isinstance(path,str) and path.strip() else None,
                    'file':epfile}
   media=_sonarr_media(epfile)
   if media is None:continue
   media['identity']={'id':fid,'base':registered[key]['base'],'size':registered[key]['size']}
   entry=entries.get(key)
   if entry is None:
    if len(entries)>=_MAX_ENTRIES:continue
    entries[key]={'url':None,'media':media};continue
   if entry['media'] is None:entry['media']=media  # fills an unclaimed slot; never overrides Jellyfin
 return checked,registered

def _validate_jf_identity(entries,checked,registered,previous):
 """Conservative cross-check of fresh Jellyfin claims against Sonarr.

 A Jellyfin snapshot is only labeled verified while Sonarr still registers
 the SAME file for the exact S/E: removal (no file) or replacement (different
 registered id / basename+size) demote the claim — Jellyfin may still serve
 the old file until it rescans. Demotion prefers the REGISTERED file facts
 (fresh Sonarr mediaInfo of the actual file); without usable ones it falls
 back to the stale previous snapshot, else unknown. Series Sonarr could not
 answer for provide no evidence and are never degraded by this check.
 """
 for key,entry in entries.items():
  media=entry.get('media')
  if not media or media.get('status')!='verified' or media.get('source')!='jellyfin':continue
  if key not in checked:continue  # no Sonarr evidence this round
  reg=registered.get(key)
  if reg is None:  # Sonarr no longer registers a file for this episode
   entry['media']=_demote(key,media,previous,None);continue
  prior=(previous.get(key) or {}).get('identity') or {}
  jf=media.get('identity') or {}
  identity={'id':reg.get('id'),'base':reg.get('base'),'size':reg.get('size')}
  if jf.get('base') is not None and reg.get('base') is not None:
   same=jf['base']==reg['base'] and jf.get('size')==reg.get('size')
  elif isinstance(prior.get('id'),int):
   same=reg.get('id')==prior['id']
  else:
   entry['media']['identity']=identity;continue  # first comparable round: baseline
  if same:
   entry['media']['identity']=identity
  else:
   entry['media']=_demote(key,media,previous,reg)
   if entry['media'].get('status')=='verified':entry['media']['identity']=identity

def _demote(key,jf_media,previous,reg):
 """Registered-file facts when usable, else the stale snapshot, else unknown."""
 fallback=_sonarr_media(reg.get('file')) if reg else None
 if fallback is not None:return fallback
 old=previous.get(key)
 if isinstance(old,dict) and old.get('status')=='verified':
  origin=old.get('source') if old.get('source') in ('jellyfin','sonarr') else 'jellyfin'
  return _stale_snapshot(old,origin)
 return _unknown('jellyfin')

def _stale_snapshot(old,origin):
 return {'status':'stale','quality':old.get('quality') if isinstance(old.get('quality'),dict) else None,
         'audioTracks':[t for t in old.get('audioTracks') or [] if isinstance(t,dict)],
         'verifiedAt':old.get('verifiedAt'),'source':origin}

def get_library(m):
 studios=_studios();sonarr=[x['series'] for x in m.watching()];entries={}
 try:
  cfg=rt.connection('jellyfin')
  shows=jellyfin_links.jf_get(cfg,'Items',{'UserId':cfg['userId'],'IncludeItemTypes':'Series',
   'Recursive':'true','Fields':'ProviderIds','Limit':5000})['Items']
  series_map=jellyfin_links._series_map(shows,sonarr)
  for item in _episodes(cfg):
   sid=series_map.get(item.get('SeriesId'));season=item.get('ParentIndexNumber')
   number=item.get('IndexNumber');iid=item.get('Id')
   if sid is None or not isinstance(season,int) or not isinstance(number,int) or not _ITEM_ID.fullmatch(iid or ''):continue
   if len(entries)>=_MAX_ENTRIES:continue
   url=jellyfin_links._detail_url(cfg,iid);start,end=jellyfin_links._episode_range(item)
   media=_item_media(item,studios)
   for n in range(start,end+1):
    key=f'{sid}:{season}:{n}';entry=entries.get(key)
    if entry is None:entries[key]={'url':url,'media':media};continue
    if entry['media'] is None and media is not None:entry['media']=media;continue
    if entry['media'] is not None and media is not None and (\
       _signature(entry['media']['quality'],entry['media']['audioTracks'])!=_signature(media['quality'],media['audioTracks']) or \
       entry['media'].get('identity')!=media.get('identity')):
     entry['media']=_unknown('jellyfin',conflict=True)  # two items claim one episode
 except Exception:pass  # Jellyfin unavailable: Sonarr fallback and stale snapshots still answer
 checked,registered=_attach_sonarr(m,entries,studios)
 previous=_load_previous(m)
 _validate_jf_identity(entries,checked,registered,previous)
 out={};verified={k:v for k,v in previous.items() if isinstance(v,dict) and v.get('status')=='verified' and k.split(':',1)[0] in {str(sr.get('tvdbId')) for sr in sonarr}}
 for key,entry in entries.items():
  media=entry['media']
  if media is None or media.get('conflict'):
   old=previous.get(key)
   if isinstance(old,dict) and old.get('status')=='verified':
    origin=old.get('source') if old.get('source') in ('jellyfin','sonarr') else 'jellyfin'
    media=_stale_snapshot(old,origin)
   else:
    media=_unknown('jellyfin' if entry['url'] else 'sonarr')
  public={'jellyfinUrl':entry['url'],
          'localMedia':{'status':media['status'],'quality':media['quality'],
                        'audioTracks':media['audioTracks'][:_MAX_TRACKS],
                        'verifiedAt':media['verifiedAt'],'source':media['source']}}
  if public['localMedia']['status']=='verified' and len(verified)<_MAX_ENTRIES:
   stored={k:public['localMedia'][k] for k in ('status','quality','audioTracks','verifiedAt','source')}
   identity=media.get('identity')
   if isinstance(identity,dict):stored['identity']=identity
   verified[key]=stored
  out[key]=public
 # Keys verified in a previous snapshot but missing this round (file removed,
 # Sonarr offline, item rescan) stay in the mapping as stale so consumers can
 # distinguish "no file" from "could not re-verify".
 watched={str(sr.get('tvdbId')) for sr in sonarr}
 for key,old in previous.items():
  if len(out)>=_MAX_ENTRIES:break
  if key in out or key.count(':')!=2 or key.split(':',1)[0] not in watched:continue
  if not isinstance(old,dict) or old.get('status')!='verified':continue
  origin=old.get('source') if old.get('source') in ('jellyfin','sonarr') else 'jellyfin'
  out[key]={'jellyfinUrl':None,'localMedia':_stale_snapshot(old,origin)}
 _store(m,verified)
 return out
