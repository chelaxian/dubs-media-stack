import types
import unittest
from unittest.mock import patch
import _dubs_env
import episodes

class DetailIntegration(unittest.TestCase):
    def test_evidence_can_remove_language_veto_but_preserves_quality(self):
        studio = {'name': 'Кураж-Бамбей', 'pattern': '(?i)кураж'}
        m = types.SimpleNamespace(STUDIOS=[studio], BASE='http://localhost/api/v3', HEAD={})
        m.api = lambda path: {'parsedEpisodeInfo': {'quality': {'quality': {'id': 18}}, 'episodeNumbers': [5], 'seasonNumber': 3},
                              'customFormats': [{'id': 15, 'name': episodes.rt.settings()['veto_format']}]}
        row = {'title': 'Show S03E05 1080p', 'infoUrl': 'https://rutor.info/torrent/1/test', 'seeders': 5}
        profile = {'items': [{'allowed': True, 'quality': {'id': 18}}], 'minFormatScore': 100,
                   'formatItems': [{'format': 5, 'score': 100}]}
        evidence = {'russian': True, 'voices': ['Кураж-Бамбей'], 'formats': [{'id': 5, 'name': 'Кураж'}]}
        with patch.object(episodes.studio_order, 'indices', return_value=[0]), patch.object(episodes.release_details, 'get_or_schedule', return_value=evidence):
            display, result = episodes.normalize(m, row, profile, 'prowlarr')
            self.assertTrue(display['allowed'])
            self.assertEqual(display['voices'], ['Кураж-Бамбей'])
            self.assertEqual(result['quality']['quality']['id'], 18)
            profile['items'][0]['allowed'] = False
            display, _ = episodes.normalize(m, row, profile, 'prowlarr')
            self.assertFalse(display['allowed'])
        with patch.object(episodes.studio_order, 'indices', return_value=[0]), patch.object(episodes.release_details, 'get_or_schedule', return_value=None):
            display, _ = episodes.normalize(m, row, profile, 'prowlarr')
            self.assertFalse(display['allowed'])
