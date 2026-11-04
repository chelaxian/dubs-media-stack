"""Service discovery and readiness classification.

Services are detected through their real APIs against the *configured*
base_url when one exists (remote hosts, custom ports, base paths), then via
Docker labels, then via standard loopback ports — never by container name.

Classification (mutually exclusive):
  ready           API answers and configured credentials authenticate
  needs-setup     the expected service is running but credentials are
                  missing/rejected, or a first-run wizard is still open
  wrong-service   the endpoint answers with a *different* known product
                  (fails closed: never a reason to install a duplicate)
  port-occupied   something listens but no known product answers
  unavailable     nothing is listening

Every result carries:
  external  True when an operator-declared base_url points off-loopback —
            plan/install must treat it as adopted and NEVER install locally
  managed   True when a container carries our com.dubs.service label
"""
import json
import urllib.parse

from . import system

SERVICE_PORTS = {
    'jellyfin': 8096, 'seerr': 5055, 'sonarr': 8989, 'radarr': 7878,
    'lidarr': 8686, 'transmission': 9091, 'prowlarr': 9696,
    'jackett': 9117, 'flaresolverr': 8191,
}

# Deterministic app API version per product (never inferred from probe hits).
ARR_API = {'sonarr': '/api/v3', 'radarr': '/api/v3', 'lidarr': '/api/v1',
           'prowlarr': '/api/v1'}
ARR_PRODUCTS = ('sonarr', 'radarr', 'lidarr', 'prowlarr')
SERVICE_ORDER = ['jellyfin', 'seerr', 'sonarr', 'radarr', 'lidarr',
                 'transmission', 'prowlarr', 'jackett', 'flaresolverr']


def is_external(base_url):
    """True when base_url does not point at this host's loopback."""
    host = urllib.parse.urlsplit(base_url).hostname or ''
    return host.lower() not in ('127.0.0.1', 'localhost', '::1')


def _base(cfg, name):
    base = (cfg or {}).get('base_url') or ''
    if base:
        return base.rstrip('/')
    return 'http://127.0.0.1:%d' % SERVICE_PORTS[name]


def _configured_port(cfg, name):
    """Port of the declared socket (cfg.base_url wins over the default)."""
    base = (cfg or {}).get('base_url') or ''
    if base:
        import urllib.parse
        port = urllib.parse.urlsplit(base).port
        if port:
            return port
    return SERVICE_PORTS[name]


def _occupied(cfg, name, base):
    """True when the DECLARED host:port accepts TCP (right host, not loopback)."""
    import urllib.parse
    host = urllib.parse.urlsplit(base).hostname or '127.0.0.1'
    return system.port_in_use(_configured_port(cfg, name), host=host)


def _classify_result(result, cfg, name):
    """Attach external flag; external declared services never imply install."""
    declared = bool(cfg.get('base_url')) if cfg else False
    result['external'] = bool(declared and is_external(cfg['base_url']))
    if result['external'] and result['class'] == 'unavailable':
        result['detail'] = ('declared service %s is unreachable — check URL/VPN; '
                            'it will NOT be installed locally' % cfg['base_url'])
    result['base_url'] = (cfg or {}).get('base_url') or ''
    return result


def _arr_api(base, prefix, key, expected, timeout=5):
    """(code, data|None) for GET {base}{prefix}/system/status with auth header."""
    code, body, _ = system.http_probe(base + prefix + '/system/status', timeout=timeout,
                                      headers={'X-Api-Key': key or ''})
    if code == 200 and body:
        try:
            return code, json.loads(body)
        except ValueError:
            return code, None
    return code, None


