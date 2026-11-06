"""Offline tests for the get_library local-file provenance contract.

Fixtures mirror the sanitized production schema (Jellyfin MediaSources with
actual MediaStreams incl. multi-studio Russian audio, an English track and an
EmbeddedImage cover-art decoy; Sonarr mediaInfo with resolution string,
audioLanguages/audioStreamCount and no per-track detail). Episode video
titles are generic: no personal metadata enters the repository.

All HTTP is stubbed in-process; no production API is ever contacted and no
real token, host, path or file content appears in fixtures.
"""
import json,shutil,stat,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit,parse_qs

import _dubs_env  # offline roots; src/ on sys.path
import jellyfin_links
import local_media

TVDB='316164';IMDB='tt2861424';SHOW_ID='a'*32;SERVER_ID='b'*32
TOKEN='secret-jf-token';USER='secret-user-777'
EP1='0'*31+'1';EP2='0'*31+'2';EP3='0'*31+'3'
CFG={'base_url':'http://127.0.0.1:8096','token':TOKEN,'userId':USER,
     'public':'https://jf.example.org','serverId':SERVER_ID}

def video(index=0,codec='h264',width=1920,height=1080,title='Episode',hdr='SDR',default=True):
 return {'Codec':codec,'Language':'eng','ColorSpace':'bt709','ColorTransfer':'bt709',
         'ColorPrimaries':'bt709','Title':title,'VideoRange':hdr,'VideoRangeType':hdr,
         'DisplayTitle':'%s - 1080p - %s - %s'%(title,codec.upper(),hdr),'BitDepth':8,
         'IsDefault':default,'IsForced':False,'Height':height,'Width':width,
         'Profile':'High','Type':'Video','Index':index}

def audio(index,codec='ac3',language='rus',title=None,channels=6,layout='5.1',default=False,profile=None):
 s={'Codec':codec,'Language':language,'VideoRange':'Unknown','VideoRangeType':'Unknown',
    'DisplayTitle':'track %d'%index,'ChannelLayout':layout,'Channels':channels,
    'IsDefault':default,'IsForced':False,'Type':'Audio','Index':index}
 if title is not None:s['Title']=title
 if profile:s['Profile']=profile
 return s

def subtitle(index,language='eng',title=None):
 s={'Codec':'subrip','Language':language,'VideoRange':'Unknown','VideoRangeType':'Unknown',
    'DisplayTitle':'sub %d'%index,'IsDefault':False,'IsForced':False,'Type':'Subtitle','Index':index}
 if title is not None:s['Title']=title
 return s

def image(index,codec='png',width=2000,height=3000):
 return {'Codec':codec,'ColorSpace':'gbr','VideoRange':'Unknown','VideoRangeType':'Unknown',
         'IsDefault':False,'IsForced':False,'Height':height,'Width':width,
         'Type':'EmbeddedImage','Index':index}

def streams_ep1():
 return [video(title='Episode 1'),audio(1,codec='ac3',title='TVShows',default=True),
         audio(2,codec='aac',title='WinMedia',profile='LC'),
         audio(3,codec='aac',title='Red Head Sound',channels=2,layout='stereo',profile='LC'),
         audio(4,codec='eac3',language='eng'),subtitle(5),subtitle(6,title='SDH'),
         subtitle(7,language='heb'),image(8)]

def streams_ep3():
 noisy='AC-3 5.1 @ 384 Kbps Многоголосый закадровый '
 return [video(title='Episode 3'),
         audio(1,codec='ac3',title=noisy+'[LostFilm.tv]',default=True),
         audio(2,codec='ac3',title=noisy+'[BaibaKo.tv]'),
         audio(3,codec='ac3',language='eng',title='AC-3 5.1 @ 384 Kbps Оригинальный [Original]'),
         subtitle(4,language='rus',title='Русские (полные) [Notabenoid]'),
         image(7,codec='mjpeg',width=170,height=170)]

def jf_item(iid,season,number,streams,end=None,location='FileSystem',virtual=None,sources='same'):
 item={'Id':iid,'SeriesId':SHOW_ID,'ParentIndexNumber':season,'IndexNumber':number,
       'LocationType':location,'IsVirtualItem':virtual,'MediaStreams':streams}
 if end is not None:item['IndexNumberEnd']=end
 if sources=='same':item['MediaSources']=[{'Id':iid,'MediaStreams':streams}]
 elif sources is not None:item['MediaSources']=sources
 return item

def jf_source(iid,streams,path=None,size=None):
 """MediaSource with optional internal file identity (Path/Size, never output)."""
 out={'Id':iid,'MediaStreams':streams}
 if path is not None:out['Path']=path
 if size is not None:out['Size']=size
 return out

def sonarr_file(fid,size=806743497,media=None,quality_name='HDTV-1080p',source='television',path=None):
 mi={'audioBitrate':0,'audioChannels':2,'audioCodec':'AAC','audioLanguages':'rus/eng',
     'audioStreamCount':2,'videoBitDepth':8,'videoBitrate':0,'videoCodec':'h264',
     'videoFps':23.976,'videoDynamicRange':'','videoDynamicRangeType':'',
     'resolution':'1920x1080','runTime':'22:10','scanType':'Progressive','subtitles':'rus/eng/eng'}
 if media is not None:mi=media
 out={'id':fid,'seriesId':1,'size':size,'mediaInfo':mi,'releaseGroup':'RARBG',
      'quality':{'quality':{'id':9,'name':quality_name,'source':source,'resolution':1080},
                 'revision':{'version':1,'real':0,'isRepack':False}}}
 if path is not None:out['Path']=path
 return out

