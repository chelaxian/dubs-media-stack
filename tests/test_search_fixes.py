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
if __name__=='__main__':unittest.main()