def _arr_identity(base, expected):
    """Identify the product answering at `base` without credentials.

    Returns 'match', 'other', 'challenge' (an arr API 401), or 'unknown'.
    A bare 401 alone is NOT proof of the right product — callers must still
    authenticate a key and verify appName.
    """
    prefix = ARR_API[expected]
    code, body, _ = system.http_probe(base + prefix + '/system/status', timeout=5)
    if code == 401:
        return 'challenge'
    if code == 200 and body:
        try:
            appname = (json.loads(body).get('appName') or '').lower()
            if appname == expected:
                return 'match'
            if appname:
                return 'other'
        except ValueError:
            pass
    code, body, _ = system.http_probe(base + '/', timeout=5)
    if code and code < 500 and body:
        text = body.decode('utf-8', 'replace').lower()
        title = ''
        tstart = text.find('<title>')
        if tstart >= 0:
            title = text[tstart + 7:text.find('</title>', tstart)].strip()
        for product in ARR_PRODUCTS:
            if title.startswith(product):
                return 'match' if product == expected else 'other'
        if 'onarr' in title or 'adarr' in title or 'idarr' in title or 'rowlarr' in title:
            return 'other'
    return 'unknown'


def _detect_arr(name, cfg):
    base = _base(cfg, name)
    key = (cfg or {}).get('key') or ''
    port = SERVICE_PORTS[name]
    identity = _arr_identity(base, name)
    if identity == 'unknown':
        if _occupied(cfg, name, base):
            return {'class': 'port-occupied',
                    'detail': 'socket %s is occupied but no known arr API answers '
                              '(blocked; not installing anything)' % base}
        return {'class': 'unavailable',
                'detail': 'no arr API answered at %s' % base}
    if identity == 'other':
        return {'class': 'wrong-service',
                'detail': '%s serves a different product (expected %s)' % (base, name)}
    if not key:
        return {'class': 'needs-setup',
                'detail': '%s running at %s; API key missing (%s.json)' % (name, base, name)}
    code, data = _arr_api(base, ARR_API[name], key, name)
    if code == 200 and data:
        appname = (data.get('appName') or '').lower()
        if appname != name:
            return {'class': 'wrong-service',
                    'detail': 'authenticated endpoint reports appName=%r, expected %s'
                              % (appname, name)}
        return {'class': 'ready', 'detail': '%s %s at %s: API authenticated'
                % (name, data.get('version', ''), base)}
    if code == 401:
        return {'class': 'needs-setup', 'detail': 'API key rejected at %s (401)' % base}
    return {'class': 'needs-setup', 'detail': '%s/status HTTP %s' % (ARR_API[name], code)}


def _jellyfin_headers(cfg):
    token = (cfg or {}).get('token') or (cfg or {}).get('key') or ''
    return {'Authorization': 'MediaBrowser Token="%s"' % token,
            'X-Emby-Token': token}


def _detect_jellyfin(cfg):
    base = _base(cfg, 'jellyfin')
    port = SERVICE_PORTS['jellyfin']
    code, body, _ = system.http_probe(base + '/system/info/public', timeout=5)
    if code is None:
        if _occupied(cfg, 'jellyfin', base):
            return {'class': 'port-occupied',
                    'detail': 'socket %s is occupied but no Jellyfin identity '
                              'answered (blocked; not installing anything)' % base}
        return {'class': 'unavailable', 'detail': 'no answer at %s' % base}
    product, wizard = '', None
    if body:
        try:
            data = json.loads(body)
            product = (data.get('ProductName') or '').lower()
            wizard = data.get('StartupWizardCompleted')
        except ValueError:
            pass
    if 'jellyfin' not in product:
        if code and code < 500 and body:
            return {'class': 'wrong-service',
                    'detail': '%s answered HTTP %s but is not Jellyfin' % (base, code)}
        return {'class': 'port-occupied',
                'detail': 'port %d occupied by an unrecognized service' % port}
    if wizard is False:
        return {'class': 'needs-setup', 'detail': 'first-run setup wizard not completed'}
    token = (cfg or {}).get('token') or (cfg or {}).get('key') or ''
    if not token:
        return {'class': 'needs-setup',
                'detail': 'Jellyfin running; create the readonly metadata token '
                          'and put it in jellyfin.json'}
    user_id = (cfg or {}).get('userId') or ''
    # Readonly tokens: verify with an allowed metadata endpoint (admin /System/Info
    # is deliberately not required).
    if user_id:
        path = '/Users/' + urllib.parse.quote(user_id)
    else:
        path = '/Shows/NextUp?limit=1'
    code, body2, _ = system.http_probe(base + path, timeout=5,
                                       headers=_jellyfin_headers(cfg))
    if code == 200:
        return {'class': 'ready', 'detail': 'readonly metadata token accepted at %s' % base}
    if code == 401:
        return {'class': 'needs-setup', 'detail': 'token rejected at %s (401)' % base}
    return {'class': 'needs-setup', 'detail': 'metadata endpoint HTTP %s' % code}