def sonarr_episode(eid,season,number,has_file,file_id=None):
 return {'id':eid,'seasonNumber':season,'episodeNumber':number,'hasFile':has_file,
         'episodeFileId':file_id,'monitored':True}

class JfStub:
 """requests shim: serves Items pages from a list, records every call."""
 def __init__(self,episodes=None):
  self.episodes=episodes or [];self.calls=[]
 def get(self,url,params=None,headers=None,timeout=None):
  self.calls.append({'url':url,'params':dict(params or {}),'headers':dict(headers or {}),'timeout':timeout})
  if params.get('IncludeItemTypes')=='Series':
   items=[{'Id':SHOW_ID,'ProviderIds':{'Tvdb':TVDB,'Imdb':IMDB}}]
  else:
   start=int(params.get('StartIndex') or 0);limit=int(params.get('Limit') or 0)
   items=self.episodes[start:start+limit]
  return SimpleNamespace(status_code=200,json=lambda:{'Items':items},raise_for_status=lambda:None)
 def episode_calls(self):
  return [c for c in self.calls if c['params'].get('IncludeItemTypes')=='Episode']

class SonarrApi:
 def __init__(self,episodes=None,files=None,fail=False):
  self.episodes=episodes or [];self.files=files or [];self.calls=[];self.fail=fail
 def __call__(self,path):
  self.calls.append(path)
  if self.fail:raise RuntimeError('Sonarr offline')
  if path.startswith('episode?seriesId='):return self.episodes
  if path.startswith('episodefile?seriesId='):return self.files
  raise AssertionError('unexpected Sonarr path '+path)

def watching():
 return [{'myshowsId':7,'title':'Show','series':{'id':1,'tvdbId':int(TVDB),'imdbId':IMDB}}]

def model(api,root):
 return SimpleNamespace(watching=watching,api=api,WORKER_STATE=Path(root))

def library(root,stub,api):
 m=model(api,root)
 with patch.object(jellyfin_links,'requests',stub),patch.object(jellyfin_links.rt,'connection',return_value=dict(CFG)):
  return jellyfin_links.get_library(m)

class TempCase(unittest.TestCase):
 """Every test gets an isolated worker-state root, removed afterwards."""
 def setUp(self):
  self.root=Path(tempfile.mkdtemp(prefix='dubs-localmedia-'))
  self.addCleanup(shutil.rmtree,self.root,True)

class GetLinksRegression(TempCase):
 """The old URL-only contract for players must stay byte-identical."""
 def test_exact_mapping_range_and_serverid(self):
  episodes=[jf_item(EP1,1,1,streams_ep1()),jf_item(EP2,1,2,streams_ep1(),end=4)]
  stub=JfStub(episodes)
  with patch.object(jellyfin_links,'requests',stub),patch.object(jellyfin_links.rt,'connection',return_value=dict(CFG)):
   links=jellyfin_links.get_links(model(SonarrApi(),self.root))
  self.assertEqual(set(links),{'%s:1:%d'%(TVDB,n) for n in (1,2,3,4)})
  base='https://jf.example.org/web/#/details?'
  self.assertEqual(links['%s:1:1'%TVDB],base+'id='+EP1+'&serverId='+SERVER_ID)
  self.assertEqual(links['%s:1:4'%TVDB],base+'id='+EP2+'&serverId='+SERVER_ID)
 def test_no_serverid_when_invalid_no_credentials_anywhere(self):
  cfg=dict(CFG);cfg.pop('serverId')
  stub=JfStub([jf_item(EP1,1,1,streams_ep1())])
  with patch.object(jellyfin_links,'requests',stub),patch.object(jellyfin_links.rt,'connection',return_value=cfg):
   links=jellyfin_links.get_links(model(SonarrApi(),self.root))
  self.assertTrue(all(v=='https://jf.example.org/web/#/details?id='+EP1 for v in links.values()))
  self.assertTrue(all(TOKEN not in v and USER not in v for v in links.values()))
  self.assertTrue(all('token' not in c['url'].lower() and 'apikey' not in c['url'].lower() for c in stub.calls))
 def test_401_raises_reconnection(self):
  denied=lambda *a,**k:SimpleNamespace(status_code=401,json=lambda:{},raise_for_status=lambda:None)
  with patch.object(jellyfin_links,'requests') as module,patch.object(jellyfin_links.rt,'connection',return_value=dict(CFG)):
   module.get.side_effect=denied
   with self.assertRaises(RuntimeError):jellyfin_links.get_links(model(SonarrApi(),self.root))

