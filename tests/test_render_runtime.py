"""Offline render + runtime-state tests (fresh install with zero shows)."""
import _dubs_env, os, stat, tempfile, unittest, json
from pathlib import Path
from unittest.mock import patch
from _dubs_env import load_script

m = load_script('ctrl', _dubs_env.SRC / 'control.py')
import render as ui


class RenderRuntime(unittest.TestCase):
 def setUp(self):
  d = tempfile.mkdtemp(prefix='dubs-render-')
  state = Path(d); feed = state / 'feed'; feed.mkdir()
  (feed / 'status.json').write_text(json.dumps({'updated': 1730000000}))
  p_state = patch.object(m, 'STATE', state)
  p_feed = patch.object(m.rt, 'feed_state_root', return_value=feed)
  for p in (p_state, p_feed):
   p.start(); self.addCleanup(p.stop)

 def test_atomic_state_is_0640(self):
  with tempfile.TemporaryDirectory() as d:
   with patch.object(m, 'STATE', Path(d)):
    old = os.umask(0o077)
    try: m.save('preferences.json', {'fixture': True})
    finally: os.umask(old)
    assert stat.S_IMODE((Path(d) / 'preferences.json').stat().st_mode) == 0o640
    assert m.load('preferences.json', {}) == {'fixture': True}

 def test_fresh_install_page_has_no_shows_and_cleanup_off(self):
  with patch.object(m, 'watching', return_value=[]):
   page = ui.render(m, 'c' * 43)
  self.assertEqual(page.count('class="show"'), 0)
  self.assertIn('Автоочистка отключена', page)
  self.assertIn('id="playback-client"', page)  # playback selector retained
  self.assertIn('подключите аккаунт', page)

 def test_apps_render_central_latest_selection_and_semantic_dom_order(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory)
   names=['Dubs-1.9.apk','Dubs-1.10.apk','Swiftfin-1.5.ipa','Swiftfin-1.5.1.ipa','SHA256SUMS']
   for name in names:(root/name).touch()
   config=dict(m.rt.settings());config['mobile']={'dir':directory,'artifacts':[{'file':n} for n in names]}
   with patch.object(m.rt,'settings',return_value=config),patch.object(m,'watching',return_value=[]):
    page=ui.render(m,'c'*43)
   for name in [names[1],names[3],names[4]]:self.assertIn('href="/apps/'+name+'"',page)
   for name in [names[0],names[2]]:self.assertNotIn('href="/apps/'+name+'"',page)
   order=['events-panel','account-panel','apps-panel','appearance-panel','player-panel','cleanup-panel','download-panel']
   positions=[page.index('id="'+name+'"') for name in order]
   self.assertEqual(positions,sorted(positions))
   self.assertEqual(page.count('action="/myshows-login"'),1)
   self.assertIn('name="csrf" value="'+'c'*43+'"',page)

 def _load(self, name, default=None):
  if name == 'preferences.json':
   return {'21': {'baseProfile': None, 'studio': 'Сыендук', 'mode': 'strict'}}
  return default

 def _rows(self):
  return [{'id': 21, 'title': 'Тестовый сериал',
           'series': {'id': 21, 'title': 'Test Show', 'status': 'continuing', 'year': 2024}}]

 def test_page_renders_fixture_rows(self):
  with patch.object(m, 'watching', return_value=self._rows()), \
       patch.object(m, 'load', side_effect=self._load):
   page = ui.render(m, 'c' * 43)
  self.assertEqual(page.count('class="show"'), 1)
  self.assertIn('show-21', page)
  self.assertIn('Сыендук', page)          # named studio preference displayed
  self.assertIn('ТОЛЬКО ЭТА', page)       # strict mode badge

 def test_badge_metadata_uses_current_order_and_resolved_saved_choices(self):
  import html, re
  studios=[{'name':'LostFilm'},{'name':'Кураж-Бамбей'},{'name':'<unsafe "studio">'}]
  preferences={'7':{'studio':1,'secret':'never expose'},'8':{'studio':'LostFilm'},'9':{'studio':-1}}
  with patch.object(m,'watching',return_value=[]), patch.object(m,'STUDIOS',studios), \
       patch.object(m,'load',side_effect=lambda name,default: preferences if name=='preferences.json' else default):
   page=ui.render(m,'c'*43)
  encoded=re.search(r'id="media-badge-config" hidden data-config="([^"]*)"',page).group(1)
  config=json.loads(html.unescape(encoded))
  self.assertEqual(config,{'studios':[x['name'] for x in studios],'preferred':{'7':'Кураж-Бамбей','8':'LostFilm'}})
  self.assertNotIn('never expose',page)
  self.assertNotIn('<unsafe "studio">',encoded)


if __name__ == '__main__':
 unittest.main()