def _detect_transmission(cfg):
    base = _base(cfg, 'transmission')
    user = (cfg or {}).get('user') or ''
    password = (cfg or {}).get('password') or ''
    url = base + '/transmission/rpc'
    import base64
    headers = {'X-Transmission-Session-Id': ''}
    if user or password:
        token = base64.b64encode(('%s:%s' % (user, password)).encode()).decode()
        headers['Authorization'] = 'Basic ' + token
    try:
        code, body, headers_r = system.http_probe(
            url, method='POST', headers=headers, json_body={
                'method': 'session-get', 'arguments': {}}, timeout=5)
        if code == 409 and headers_r.get('x-transmission-session-id'):
            headers['X-Transmission-Session-Id'] = headers_r['x-transmission-session-id']
            code, body, _ = system.http_probe(
                url, method='POST', headers=headers, json_body={
                    'method': 'session-get', 'arguments': {}}, timeout=5)
        if code == 200:
            try:
                payload = json.loads(body)
            except ValueError:
                return {'class': 'wrong-service',
                        'detail': '%s answered with non-RPC payload' % base}
            if payload.get('result') == 'success':
                if not (user or password) and cfg is not None and 'user' in cfg:
                    return {'class': 'needs-setup',
                            'detail': 'Transmission accepted empty credentials; '
                                      'set user/password in transmission.json'}
                return {'class': 'ready', 'detail': 'RPC authenticated at %s' % base}
            return {'class': 'needs-setup',
                    'detail': 'RPC result=%s' % payload.get('result')}
        if code == 401:
            return {'class': 'needs-setup',
                    'detail': 'RPC credentials rejected at %s (401)' % base}
    except OSError:
        pass
    if _occupied(cfg, 'transmission', base):
        return {'class': 'port-occupied',
                'detail': 'socket %s is occupied but no Transmission RPC answered '
                          '(blocked; not installing anything)' % base}
    return {'class': 'unavailable', 'detail': 'no Transmission RPC at %s' % base}


def _detect_jackett(cfg):
    base = _base(cfg, 'jackett')
    port = SERVICE_PORTS['jackett']
    key = (cfg or {}).get('key') or ''
    _, body_root, _ = system.http_probe(base + '/', timeout=5)
    identity = (body_root or b'').decode('utf-8', 'replace').lower()
    if 'jackett' not in identity:
        if _occupied(cfg, 'jackett', base):
            return {'class': 'port-occupied',
                    'detail': 'socket %s is occupied but Jackett UI not recognized '
                              '(blocked; not installing anything)' % base}
        return {'class': 'unavailable', 'detail': 'no Jackett UI at %s' % base}
    if not key:
        return {'class': 'needs-setup',
                'detail': 'Jackett running at %s; copy its API key into jackett.json' % base}
    caps = base + '/api/v2.0/indexers/all/results/torznab/api?t=caps&apikey=' \
        + urllib.parse.quote(key)
    code, body, _ = system.http_probe(caps, timeout=6)
    if code == 200 and body and (b'<indexers' in body or b'torznab' in body):
        return {'class': 'ready', 'detail': 'Torznab API accepts the key at %s' % base}
    if code == 401:
        return {'class': 'needs-setup', 'detail': 'API key rejected (401)'}
    return {'class': 'needs-setup', 'detail': 'Torznab caps HTTP %s' % code}


