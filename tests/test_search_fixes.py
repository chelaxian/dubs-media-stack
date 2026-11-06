import sys,unittest,types
from unittest.mock import Mock
import _dubs_env  # offline roots; src/automations on sys.path
import episodes
class SearchFixes(unittest.TestCase):
 def test_bilingual_exact_known_aliases(self):
  expected={'id':36,'title':"Georgie & Mandy's First Marriage",'_myshowsTitle':'Первый брак Джорджи и Мэнди','alternateTitles':[{'title':'Georgie and Mandys First Marriage'}]}
  for title in ["Первый брак Джорджи и Мэнди / Georgie and Mandy's First Marriage", "Первый брак Джорджи и Мэнди  Georgie and Mandy's First Marriage"]:
   self.assertTrue(episodes.series_match({'parsedEpisodeInfo':{'seriesTitle':title}},title,expected))
  self.assertFalse(episodes.series_match({'parsedEpisodeInfo':{'seriesTitle':'Other / Georgie and Mandys First Marriage'}},'',expected))
 def test_original_localized_and_alternate_queries_are_bounded(self):
  s={'title':'ONE PIECE (2023)','alternateTitles':[{'title':'One Piece'},{'title':'ワンピース'}]}
  self.assertEqual(episodes.title_queries(s,'Ван-Пис'),['ONE PIECE','Ван-Пис','ワンピース'])
 def test_preferred_trackers_receive_both_titles_first(self):
  sources=[{'name':'AniDUB'}, {'name':'Kinozal'}, {'name':'RuTracker.org'}, {'name':'RuTor'}]
  tasks=episodes.indexer_queries(sources,['One Piece','Ван-Пис'])
  self.assertEqual([(s['name'],q) for s,q in tasks[:4]],[('RuTracker.org','One Piece'),('RuTracker.org','Ван-Пис'),('Kinozal','One Piece'),('Kinozal','Ван-Пис')])
 def test_later_season_dual_title_and_remake_protection(self):
  expected={'id':6,'title':'ONE PIECE (2023)','year':2023,'_airYear':'2026','_myshowsTitle':'Ван-Пис','alternateTitles':[{'title':'One Piece'}]}
  candidate='One Piece Большой куш (Ван-Пис) One Piece'
  parsed={'parsedEpisodeInfo':{'seriesTitle':candidate}}
  self.assertTrue(episodes.series_match(parsed,candidate+' S02E01 2026',expected))
  self.assertFalse(episodes.series_match(parsed,candidate+' S02E01 2024',expected))
  self.assertFalse(episodes.series_match({'parsedEpisodeInfo':{'seriesTitle':'One Piece'}},'One Piece S02E01 2026',expected))
  self.assertFalse(episodes.series_match({'series':{'id':7},'parsedEpisodeInfo':{'seriesTitle':candidate}},candidate+' 2026',expected))
 def test_external_identifiers_match_and_conflict(self):
  expected={'id':6,'title':'ONE PIECE (2023)','year':2023,'tvdbId':392276,'imdbId':'tt11737520'};parsed={'parsedEpisodeInfo':{'seriesTitle':'unrecognized'}}
  self.assertTrue(episodes.series_match(parsed,'2026',expected,{'tvdbId':392276,'imdbId':11737520}))
  self.assertFalse(episodes.series_match(parsed,'2026',expected,{'tvdbId':392276,'imdbId':999999}))
  self.assertFalse(episodes.series_match(parsed,'2026',expected,{'tvdbId':123}))
 def test_range_is_preserved(self):
  m=types.SimpleNamespace(STUDIOS=[],api=Mock(return_value={'parsedEpisodeInfo':{'seasonNumber':3,'episodeNumbers':[1],'quality':{'quality':{'id':18}}}}))
  d,r=episodes.normalize(m,{'title':'Example [S3E1-4 of 22]','seeders':14},{'items':[{'allowed':True,'quality':{'id':18}}],'minFormatScore':0},'prowlarr')
  self.assertEqual(r['episodeNumbers'],[1,2,3,4])
 def test_one_seeder_is_valid_fallback(self):
  m=types.SimpleNamespace(STUDIOS=[])
  profile={'items':[{'allowed':True,'quality':{'id':18}}],'minFormatScore':0}
  base={'title':'Example S03','quality':{'quality':{'id':18}}}
  for count,allowed in [(0,False),(1,True),(3,True)]:
   d,_=episodes.normalize(m,{**base,'seeders':count},profile,'sonarr')
   self.assertEqual(d['allowed'],allowed)
 def test_tv_sources_only_and_waiting_detail(self):
  self.assertFalse(episodes.supports_tv({'capabilities':{'categories':[{'id':6000}]}}));self.assertTrue(episodes.supports_tv({'capabilities':{'categories':[{'id':5000}]}}))
  text=episodes.waiting_summary([{'display':{'rejections':['Русский язык не подтверждён']}}],['offline'])
  self.assertIn('подходящих 0',text);self.assertIn('Русский язык',text);self.assertIn('Transmission ничего не передано',text)
 def test_russian_audio_title_markers(self):
  import re
  sys.path.insert(0,str(_dubs_env.INSTALLER))
  import bootstrap
  pattern=re.compile(bootstrap.ru_evidence_regex([{'name':'Named','pattern':'does-not-match'},{'name':'Other','pattern':'x'}]))
  for title in ['One Piece (Ван-Пис) S02 DUB (Videofilm Int.)','Title RUS','Title RU','Title Russia','Title Russian','Title РУС','Title перевод','Title озвучка','Title дубляж','Title dubbing','Title мультираздача']:
   self.assertRegex(title,pattern)
  self.assertNotRegex('English Title S02 DUB',pattern)
if __name__=='__main__':unittest.main()