class VerifiedSnapshot(TempCase):
 """Realistic Jellyfin item -> verified localMedia with per-track studios."""
 def setUp(self):
  super().setUp()
  self.stub=JfStub([jf_item(EP1,1,1,streams_ep1()),jf_item(EP2,1,3,streams_ep3())])
  self.out=library(self.root,self.stub,SonarrApi())
  self.media=self.out[TVDB+':1:1']['localMedia']
 def test_key_and_url_credential_free(self):
  self.assertIn(TVDB+':1:1',self.out)
  url=self.out[TVDB+':1:1']['jellyfinUrl']
  self.assertEqual(url,'https://jf.example.org/web/#/details?id='+EP1+'&serverId='+SERVER_ID)
  self.assertNotIn(TOKEN,url);self.assertNotIn(USER,url)
 def test_video_quality_ignores_embedded_image(self):
  self.assertEqual(self.media['status'],'verified');self.assertEqual(self.media['source'],'jellyfin')
  self.assertEqual(self.media['quality'],{'label':'1080p H264','width':1920,'height':1080,
                                          'codec':'h264','hdr':'SDR','source':None})
 def test_all_four_audio_tracks_kept_separately(self):
  tracks=self.media['audioTracks']
  self.assertEqual([t['index'] for t in tracks],[1,2,3,4])
  self.assertEqual([t['title'] for t in tracks],['TVShows','WinMedia','Red Head Sound',None])
  self.assertEqual([t['studio'] for t in tracks],[None,None,'Red Head Sound',None])
  self.assertEqual([t['codec'] for t in tracks],['ac3','aac','aac','eac3'])
  self.assertEqual([t['channels'] for t in tracks],[6,6,2,6])
  self.assertEqual([t['language'] for t in tracks],['rus','rus','rus','eng'])
  self.assertEqual([t['languageLabel'] for t in tracks],['Русский','Русский','Русский','Английский'])
  self.assertEqual([t['isDefault'] for t in tracks],[True,False,False,False])
  self.assertTrue(all(t['source']=='jellyfin' for t in tracks))
  self.assertEqual(tracks[0]['label'],'Русский · AC3 · 5.1')
 def test_noisy_title_tags_studio_and_keeps_raw_title(self):
  tracks=self.out[TVDB+':1:3']['localMedia']['audioTracks']
  self.assertEqual(tracks[0]['studio'],'LostFilm')
  self.assertTrue(tracks[0]['title'].endswith('[LostFilm.tv]'))
  self.assertIsNone(tracks[1]['studio'])
  self.assertTrue(tracks[1]['title'].endswith('[BaibaKo.tv]'))
  self.assertIsNone(tracks[2]['studio'])  # English original is not a studio
 def test_verified_at_is_fresh_epoch(self):
  self.assertAlmostEqual(self.media['verifiedAt'],time.time(),delta=120)
  self.assertAlmostEqual(self.out[TVDB+':1:3']['localMedia']['verifiedAt'],time.time(),delta=120)
 def test_contract_shape_exact(self):
  self.assertEqual(set(self.out[TVDB+':1:1']),{'jellyfinUrl','localMedia'})
  self.assertEqual(set(self.media),{'status','quality','audioTracks','verifiedAt','source'})
  for t in self.media['audioTracks']:
   self.assertEqual(set(t),{'index','language','languageLabel','title','studio',
                            'codec','channels','isDefault','label','source'})

class MultiEpisodeAndConservatism(TempCase):
 def test_range_shares_same_file_snapshot(self):
  out=library(self.root,JfStub([jf_item(EP1,1,2,streams_ep1(),end=3)]),SonarrApi())
  a=out[TVDB+':1:2']['localMedia'];b=out[TVDB+':1:3']['localMedia']
  self.assertEqual(a['status'],'verified');self.assertEqual(b['status'],'verified')
  self.assertEqual(a['quality'],b['quality']);self.assertEqual(a['audioTracks'],b['audioTracks'])
 def test_conflicting_versions_claim_nothing(self):
  hi=[video()]+streams_ep1()[1:]
  lo=[video(width=1280,height=720)]+streams_ep1()[1:]
  item=jf_item(EP1,1,1,hi,sources=[{'Id':EP1,'MediaStreams':hi},{'Id':EP2,'MediaStreams':lo}])
  out=library(self.root,JfStub([item]),SonarrApi())
  media=out[TVDB+':1:1']['localMedia']
  self.assertEqual(media['status'],'unknown');self.assertIsNone(media['quality'])
  self.assertEqual(media['audioTracks'],[]);self.assertIsNone(media['verifiedAt'])
 def test_identical_versions_stay_verified(self):
  streams=[video(title='Episode 1')]+streams_ep1()[1:]
  item=jf_item(EP1,1,1,streams,sources=[{'Id':EP1,'MediaStreams':streams},
                                        {'Id':EP2,'MediaStreams':list(streams)}])
  out=library(self.root,JfStub([item]),SonarrApi())
  self.assertEqual(out[TVDB+':1:1']['localMedia']['status'],'verified')
 def test_two_items_claiming_one_episode_degrade_to_unknown(self):
  s1=streams_ep1();s2=streams_ep1();s2[0]=video(width=1280,height=720,title='Episode 1 low')
  out=library(self.root,JfStub([jf_item(EP1,1,1,s1),jf_item(EP2,1,1,s2)]),SonarrApi())
  self.assertEqual(out[TVDB+':1:1']['localMedia']['status'],'unknown')
 def test_virtual_and_streamless_items_never_verified(self):
  virtual=jf_item(EP1,1,1,streams_ep1(),location='Virtual')
  novirtual=jf_item(EP2,1,2,streams_ep1(),virtual=True)
  empty=jf_item(EP3,1,3,streams_ep1(),sources=[])
  out=library(self.root,JfStub([virtual,novirtual,empty]),SonarrApi())
  self.assertEqual(out[TVDB+':1:1']['localMedia']['status'],'unknown')
  self.assertEqual(out[TVDB+':1:2']['localMedia']['status'],'unknown')
  self.assertEqual(out[TVDB+':1:3']['localMedia']['status'],'unknown')
 def test_hdr_variants_reported(self):
  out=library(self.root,JfStub([jf_item(EP1,1,1,[video(hdr='HDR10')])]),SonarrApi())
  self.assertEqual(out[TVDB+':1:1']['localMedia']['quality']['hdr'],'HDR10')
  out=library(self.root,JfStub([jf_item(EP2,1,2,[video(hdr='Unknown')])]),SonarrApi())
  self.assertIsNone(out[TVDB+':1:2']['localMedia']['quality']['hdr'])
 def test_ambiguous_series_identity_maps_nothing_from_jellyfin(self):
  def two_shows():
   return [{'myshowsId':7,'title':'A','series':{'id':1,'tvdbId':int(TVDB),'imdbId':IMDB}},
           {'myshowsId':8,'title':'B','series':{'id':2,'tvdbId':999999,'imdbId':IMDB}}]
  m=SimpleNamespace(watching=two_shows,api=SonarrApi(),WORKER_STATE=self.root)
  stub=JfStub([jf_item(EP1,1,1,streams_ep1())])  # series matches both shows
  with patch.object(jellyfin_links,'requests',stub),patch.object(jellyfin_links.rt,'connection',return_value=dict(CFG)):
   out=jellyfin_links.get_library(m)
  self.assertNotIn(TVDB+':1:1',out)  # provider identity ambiguous: no claim from this item

