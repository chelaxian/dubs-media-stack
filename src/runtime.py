"""Portable runtime configuration for the Dubs panel and its automations.

All deployment-specific values (paths, hosts, ports, accounts) come from the
config directory and may be relocated with environment variables so the same
code runs on the production host, inside a container and in offline tests.

Environment overrides (all optional):
  SERIES_CONTROL_CONFIG_ROOT   main config dir      (default /etc/series-control)
  SERIES_CONTROL_STATE_ROOT    web state dir        (default /var/lib/series-control)
  SERIES_CONTROL_WORKER_ROOT   worker state dir     (default /var/lib/series-control-worker)
  SERIES_CONTROL_UI_DIR        ui assets dir        (default config root)
  SERIES_CONTROL_STUDIOS       studios.json path    (default <config root>/../series-control-studios.json)
  SERIES_CONTROL_FEED_STATE    myshows feed state   (default /var/lib/myshows-sync)
  SERIES_CONTROL_COVERS        poster cache         (default /var/cache/series-control-covers)
  SERIES_CONTROL_TIMEZONE      timezone             (default Europe/Moscow, config may override)

The module never contains deployment addresses and never logs secrets.
"""
import json, os, secrets, threading
from pathlib import Path

_LOCK = threading.RLock()
_CACHE = {}

def config_root():
    return Path(os.environ.get('SERIES_CONTROL_CONFIG_ROOT', '/etc/series-control'))

def state_root():
    p = Path(os.environ.get('SERIES_CONTROL_STATE_ROOT', '/var/lib/series-control'))
    p.mkdir(parents=True, exist_ok=True)
    return p

def worker_root():
    p = Path(os.environ.get('SERIES_CONTROL_WORKER_ROOT', '/var/lib/series-control-worker'))
    p.mkdir(parents=True, exist_ok=True)
    return p

def ui_dir():
    """Directory holding the panel code and UI assets (render.py, ui.js, style.css)."""
    return Path(os.environ.get('SERIES_CONTROL_UI_DIR', '/etc/series-control-ui'))

def studios_path():
    env = os.environ.get('SERIES_CONTROL_STUDIOS')
    if env:return Path(env)
    sibling = config_root().parent/'series-control-studios.json'
    return sibling if sibling.exists() else config_root()/'studios.json'

def feed_state_root():
    p = Path(os.environ.get('SERIES_CONTROL_FEED_STATE', '/var/lib/myshows-sync'))
    p.mkdir(parents=True, exist_ok=True)
    return p

def covers_root():
    p = Path(os.environ.get('SERIES_CONTROL_COVERS', '/var/cache/series-control-covers'))
    p.mkdir(parents=True, exist_ok=True)
    return p

def load_json(path, default=None, required=False):
    p = Path(path)
    try:
        with p.open() as f:return json.load(f)
    except FileNotFoundError:
        if required:raise
        return {} if default is None else default

def _settings_uncached():
    cfg = load_json(config_root()/'config.json', {})
    out = {
        'proxy': cfg.get('proxy') or '',                       # optional SOCKS5 host:port; empty = direct
        'login': cfg.get('login') or '',                       # MyShows account that tokens must belong to
        'timezone': cfg.get('timezone') or os.environ.get('SERIES_CONTROL_TIMEZONE', 'Europe/Moscow'),
        'owner_label': cfg.get('owner_label') or cfg.get('login') or 'Owner',
        'public_url': (cfg.get('public_url') or '').rstrip('/'),   # https://panel.example.com
        'jellyfin_api_url': cfg.get('jellyfin_api_url', ''),  # non-secret login/logout transport; empty = connection fallback
        'jellyfin_url': (cfg.get('jellyfin_url') or '').rstrip('/'),  # https://jellyfin.example.com
        'mobile': cfg.get('mobile') or {},                     # {"artifacts":[{"file","label"}]}
        'paths': dict(cfg.get('paths') or {}),                 # host/container media roots
        'panel': dict(cfg.get('panel') or {}),                 # bind host/ports/tls paths
        'indexer_endpoints': list(cfg.get('indexer_endpoints') or []),  # trusted torrent download endpoints
        'veto_format': cfg.get('veto_format') or 'ОБЯЗАТЕЛЬНО RU — запрет без русского',
        'cleanup_enable': bool(cfg.get('cleanup_enable')),     # watched cleanup is opt-in and off by default
        'daily_upgrade_hour': int(cfg.get('daily_upgrade_hour', 18)),
        'storage_sentinel': bool(cfg.get('storage_sentinel', True)),  # require armed storage before deleting
    }
    return out

def settings():
    with _LOCK:
        if 'settings' not in _CACHE:_CACHE['settings'] = _settings_uncached()
        return _CACHE['settings']

def reset_cache():
    with _LOCK:_CACHE.clear()

def timezone_name():
    return settings()['timezone']

def media_paths():
    defaults = {
        'shows_host':'/mnt/shows', 'torrent_host':'/mnt/torrent',
        'sonarr_tv':'/tv', 'sonarr_torrents':'/torrents', 'downloads':'/downloads',
    }
    out = dict(defaults);out.update(settings()['paths'])
    for key,value in list(out.items()):
        if not isinstance(value,str) or not value.startswith('/'):out[key]=defaults[key]
    return out