def _detect_seerr(cfg):
    base = _base(cfg, 'seerr')
    port = SERVICE_PORTS['seerr']
    # Real schema: /api/v1/settings/public exposes {initialized, ...} for the
    # Overseerr/Jellyseerr/Seerr family; /api/v1/status has version fields but
    # no reliable appName on current Seerr.
    code, body, _ = system.http_probe(base + '/api/v1/settings/public', timeout=5)
    initialized = None
    if code == 200 and body:
        try:
            data = json.loads(body)
            if isinstance(data, dict) and 'initialized' in data:
                initialized = bool(data['initialized'])
        except ValueError:
            pass
    if initialized is None:
        code2, body2, _ = system.http_probe(base + '/api/v1/status', timeout=5)
        looks_seerr = False
        if code2 == 200 and body2:
            try:
                data = json.loads(body2)
                looks_seerr = isinstance(data, dict) and 'version' in data
            except ValueError:
                pass
        if not looks_seerr:
            if _occupied(cfg, 'seerr', base):
                return {'class': 'port-occupied',
                        'detail': 'socket %s is occupied but no Seerr settings/public '
                                  'API (blocked; not installing anything)' % base}
            return {'class': 'unavailable', 'detail': 'no Seerr API at %s' % base}
        return {'class': 'needs-setup',
                'detail': 'Seerr-family service at %s; complete web setup' % base}
    if not initialized:
        return {'class': 'needs-setup',
                'detail': 'Seerr at %s: run the web setup wizard' % base}
    return {'class': 'ready',
            'detail': 'Seerr initialized at %s (verify Jellyfin connection in its UI)'
                      % base}


def _detect_flaresolverr(cfg):
    base = _base(cfg, 'flaresolverr')
    port = SERVICE_PORTS['flaresolverr']
    code, body, _ = system.http_probe(base + '/health', timeout=8)
    if code is None:
        code2, body2, _ = system.http_probe(base + '/', timeout=5)
        if code2 is None:
            if _occupied(cfg, 'flaresolverr', base):
                return {'class': 'port-occupied',
                        'detail': 'socket %s is occupied (blocked; not installing '
                                  'anything)' % base}
            return {'class': 'unavailable', 'detail': 'no FlareSolverr at %s' % base}
        body = body2
    text = (body or b'').decode('utf-8', 'replace').lower()
    if 'flaresolverr' in text or '"status"' in text:
        return {'class': 'ready', 'detail': 'FlareSolverr answering at %s' % base}
    return {'class': 'port-occupied',
            'detail': 'port %d occupied by unknown service' % port}


def detect(name, connections=None, docker=None):
    """Classify one service.

    connections maps name -> {base_url, key|token/userId|user/password}.
    docker is an optional pre-fetched label map (see system.docker_services()).
    """
    if name not in SERVICE_PORTS:
        raise ValueError('unknown service: %s' % name)
    cfg = dict((connections or {}).get(name) or {})
    docker = docker if docker is not None else system.docker_services()
    result = _DETECTORS[name](cfg)
    result['managed'] = name in docker
    if result['managed']:
        result['detail'] += ' [container: %s]' % docker[name]['name']
    return _classify_result(result, cfg, name)


_DETECTORS = {
    'sonarr': lambda cfg: _detect_arr('sonarr', cfg),
    'radarr': lambda cfg: _detect_arr('radarr', cfg),
    'lidarr': lambda cfg: _detect_arr('lidarr', cfg),
    'prowlarr': lambda cfg: _detect_arr('prowlarr', cfg),
    'jellyfin': _detect_jellyfin,
    'transmission': _detect_transmission, 'jackett': _detect_jackett,
    'seerr': _detect_seerr, 'flaresolverr': _detect_flaresolverr,
}


def docker_labels():
    return system.docker_services()