class SonarrFallback(TempCase):
 def test_registered_file_becomes_verified_summary(self):
  api=SonarrApi(episodes=[sonarr_episode(11,1,4,True,1)],files=[sonarr_file(1)])
  out=library(self.root,JfStub([]),api)  # nothing in Jellyfin at all
  key=TVDB+':1:4';self.assertIn(key,out);self.assertIsNone(out[key]['jellyfinUrl'])
  media=out[key]['localMedia']
  self.assertEqual(media['status'],'verified');self.assertEqual(media['source'],'sonarr')
  # label from ACTUAL mediaInfo dimensions; release-derived name/source never surface
  self.assertEqual(media['quality'],{'label':'1080p H264','width':1920,'height':1080,
                                     'codec':'h264','hdr':None,'source':None})
  self.assertEqual([t['language'] for t in media['audioTracks']],['rus','eng'])
  self.assertTrue(all(t['studio'] is None for t in media['audioTracks']))
  self.assertTrue(all(t['source']=='sonarr' for t in media['audioTracks']))
 def test_release_group_cannot_tag_studio(self):
  api=SonarrApi(episodes=[sonarr_episode(11,1,4,True,1)],files=[sonarr_file(1)])
  out=library(self.root,JfStub([]),api)
  tracks=out[TVDB+':1:4']['localMedia']['audioTracks']
  self.assertTrue(all(t['studio'] is None and t['title'] is None for t in tracks))
 def test_summary_cannot_claim_per_track_facts_when_two_streams(self):
  api=SonarrApi(episodes=[sonarr_episode(11,1,4,True,1)],files=[sonarr_file(1)])
  out=library(self.root,JfStub([]),api)
  for t in out[TVDB+':1:4']['localMedia']['audioTracks']:
   self.assertIsNone(t['isDefault']);self.assertIsNone(t['codec']);self.assertIsNone(t['channels'])
  self.assertEqual([t['label'] for t in out[TVDB+':1:4']['localMedia']['audioTracks']],
                   ['Русский','Английский'])
 def test_single_stream_summary_describes_the_only_track(self):
  mi=sonarr_file(1)['mediaInfo']
  mi.update({'audioStreamCount':1,'audioChannels':6.0,'audioCodec':'AC3','audioLanguages':'rus'})
  api=SonarrApi(episodes=[sonarr_episode(11,1,4,True,1)],files=[sonarr_file(1,media=mi)])
  out=library(self.root,JfStub([]),api)
  tracks=out[TVDB+':1:4']['localMedia']['audioTracks']
  self.assertEqual(len(tracks),1)
  self.assertEqual(tracks[0]['codec'],'AC3');self.assertEqual(tracks[0]['channels'],6)
  self.assertTrue(tracks[0]['isDefault']);self.assertEqual(tracks[0]['language'],'rus')
 def test_misaligned_language_list_stays_null(self):
  mi=sonarr_file(1)['mediaInfo'];mi['audioLanguages']='rus'  # count=2, languages=1
  api=SonarrApi(episodes=[sonarr_episode(11,1,4,True,1)],files=[sonarr_file(1,media=mi)])
  out=library(self.root,JfStub([]),api)
  self.assertTrue(all(t['language'] is None
                      for t in out[TVDB+':1:4']['localMedia']['audioTracks']))
 def test_jellyfin_verified_wins_over_sonarr(self):
  api=SonarrApi(episodes=[sonarr_episode(11,1,1,True,1)],files=[sonarr_file(1)])
  out=library(self.root,JfStub([jf_item(EP1,1,1,streams_ep1())]),api)
  media=out[TVDB+':1:1']['localMedia']
  self.assertEqual(media['source'],'jellyfin');self.assertEqual(len(media['audioTracks']),4)
 def test_sonarr_fills_unclaimed_slot_and_new_episodes(self):
  # Jellyfin item has no usable streams; Sonarr also registers an episode Jellyfin never scanned.
  api=SonarrApi(episodes=[sonarr_episode(11,1,1,True,1),sonarr_episode(12,1,2,True,2)],
                files=[sonarr_file(1),sonarr_file(2)])
  out=library(self.root,JfStub([jf_item(EP1,1,1,streams_ep1(),sources=[])]),api)
  self.assertEqual(out[TVDB+':1:1']['localMedia']['source'],'sonarr')
  self.assertEqual(out[TVDB+':1:1']['localMedia']['status'],'verified')
  self.assertEqual(out[TVDB+':1:2']['localMedia']['source'],'sonarr')
  self.assertIsNone(out[TVDB+':1:2']['jellyfinUrl'])
 def test_unregistered_or_flagless_files_ignored(self):
  api=SonarrApi(episodes=[sonarr_episode(11,1,4,False,1),sonarr_episode(12,1,5,True,99)],
                files=[sonarr_file(1)])
  out=library(self.root,JfStub([]),api)
  self.assertNotIn(TVDB+':1:4',out);self.assertNotIn(TVDB+':1:5',out)
 def test_zero_length_file_carries_no_claim(self):
  api=SonarrApi(episodes=[sonarr_episode(11,1,4,True,1)],files=[sonarr_file(1,size=0)])
  out=library(self.root,JfStub([]),api)
  self.assertNotIn(TVDB+':1:4',out)
 def test_missing_mediainfo_carries_no_claim(self):
  api=SonarrApi(episodes=[sonarr_episode(11,1,4,True,1)],
                files=[{'id':1,'seriesId':1,'size':123,'quality':{}}])
  out=library(self.root,JfStub([]),api)
  self.assertNotIn(TVDB+':1:4',out)
 def test_specials_and_duplicate_records_skipped(self):
  api=SonarrApi(episodes=[sonarr_episode(11,0,1,True,1),
                          sonarr_episode(12,1,6,True,1),sonarr_episode(13,1,6,True,2)],
                files=[sonarr_file(1),sonarr_file(2)])
  out=library(self.root,JfStub([]),api)
  self.assertNotIn(TVDB+':0:1',out);self.assertNotIn(TVDB+':1:6',out)

