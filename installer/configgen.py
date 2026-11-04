"""Generate runtime configuration skeletons. Never overwrites without --force.

Generated files contain no real credentials and no deployment-specific
addresses beyond loopback defaults; the operator fills in keys and hosts.
"""
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

SERVICES = {
    # name -> default loopback base_url; keys are always empty on generation
    'sonarr': 'http://127.0.0.1:8989',
    'radarr': 'http://127.0.0.1:7878',
    'lidarr': 'http://127.0.0.1:8686',
    'jellyfin': 'http://127.0.0.1:8096',
    'seerr': 'http://127.0.0.1:5055',
    'transmission': 'http://127.0.0.1:9091',
    'prowlarr': 'http://127.0.0.1:9696',
    'jackett': 'http://127.0.0.1:9117',
}

# jellyfin.json is a READONLY metadata identity, never an admin token: the
# panel only reads libraries/users to build the playback and watch-state views.
JELLYFIN_ENTRY = {'base_url': SERVICES['jellyfin'], 'token': '', 'userId': ''}

CONFIG_EXAMPLE = {
    'login': '',                      # MyShows account the tokens must belong to
    'proxy': '',                      # optional socks5 host:port; empty = direct
    'timezone': 'Europe/Moscow',      # schedules the daily 18:00 upgrade pass
    'owner_label': '',
    'public_url': '',                 # https URL the panel is published under
    'jellyfin_api_url': '',           # non-secret login API base; empty uses jellyfin.json connection
    'jellyfin_url': '',               # https URL of the public Jellyfin
    'cleanup_enable': False,          # watched cleanup is opt-in; stays off on fresh installs
    'daily_upgrade_hour': 18,
    'veto_format': 'ОБЯЗАТЕЛЬНО RU — запрет без русского',
    'paths': {
        'shows_host': '/mnt/shows',
        'torrent_host': '/mnt/torrent',
        'sonarr_tv': '/tv',
        'sonarr_torrents': '/torrents',
        'downloads': '/downloads',
    },
    'panel': {
        'host': '127.0.0.1',
        'http': 9192,
        'https': 9193,
    },
    'indexer_endpoints': [],          # extra trusted download endpoints beyond loopback
    'mobile': {'dir': '', 'artifacts': []},
}


def security_skeleton():
    import runtime as rt  # noqa: local import keeps test envs isolated
    return {
        'signing_key': rt.generate_signing_key(),
        # Jellyfin owner gate: the panel login accepts ONLY these accounts.
        # owner_names = Jellyfin usernames; owner_ids = their Jellyfin UserId.
        # Both must match (plus IsAdministrator) — fill them in from Jellyfin.
        'owner_names': [],
        'owner_ids': [],
        'trusted_proxies': [],           # reverse-proxy IPs allowed to set X-Forwarded-*
        'allowed_networks': ['127.0.0.1/32', '::1/128'],
        'allowed_origins': [],           # canonical Origins, e.g. https://panel.example.org
        'public_hosts': [],              # canonical Host values served behind the proxy
    }


def write_json(path, data, force=False, mode=0o640):
    path = Path(path)
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    os.chmod(tmp, mode)
    os.replace(tmp, path)
    return True


def generate(config_root, force=False, torrent_host=None, shows_host=None,
             media_root=None, transmission_user=None, transmission_pass=None):
    """Create config.json, security.json, per-service json, studios.json.

    When compose storage roots are known (from compose/.env), config.json
    `paths` are seeded to match that layout so the worker/units agree with it.
    transmission.json is seeded with the SAME RPC credentials the compose
    daemon uses (TRANSMISSION_USER/PASS inputs mapped to LSIO USER/PASS),
    so the fresh daemon actually
    requires the auth the panel sends.
    Returns (created, skipped) lists of file paths.
    """
    root = Path(config_root)
    created, skipped = [], []
    config = dict(CONFIG_EXAMPLE)
    paths = dict(config['paths'])
    if torrent_host:
        paths['torrent_host'] = str(torrent_host)
    if shows_host:
        paths['shows_host'] = str(shows_host)
    if media_root:
        paths.setdefault('movies_host', str(Path(media_root) / 'movies'))
        paths.setdefault('music_host', str(Path(media_root) / 'music'))
    config['paths'] = paths
    plan = {
        root / 'config.json': config,
        root / 'security.json': security_skeleton(),
    }
    for name, base in SERVICES.items():
        if name == 'jellyfin':
            plan[root / (name + '.json')] = dict(JELLYFIN_ENTRY)
            continue
        entry = {'base_url': base, 'key': ''}
        if name == 'transmission':
            entry['user'] = transmission_user or ''
            entry['password'] = transmission_pass or ''
        plan[root / (name + '.json')] = entry
    for path, data in plan.items():
        (created if write_json(path, data, force=force) else skipped).append(path)
    studios_src = REPO / 'studios.json'
    studios_dst = root / 'studios.json'
    if studios_src.exists():
        (created if write_json(studios_dst, json.loads(studios_src.read_text()),
                               force=force, mode=0o644) else skipped).append(studios_dst)
    try:
        os.chmod(root, 0o750)
    except OSError:
        pass
    return created, skipped


def validate(config_root):
    """Load the generated configuration through the real runtime layer."""
    import runtime as rt
    rt.reset_cache()
    old = os.environ.get('SERIES_CONTROL_CONFIG_ROOT')
    os.environ['SERIES_CONTROL_CONFIG_ROOT'] = str(config_root)
    try:
        rt.reset_cache()
        rt.settings()
        for name in SERVICES:
            rt.connection(name)
        missing = [k for k in ('signing_key',) if k not in rt.load_json(
            rt.config_root() / 'security.json', required=True)]
        if missing:
            raise ValueError('security.json missing: %s' % missing)
    finally:
        if old is None:
            os.environ.pop('SERIES_CONTROL_CONFIG_ROOT', None)
        else:
            os.environ['SERIES_CONTROL_CONFIG_ROOT'] = old
        rt.reset_cache()
    return True
