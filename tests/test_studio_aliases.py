import json
import re
from pathlib import Path

STUDIOS = json.loads((Path(__file__).resolve().parents[1] / 'studios.json').read_text())

def matches(name, title):
    return bool(re.search(next(x['pattern'] for x in STUDIOS if x['name'] == name), title))

def test_kurazh_known_aliases_and_context():
    name = 'Кураж-Бамбей'
    for value in ('Кураж', 'куражбамбей', 'Кураж-Бомбей', 'Kuraj-bambey',
                  'Kurash-bombey', 'kurajbambey', 'kuraj', '***Кураж***-***Бамбей***',
                  '[К-Б]', '[K-B]', 'озвучка Денис Колесников', 'dub Mr. Dee',
                  'перевод DeeVision Productions'):
        assert matches(name, value), value
    for value in ('K-B algorithm', 'К-Блок', 'куражный', 'Колесников Денис',
                  'Сериал Денис Колесников', 'Deep Space', 'Bombey'):
        assert not matches(name, value), value

def test_other_aliases_and_fallback_exclusions():
    for name, alias in [('LostFilm', 'LostFilm.TV'), ('Кубик в Кубе', 'Kubik in Kube'),
                        ('Viruse Project', 'Вирус Проект'), ('HDRezka Studio', 'ХД Резка'),
                        ('LE-Production', 'Ле Продакшен'), ('Сыендук / Syenduk / sndk', 'Siyenduk')]:
        assert matches(name, alias), alias
        assert not matches('Другая русская озвучка', alias), alias
    for value in ('русский RHS', '[K-B]', 'озвучка Денис Колесников'):
        assert not matches('Другая русская озвучка', value), value