class Staleness(TempCase):
 def test_sonarr_failure_degrades_to_stale_with_old_data(self):
  api=SonarrApi(episodes=[sonarr_episode(11,1,4,True,1)],files=[sonarr_file(1)])
  fresh=library(self.root,JfStub([]),api)[TVDB+':1:4']['localMedia']
  self.assertEqual(fresh['status'],'verified')
  out=library(self.root,JfStub([]),SonarrApi(fail=True))
  media=out[TVDB+':1:4']['localMedia']
  self.assertEqual(media['status'],'stale');self.assertEqual(media['source'],'sonarr')
  self.assertEqual(media['quality'],fresh['quality'])
  self.assertEqual(media['audioTracks'],fresh['audioTracks'])
  self.assertEqual(media['verifiedAt'],fresh['verifiedAt'])  # old snapshot, never current-looking
 def test_deregistered_file_degrades_to_stale(self):
  api=SonarrApi(episodes=[sonarr_episode(11,1,4,True,1)],files=[sonarr_file(1)])
  fresh=library(self.root,JfStub([]),api)[TVDB+':1:4']['localMedia']
  api=SonarrApi(episodes=[sonarr_episode(11,1,4,False,1)],files=[])
  media=library(self.root,JfStub([]),api)[TVDB+':1:4']['localMedia']
  self.assertEqual(media['status'],'stale');self.assertEqual(media['verifiedAt'],fresh['verifiedAt'])
 def test_replaced_file_is_fresh_verified_not_stale(self):
  api=SonarrApi(episodes=[sonarr_episode(11,1,4,True,1)],files=[sonarr_file(1)])
  old=library(self.root,JfStub([]),api)[TVDB+':1:4']['localMedia']['verifiedAt']
  time.sleep(0.02)
  new=sonarr_file(7);new['mediaInfo']['resolution']='3840x2160';new['quality']['quality']['name']='UHD-2160p'
  api=SonarrApi(episodes=[sonarr_episode(11,1,4,True,7)],files=[new])
  media=library(self.root,JfStub([]),api)[TVDB+':1:4']['localMedia']
  self.assertEqual(media['status'],'verified');self.assertGreater(media['verifiedAt'],old)
  self.assertEqual(media['quality']['width'],3840)
 def test_no_previous_snapshot_means_unknown_not_stale(self):
  api=SonarrApi(fail=True)
  out=library(self.root,JfStub([jf_item(EP1,1,1,streams_ep1(),sources=[])]),api)
  self.assertEqual(out[TVDB+':1:1']['localMedia']['status'],'unknown')
 def test_stale_keys_survive_when_item_disappears_from_library(self):
  api=SonarrApi(episodes=[sonarr_episode(11,1,4,True,1)],files=[sonarr_file(1)])
  fresh=library(self.root,JfStub([]),api)[TVDB+':1:4']['localMedia']
  out=library(self.root,JfStub([]),SonarrApi(fail=True))
  media=out[TVDB+':1:4']['localMedia']
  self.assertEqual(media['status'],'stale');self.assertEqual(media['verifiedAt'],fresh['verifiedAt'])
  self.assertIsNone(out[TVDB+':1:4']['jellyfinUrl'])
 def test_stale_not_resurrected_for_unwatched_series(self):
  api=SonarrApi(episodes=[sonarr_episode(11,1,4,True,1)],files=[sonarr_file(1)])
  library(self.root,JfStub([]),api)
  def other_show():
   return [{'myshowsId':9,'title':'Other','series':{'id':5,'tvdbId':555555,'imdbId':'tt0000005'}}]
  m=SimpleNamespace(watching=other_show,api=SonarrApi(fail=True),WORKER_STATE=self.root)
  with patch.object(jellyfin_links,'requests',JfStub([])),patch.object(jellyfin_links.rt,'connection',return_value=dict(CFG)):
   out=jellyfin_links.get_library(m)
  self.assertNotIn(TVDB+':1:4',out)