def indexer_endpoints():
    """Exact trusted torrent-download endpoints used by safe_download.

    Each entry: {"host": ..., "ports": [9696], "path_prefixes": ["/api/v1/"],
    "scheme": "http", "tls_verify": True}. Implicit default ports are accepted
    (https->443, http->80) when that port is configured for the host. Loopback
    is always trusted for the configured Arr/Prowlarr ports; any additional
    host/port/scheme must be listed explicitly in config.json.
    """
    allowed = {}
    def add(host, ports, prefixes, scheme='http', tls_verify=True):
        host = str(host).lower()
        ports = [int(p) for p in (ports or []) if isinstance(p,int) and 0<p<65536]
        if not host or not ports or not prefixes:return
        entry = allowed.setdefault(host, {'ports':set(),'prefixes':set(),'tls':{}})
        entry['ports'].update(ports)
        entry['prefixes'].update(p for p in prefixes if isinstance(p,str) and p.startswith('/'))
        if scheme in ('http','https'):
            for port in ports:
                entry['tls'].setdefault((scheme,port),set()).add(bool(tls_verify))
    for host, ports, prefixes in [('127.0.0.1',[9696,8989,9117],['/api/v1/','/api/v3/','/api/v2.0/']),
                                  ('localhost',[9696,8989,9117],['/api/v1/','/api/v3/','/api/v2.0/'])]:
        add(host, ports, prefixes)
    for item in settings()['indexer_endpoints']:
        if not isinstance(item,dict):continue
        add(item.get('host',''), item.get('ports') or [], item.get('path_prefixes') or [],
            item.get('scheme') or 'http', item.get('tls_verify', True))
    return {h:{'ports':v['ports'],'prefixes':v['prefixes'],
               'tls':{k:sorted(vv) for k,vv in v['tls'].items()}} for h,v in allowed.items()}

def jellyfin_api_base():
    """Non-secret login transport, independent of the private metadata identity.

    Empty keeps portable installs using their configured Jellyfin connection.
    An explicit invalid URL fails closed, without reading that private file.
    """
    from urllib.parse import urlsplit
    base = settings().get('jellyfin_api_url', '')
    if base == '' or base is None:
        base = connection('jellyfin')['base_url']
    if not isinstance(base, str) or not base or any(ord(c) <= 32 or ord(c) == 127 for c in base) or '\\' in base:
        raise ValueError('Invalid Jellyfin API base URL')
    parsed = urlsplit(base)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or '?' in base or '#' in base or not parsed.netloc
            or '%' in parsed.netloc):
        raise ValueError('Invalid Jellyfin API base URL')
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError('Invalid Jellyfin API port')
    return base.rstrip('/')

def connection(name):
    """Service connection config: accepts {"base_url","key"} or legacy {"port","key"}."""
    cfg = load_json(config_root()/(name+'.json'), required=True)
    base = cfg.get('base_url') or ('http://127.0.0.1:'+str(cfg.get('port')) if cfg.get('port') else '')
    return {'base_url':str(base).rstrip('/'), 'key':cfg.get('key',''), **{k:v for k,v in cfg.items() if k not in ('base_url','port','key')}}

def panel_bind():
    p = settings()['panel']
    return {
        'host':p.get('host') or '127.0.0.1',
        'http':int(p.get('http',9192)), 'https':int(p.get('https',9193)),
        'cert':p.get('cert') or str(config_root()/'server.crt'),
        'key':p.get('key') or str(config_root()/'server.key'),
    }

def mobile_artifacts():
    """Only artifacts explicitly configured AND present in the releases dir are offered."""
    out = []
    base = settings()['mobile'].get('dir') or '/opt/dubs-mobile/releases'
    for item in settings()['mobile'].get('artifacts') or []:
        name = item.get('file') or ''
        if not name or '/' in name or '\\' in name or name.startswith('.'):continue
        p = Path(base)/name
        try:
            if p.is_file() and not p.is_symlink():out.append({'file':name,'label':item.get('label') or name})
        except OSError:continue
    return out

def cleanup_enabled():
    return settings()['cleanup_enable']

def resolve_studio(studios, value):
    """Current index of a preferred studio.

    Preferences store the studio NAME so reordering studios.json never
    silently points an existing preference at another studio. Legacy numeric
    values keep working (resolved against the current order until re-saved).
    Returns None when the value cannot be resolved.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value < len(studios) else None
    if isinstance(value, str):
        for i, x in enumerate(studios):
            if x.get('name') == value:
                return i
    return None

def storage_ready():
    """(ok, reason) — torrent storage is mounted and, by default, armed.

    Mutating jobs (watched cleanup, download replacement) must refuse to act
    while storage looks unmounted or the operator has not armed the sentinel.
    The sentinel catches the common "Docker created an empty dir on /" case;
    it cannot prove the mount is the *intended* one, so verify mounts once.
    Adopted setups that never use the compose stack may set
    storage_sentinel=false in config.json, but the mount must still exist.
    """
    root = Path(media_paths()['torrent_host'])
    try:
        st = os.statvfs(root)
    except OSError:
        return False, 'хранилище не смонтировано: %s' % root
    if st.f_blocks <= 0:
        return False, 'хранилище сообщает 0 блоков: %s' % root
    if settings()['storage_sentinel']:
        # The compose stack arms MEDIA_ROOT/.dubs-storage-ok (the parent of the
        # torrent dir); a plain /mnt/torrent layout arms it next to the data.
        candidates = [root/'.dubs-storage-ok']
        parent = root.parent
        if parent != root and parent not in ('/',):
            candidates.append(parent/'.dubs-storage-ok')
        if not any(c.is_file() for c in candidates):
            return False, ('нет sentinel-файла ни в %s, ни в %s (один раз: '
                           'install -m 0644 /dev/null <хранилище>/.dubs-storage-ok)'
                           % (candidates[0], candidates[-1]))
    return True, ''

def generate_signing_key():
    return secrets.token_hex(32)
