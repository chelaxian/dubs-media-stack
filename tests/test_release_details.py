import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import release_details as d

STUDIOS = [{'name':'Кураж-Бамбей','pattern':r'(?i)кураж'}, {'name':'Другая русская озвучка','pattern':'.*'}]

def test_explicit_audio_and_studio_fields_only():
    x=d.extract('<b>Перевод:</b> Кураж-Бамбей<br><b>Звук:</b> Русский / AAC<br>', STUDIOS)
    assert x == {'russian':True,'voices':['Кураж-Бамбей']}
    assert d.extract('<b>Аудио ru:</b> AAC 2.0<br>', STUDIOS)['russian']
    for html in ('<b>Субтитры:</b> Русские<br>', '<b>Звук:</b> Английский<br><div id="comments">Кураж Русский</div>', '<b>Звук:</b> Без русской дорожки<br>'):
        assert not d.extract(html, STUDIOS)['russian'], html

def test_tracker_url_is_strict():
    assert d.safe_url('https://rutor.info/torrent/123/a-b')
    assert d.safe_url('https://r4.rudub.world/details.php?id=123')
    for url in ('http://rutor.info/torrent/123/a', 'https://rutor.info.evil.test/torrent/123/a', 'https://user@rutor.info/torrent/123/a', 'https://rutor.info/torrent/123/../private', 'https://r4.rudub.world/details.php?id=1&next=other', 'https://127.0.0.1/details.php?id=1'):
        assert not d.safe_url(url), url

def test_failure_is_optional_and_schedule_is_nonblocking():
    import time
    original = d._fetch
    try:
        def slow(*args):
            time.sleep(.3)
            return None
        d._fetch = slow
        start = time.monotonic()
        assert d.get_or_schedule('https://rutor.info/torrent/999/test', STUDIOS) is None
        assert time.monotonic() - start < .2
    finally:
        d._fetch = original