class SafetyAndBounds(TempCase):
 def test_output_has_no_secrets_paths_or_raw_api_urls(self):
  stub=JfStub([jf_item(EP1,1,1,streams_ep1()),jf_item(EP2,1,3,streams_ep3())])
  api=SonarrApi(episodes=[sonarr_episode(11,1,4,True,1)],files=[sonarr_file(1)])
  out=library(self.root,stub,api)
  blob=json.dumps(out,ensure_ascii=False)
  self.assertNotIn(TOKEN,blob);self.assertNotIn(USER,blob);self.assertNotIn('127.0.0.1',blob)
  self.assertNotIn('/mnt/',blob);self.assertNotIn('/torrents',blob);self.assertNotIn('/tv/',blob)
  self.assertNotIn('apikey',blob.lower());self.assertNotIn('token',blob.lower())
  for v in out.values():
   url=v['jellyfinUrl']
   if url:
    self.assertTrue(url.startswith('https://jf.example.org/web/#/details?'))
    self.assertEqual(set(parse_qs(urlsplit(url).query))-{'id','serverId'},set())
 def test_token_travels_in_header_only_and_fields_are_batched(self):
  stub=JfStub([jf_item(EP1,1,1,streams_ep1())])
  library(self.root,stub,SonarrApi())
  episode=stub.episode_calls()[0]
  self.assertEqual(episode['params']['IncludeItemTypes'],'Episode')
  self.assertEqual(episode['params']['Fields'],'ProviderIds,MediaSources')
  self.assertNotIn('Path',episode['params']['Fields'])
  for call in stub.calls:
   self.assertNotIn(TOKEN,call['url'])
   self.assertIn(TOKEN,call['headers']['Authorization'])
   self.assertEqual(call['timeout'],(3,15))
 def test_pagination_bounded_and_complete(self):
  many=[jf_item('%032x'%i,1,i+1,[video(title='Episode %d'%i)]) for i in range(1200)]
  stub=JfStub(many)
  out=library(self.root,stub,SonarrApi())
  self.assertEqual(len(stub.episode_calls()),2)  # 1000 + 200
  self.assertEqual(len(out),1200)
  self.assertIn(TVDB+':1:1200',out)
 def test_pagination_hard_cap(self):
  class Endless(JfStub):
   def get(self,url,params=None,headers=None,timeout=None):
    self.calls.append({'url':url,'params':dict(params or {}),'headers':dict(headers or {}),'timeout':timeout})
    if params.get('IncludeItemTypes')=='Series':
     return SimpleNamespace(status_code=200,json=lambda:{'Items':[{'Id':SHOW_ID,'ProviderIds':{'Tvdb':TVDB}}]},raise_for_status=lambda:None)
    start=int(params.get('StartIndex') or 0)
    page=[jf_item('%032x'%(start+i),1,1,[video(title='Episode %d'%(start+i))]) for i in range(1000)]
    return SimpleNamespace(status_code=200,json=lambda:{'Items':page},raise_for_status=lambda:None)
  stub=Endless()
  library(self.root,stub,SonarrApi())
  self.assertEqual(len(stub.episode_calls()),10)  # ten pages of 1000, then stop
 def test_sonarr_requests_bounded_per_series(self):
  api=SonarrApi(episodes=[sonarr_episode(11,1,1,True,1)],files=[sonarr_file(1)])
  out=library(self.root,JfStub([jf_item(EP1,1,1,streams_ep1())]),api)
  self.assertEqual(len(api.calls),2)  # one episode + one episodefile request
  self.assertEqual(out[TVDB+':1:1']['localMedia']['source'],'jellyfin')
 def test_cache_file_written_private_and_verified_only(self):
  stub=JfStub([jf_item(EP1,1,1,streams_ep1())])
  library(self.root,stub,SonarrApi())
  p=self.root/'local-media.json'
  self.assertTrue(p.is_file())
  self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o640)
  data=json.loads(p.read_text())
  self.assertEqual(set(data['entries']),{TVDB+':1:1'})
  self.assertNotIn(TOKEN,p.read_text())
  # a degraded round must not overwrite previous verified data with unknowns
  library(self.root,JfStub([]),SonarrApi(fail=True))
  self.assertEqual(json.loads(p.read_text())['entries'],data['entries'])
 def test_corrupt_cache_is_ignored(self):
  (self.root/'local-media.json').write_text('{not json')
  out=library(self.root,JfStub([jf_item(EP1,1,1,streams_ep1())]),SonarrApi(fail=True))
  self.assertEqual(out[TVDB+':1:1']['localMedia']['status'],'verified')
 def test_get_library_survives_jellyfin_500_and_get_links_still_raises(self):
  import requests
  def boom(*a,**k):
   return SimpleNamespace(status_code=500,json=lambda:{},
                          raise_for_status=lambda:(_ for _ in ()).throw(requests.HTTPError('500')))
  class Boom:
   def get(self,url,params=None,headers=None,timeout=None):return boom()
  api=SonarrApi(episodes=[sonarr_episode(11,1,1,True,1)],files=[sonarr_file(1)])
  out=library(self.root,Boom(),api)  # must NOT raise: Sonarr fallback answers
  media=out[TVDB+':1:1']['localMedia']
  self.assertEqual(media['status'],'verified');self.assertEqual(media['source'],'sonarr')
  self.assertIsNone(out[TVDB+':1:1']['jellyfinUrl'])
  self.assertEqual(media['quality']['width'],1920)
  # the old player-links contract is unchanged: get_links still raises
  with patch.object(jellyfin_links,'requests',Boom()),patch.object(jellyfin_links.rt,'connection',return_value=dict(CFG)):
   with self.assertRaises(Exception):jellyfin_links.get_links(model(api,self.root))
 def test_long_and_control_char_titles_sanitized(self):
  noisy='tag\x00\x1b with\nnewlines '+'x'*500
  item=jf_item(EP1,1,1,[video(title='Episode 1'),audio(1,title=noisy)])
  out=library(self.root,JfStub([item]),SonarrApi())
  title=out[TVDB+':1:1']['localMedia']['audioTracks'][0]['title']
  self.assertLessEqual(len(title),200)
  self.assertNotIn('\x00',title);self.assertNotIn('\n',title)

