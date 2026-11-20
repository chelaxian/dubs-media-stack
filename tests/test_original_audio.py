import copy, types, unittest
from unittest.mock import patch
import _dubs_env
import control, episodes, upgrades

class OriginalAudioTests(unittest.TestCase):
 def test_profile_removes_only_managed_language_and_voice_requirements(self):
  veto=control.rt.settings()['veto_format']
  base={'id':7,'name':'base','minFormatScore':100,'items':[{'allowed':True,'quality':{'id':18}}], 'formatItems':[{'format':1,'name':veto,'score':-10000000},{'format':2,'name':'Озвучка 01 — LostFilm','score':50},{'format':3,'name':'Выбор сериала — LostFilm','score':1000000},{'format':4,'name':'Other custom rule','score':-20},{'format':5,'name':'Русская дорожка / RU','score':100}]}
  calls=[]
  def api(path,method='GET',data=None):
   calls.append((path,method,copy.deepcopy(data)))
   if path=='qualityprofile/7':return copy.deepcopy(base)
   if path=='qualityprofile' and method=='GET':return []
   if path=='qualityprofile' and method=='POST':return dict(data,id=88)
   raise AssertionError(path)
  with patch.object(control,'api',side_effect=api):self.assertEqual(control.original_profile(7),88)
  result=calls[-1][2];self.assertEqual(result['minFormatScore'],0);self.assertEqual([i['score'] for i in result['formatItems']],[0,0,0,-20,0]);self.assertEqual(result['items'],base['items'])
 def test_original_exempts_language_veto_but_keeps_quality_seed_and_other_rejections(self):
  veto=episodes.rt.settings()['veto_format'];m=types.SimpleNamespace(STUDIOS=[])
  profile={'name':'Сериал / база 7 / Оригинал','items':[{'allowed':True,'quality':{'id':18}}],'minFormatScore':0,'formatItems':[{'format':1,'score':0}]}
  row={'title':'Example S01E01','seeders':5,'quality':{'quality':{'id':18}},'customFormats':[{'id':1,'name':veto}]}
  self.assertTrue(episodes.normalize(m,row,profile,'sonarr')[0]['allowed'])
  self.assertFalse(episodes.normalize(m,{**row,'seeders':0},profile,'sonarr')[0]['allowed'])
  self.assertFalse(episodes.normalize(m,{**row,'quality':{'quality':{'id':19}}},profile,'sonarr')[0]['allowed'])
  self.assertFalse(episodes.normalize(m,{**row,'rejections':['Other rejection']},profile,'sonarr')[0]['allowed'])
  self.assertFalse(episodes.normalize(m,row,{**profile,'name':'other'},'sonarr')[0]['allowed'])
 def test_original_upgrade_has_no_voice_requirement(self):
  m=types.SimpleNamespace(load=lambda *a:{'42':{'studio':'Оригинал','mode':'strict'}})
  self.assertEqual(upgrades.voice_rank(m,42,'English audio'),0)
if __name__=='__main__':unittest.main()
