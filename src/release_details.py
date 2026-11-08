"""Best-effort tracker description evidence; never blocks a release search."""
import concurrent.futures
import html
from html.parser import HTMLParser
import re
import subprocess
import threading
import time
import urllib.parse
import requests

_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=3, thread_name_prefix='release-details')
_LOCK = threading.Lock()
_CACHE = {}
_PENDING = set()
_MAX_PENDING = 24


def safe_url(url):
    """Only known tracker detail pages: no redirects, credentials or arbitrary hosts."""
    try:
        u = urllib.parse.urlsplit(url or '')
        if u.scheme != 'https' or u.username or u.password or u.port not in (None, 443) or u.fragment:
            return None
        if u.hostname == 'rutor.info' and re.fullmatch(r'/torrent/[0-9]{1,12}/[a-zA-Z0-9._-]*/?', u.path) and not u.query:
            return urllib.parse.urlunsplit(('https', u.hostname, u.path, '', ''))
        if u.hostname == 'r4.rudub.world' and u.path == '/details.php':
            q = urllib.parse.parse_qs(u.query, strict_parsing=True)
            if set(q) == {'id'} and len(q['id']) == 1 and re.fullmatch(r'[0-9]{1,12}', q['id'][0]):
                return 'https://r4.rudub.world/details.php?id=' + q['id'][0]
    except (ValueError, TypeError):
        pass
    return None


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript'): self.hidden += 1
        if not self.hidden and tag in ('br', 'tr', 'p', 'li', 'div'): self.parts.append('\n')
    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript') and self.hidden: self.hidden -= 1
        if not self.hidden and tag in ('tr', 'p', 'li', 'div'): self.parts.append('\n')
    def handle_data(self, data):
        if not self.hidden: self.parts.append(data)


def extract(body, studios):
    # Ignore comments and user discussions; inspect only labelled audio fields.
    body = re.split(r'(?is)<[^>]+(?:id|class)=["\'][^"\']*(?:comments|comment-list)[^"\']*["\']', body, maxsplit=1)[0]
    parser = _Text(); parser.feed(body[:300000]); text = html.unescape(''.join(parser.parts))
    fields = []
    for line in text.splitlines():
        line = re.sub(r'\s+', ' ', line).strip()
        match = re.match(r'(?i)^(перевод|озвучка|звук|аудио(?:дорожки?)?|audio)(?:\s+(ru|rus|russian|русский))?\s*[:：]\s*(.{1,300})$', line)
        if match: fields.append((match.group(1).lower(), match.group(2) or '', match.group(3)))
    russian = False; voices = []
    for label, lang, value in fields:
        if re.search(r'(?i)\b(?:нет|без|отсутствует)\s+(?:русск|перевод|озвуч)', value): continue
        if lang or re.search(r'(?i)(?<![a-zа-яё0-9])(?:rus(?:sian)?|ru|русск(?:ий|ая|ое|ие|их)?|рус)(?![a-zа-яё0-9])', value): russian = True
        if label in ('перевод', 'озвучка', 'звук', 'аудио', 'аудиодорожка', 'аудиодорожки', 'audio'):
            for studio in studios:
                if studio['name'] == 'Другая русская озвучка': continue
                try: found = re.search(studio['pattern'], value)
                except re.error: found = False
                if found and studio['name'] not in voices: voices.append(studio['name']); russian = True
    return {'russian': russian, 'voices': voices}


def _fetch(url, studios):
    # Limit each route and total downloaded bytes; a timeout or HTTP failure is
    # inconclusive, not a search error. Never follow a redirect to another host.
    for proxy in (None, '10.11.0.224:1080', '10.11.0.224:1081'):
        args = ['curl', '--silent', '--show-error', '--fail', '--max-time', '2', '--max-filesize', '300000', '--range', '0-299999', '--proto', '=https']
        if proxy: args += ['--socks5-hostname', proxy]
        try:
            p = subprocess.run(args + ['--', url], capture_output=True, timeout=3)
            if p.returncode or len(p.stdout) > 300000: continue
            raw = p.stdout.decode('cp1251' if 'rudub.world' in url else 'utf-8', errors='replace')
            result = extract(raw, studios)
            if result['russian'] or result['voices']: return result
            return None
        except (OSError, subprocess.SubprocessError, ValueError):
            continue
    return None


def _lookup(url, studios, title, base, headers):
    evidence = _fetch(url, studios)
    if not evidence or not base or not headers: return evidence
    extra = (' RUS' if evidence.get('russian') else '') + ' ' + ' '.join(evidence.get('voices', []))
    try:
        response = requests.get(base + '/parse', params={'title': title + extra}, headers=headers, timeout=(.5, 2))
        response.raise_for_status()
        evidence['formats'] = response.json().get('customFormats', [])
        return evidence
    except (requests.RequestException, ValueError, TypeError):
        return None


def get_or_schedule(url, studios, title='', base=None, headers=None):
    url = safe_url(url)
    if not url: return None
    cache_key = (url, title, base)
    now = time.monotonic()
    with _LOCK:
        cached = _CACHE.get(cache_key)
        if cached and cached[0] > now: return cached[1]
        if cache_key in _PENDING or len(_PENDING) >= _MAX_PENDING: return None
        _PENDING.add(cache_key)
    def finished(future):
        try: result = future.result()
        except Exception: result = None
        with _LOCK:
            _PENDING.discard(cache_key)
            if len(_CACHE) >= 512: _CACHE.pop(next(iter(_CACHE)))
            _CACHE[cache_key] = (time.monotonic() + (1800 if result else 300), result)
    try:
        future = _POOL.submit(_lookup, url, studios, title, base, headers)
        future.add_done_callback(finished)
    except (RuntimeError, OSError):
        with _LOCK: _PENDING.discard(cache_key)
    return None