class IdentityAndOffline(TempCase):
 """Review regressions: file-identity cross-check, JF-offline resilience."""

 def test_signature_tolerates_none_fields(self):
  tracks=[{'language':None,'codec':'aac','channels':2,'studio':None,'title':None},
          {'language':'rus','codec':'aac','channels':2,'studio':None,'title':None}]
  local_media._signature({},tracks)  # must not raise on None vs str ordering
  local_media._signature(None,tracks)

 def test_matching_file_identity_keeps_jellyfin_verified(self):
  item=jf_item(EP1,1,1,streams_ep1(),
               sources=[jf_source(EP1,streams_ep1(),path='/media/tv/Show/S01E01.mkv',size=806743497)])
  api=SonarrApi(episodes=[sonarr_episode(11,1,1,True,1)],
                files=[sonarr_file(1,path='/media/tv/Show/S01E01.mkv')])
  out=library(self.root,JfStub([item]),api)
  media=out[TVDB+':1:1']['localMedia']
  self.assertEqual(media['status'],'verified');self.assertEqual(media['source'],'jellyfin')
  self.assertEqual(media['audioTracks'][2]['studio'],'Red Head Sound')

 def test_identity_mismatch_demotes_to_sonarr_facts(self):
  item=jf_item(EP1,1,1,streams_ep1(),
               sources=[jf_source(EP1,streams_ep1(),path='/media/tv/Show/S01E01.mkv',size=806743497)])
  api=SonarrApi(episodes=[sonarr_episode(11,1,1,True,1)],
                files=[sonarr_file(1,path='/media/tv/Show/other-release-S01E01.mkv')])
  out=library(self.root,JfStub([item]),api)
  media=out[TVDB+':1:1']['localMedia']
  self.assertEqual(media['status'],'verified');self.assertEqual(media['source'],'sonarr')
  self.assertEqual(media['quality']['width'],1920)
  self.assertTrue(all(t['studio'] is None for t in media['audioTracks']))

 def test_replaced_sonarr_file_with_stale_jellyfin_item_serves_sonarr_facts(self):
  item=jf_item(EP1,1,1,streams_ep1(),
               sources=[jf_source(EP1,streams_ep1(),path='/media/tv/Show/S01E01.mkv',size=806743497)])
  stub=JfStub([item])
  first=library(self.root,stub,SonarrApi(episodes=[sonarr_episode(11,1,1,True,1)],
                                         files=[sonarr_file(1,path='/media/tv/Show/S01E01.mkv')]))
  self.assertEqual(first[TVDB+':1:1']['localMedia']['source'],'jellyfin')
  fourk={'audioChannels':6,'audioCodec':'EAC3','audioLanguages':'rus','audioStreamCount':1,
         'videoCodec':'HEVC','videoDynamicRange':'','resolution':'3840x2160'}
  # Jellyfin still serves the OLD file; Sonarr now registers the replacement.
  second=library(self.root,stub,SonarrApi(episodes=[sonarr_episode(11,1,1,True,7)],
                                          files=[sonarr_file(7,size=42000000000,media=fourk,
                                                             path='/media/tv/Show/S01E01-2160p.mkv')]))
  media=second[TVDB+':1:1']['localMedia']
  self.assertEqual(media['status'],'verified');self.assertEqual(media['source'],'sonarr')
  self.assertEqual(media['quality']['width'],3840)
  self.assertEqual(media['quality']['codec'],'HEVC')
  self.assertNotEqual(media['audioTracks'],first[TVDB+':1:1']['localMedia']['audioTracks'])

 def test_removed_sonarr_file_makes_jellyfin_claim_stale(self):
  item=jf_item(EP1,1,1,streams_ep1(),
               sources=[jf_source(EP1,streams_ep1(),path='/media/tv/Show/S01E01.mkv',size=806743497)])
  stub=JfStub([item])
  first=library(self.root,stub,SonarrApi(episodes=[sonarr_episode(11,1,1,True,1)],
                                         files=[sonarr_file(1,path='/media/tv/Show/S01E01.mkv')]))
  verified_at=first[TVDB+':1:1']['localMedia']['verifiedAt']
  # Sonarr deregisters the file; Jellyfin (unrescanned) still lists the episode.
  second=library(self.root,stub,SonarrApi(episodes=[sonarr_episode(11,1,1,False,None)],files=[]))
  media=second[TVDB+':1:1']['localMedia']
  self.assertEqual(media['status'],'stale');self.assertEqual(media['verifiedAt'],verified_at)
  self.assertEqual(media['quality'],first[TVDB+':1:1']['localMedia']['quality'])

 def test_jellyfin_offline_sonarr_fallback_still_verified(self):
  class Offline(JfStub):
   def get(self,url,params=None,headers=None,timeout=None):
    self.calls.append({'url':url,'params':dict(params or {}),'headers':dict(headers or {}),
                       'timeout':timeout})
    raise ConnectionError('jellyfin unreachable')
  api=SonarrApi(episodes=[sonarr_episode(11,1,1,True,1)],files=[sonarr_file(1)])
  out=library(self.root,Offline(),api)
  media=out[TVDB+':1:1']['localMedia']
  self.assertEqual(media['status'],'verified');self.assertEqual(media['source'],'sonarr')
  self.assertEqual(media['quality']['width'],1920)
  self.assertIsNone(out[TVDB+':1:1']['jellyfinUrl'])

 def test_jellyfin_offline_and_sonarr_down_resurrects_stale_snapshot(self):
  stub=JfStub([jf_item(EP1,1,1,streams_ep1())])
  first=library(self.root,stub,SonarrApi())
  old=first[TVDB+':1:1']['localMedia']
  class Offline(JfStub):
   def get(self,url,params=None,headers=None,timeout=None):
    self.calls.append({'url':url,'params':dict(params or {}),'headers':dict(headers or {}),
                       'timeout':timeout})
    raise ConnectionError('jellyfin unreachable')
  second=library(self.root,Offline(),SonarrApi(fail=True))
  media=second[TVDB+':1:1']['localMedia']
  self.assertEqual(media['status'],'stale')
  self.assertEqual(media['verifiedAt'],old['verifiedAt'])
  self.assertEqual(media['quality'],old['quality'])
  self.assertEqual(media['audioTracks'],old['audioTracks'])
  self.assertIsNone(second[TVDB+':1:1']['jellyfinUrl'])


class AdditionalContract(TempCase):
 def test_group_readable_under_restrictive_umask(self):
  import os
  old=os.umask(0o077)
  try:library(self.root,JfStub([jf_item(EP1,1,1,streams_ep1())]),SonarrApi())
  finally:os.umask(old)
  self.assertEqual(stat.S_IMODE((self.root/'local-media.json').stat().st_mode),0o640)
 def test_repeated_outages_retain_snapshot(self):
  fresh=library(self.root,JfStub([jf_item(EP1,1,1,streams_ep1())]),SonarrApi())
  for _ in range(3):
   out=library(self.root,JfStub(),SonarrApi(fail=True))
   self.assertEqual(out[TVDB+':1:1']['localMedia']['status'],'stale')
   self.assertEqual(out[TVDB+':1:1']['localMedia']['verifiedAt'],fresh[TVDB+':1:1']['localMedia']['verifiedAt'])
 def test_null_sonarr_language(self):
  f=sonarr_file(1);f['mediaInfo']['audioLanguages']=None
  self.assertEqual(len(local_media._sonarr_media(f)['audioTracks']),2)
 def test_sensitive_track_title_is_not_public(self):
  for title in ('/mnt/private/media.mkv','https://example.org/?token=secret','token=secret'):
   self.assertIsNone(local_media._tracks([audio(1,title=title)],[], 'jellyfin')[0]['title'])
 def test_actual_sonarr_lowercase_path_detects_replacement(self):
  streams=streams_ep1()
  item=jf_item(EP1,1,1,streams,sources=[jf_source(EP1,streams,path='/tv/old.mkv',size=100)])
  f=sonarr_file(2);f['path']='/tv/new.mkv'
  out=library(self.root,JfStub([item]),SonarrApi([sonarr_episode(1,1,1,True,2)],[f]))
  self.assertEqual(out[TVDB+':1:1']['localMedia']['source'],'sonarr')

if __name__=='__main__':unittest.main()
