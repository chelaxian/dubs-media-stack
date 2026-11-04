"""Typed deployment configuration for the installer.

Loads operator choices from compose/.env plus CLI overrides and validates
them early: paths must be absolute, the feed must never bind wildcards, and
container-internal URLs stay separate from host-published ones.
"""
import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Bridge DNS names == compose service names on the dubs network.
INTERNAL = {
    'jellyfin': 'http://jellyfin:8096',
    'seerr': 'http://seerr:5055',
    'sonarr': 'http://sonarr:8989',
    'radarr': 'http://radarr:7878',
    'lidarr': 'http://lidarr:8686',
    'transmission': 'http://transmission:9091',
    'prowlarr': 'http://prowlarr:9696',
    'jackett': 'http://jackett:9117',
    'flaresolverr': 'http://flaresolverr:8191',
}

PUBLISHED = {
    'jellyfin': 'http://127.0.0.1:8096',
    'seerr': 'http://127.0.0.1:5055',
    'sonarr': 'http://127.0.0.1:8989',
    'radarr': 'http://127.0.0.1:7878',
    'lidarr': 'http://127.0.0.1:8686',
    'transmission': 'http://127.0.0.1:9091',
    'prowlarr': 'http://127.0.0.1:9696',
    'jackett': 'http://127.0.0.1:9117',
    'flaresolverr': 'http://127.0.0.1:8191',
}


class ConfigError(ValueError):
    pass


def read_env(path):
    """Minimal KEY=VALUE parser (no shell expansion, no secret logging)."""
    out = {}
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        value = value.strip().strip('"').strip("'")
        if value:
            out[key.strip()] = value
    return out


def require_absolute(name, value):
    if not value:
        raise ConfigError('%s is required (compose/.env)' % name)
    if not value.startswith('/'):
        raise ConfigError('%s must be an ABSOLUTE path (got %r) so containers '
                          'can never silently bind a wrong relative path' % (name, value))
    return value


@dataclass(frozen=True)
class Deployment:
    source_repo: Path = REPO          # checkout the installer runs from
    install_root: Path = Path('/opt/dubs')  # world-readable copy units execute from
    repo: Path = Path('/opt/dubs')    # the tree systemd units actually run
    user: str = 'dubs'
    group: str = 'dubs'
    puid: str = '1000'                # compose PUID: the container/media owner
    pgid: str = '1000'                # compose PGID; panel user joins this group
    config_root: Path = Path('/etc/series-control')
    state_root: Path = Path('/var/lib/series-control')
    worker_root: Path = Path('/var/lib/series-control-worker')
    feed_state: Path = Path('/var/lib/myshows-sync')
    covers: Path = Path('/var/cache/series-control-covers')
    studios: Path = Path('/etc/series-control/studios.json')
    torrent_host: Path = Path('/mnt/torrent')
    shows_host: Path = Path('/mnt/shows')
    media_root: Path = None
    data_root: Path = None
    python: str = '/usr/bin/python3'
    compose_dir: Path = REPO / 'compose'
    instance: str = 'main'
    feed_port: int = 9191
    feed_bind: tuple = ('127.0.0.1',)
    internal: dict = field(default_factory=lambda: dict(INTERNAL))
    published: dict = field(default_factory=lambda: dict(PUBLISHED))

    @staticmethod
    def load(env_file=None, **overrides):
        if env_file is None:
            # Prefer the installed tree's .env (created by `install`); fall back
            # to the checkout's own compose/.env.
            install = overrides.get('install_root') or Path(
                read_env(REPO / 'compose' / '.env').get('DUBS_INSTALL_ROOT', '/opt/dubs'))
            candidate = install / 'compose' / '.env'
            env_file = candidate if candidate.exists() else REPO / 'compose' / '.env'
        env = read_env(env_file)
        values = {}

        def pick(name, default=None, cast=str):
            if name in overrides and overrides[name] is not None:
                return overrides[name]
            if name in env:
                return cast(env[name])
            return default

        media = pick('MEDIA_ROOT')
        data = pick('DUBS_DATA_ROOT')
        if media:
            require_absolute('MEDIA_ROOT', media)
        if data:
            require_absolute('DUBS_DATA_ROOT', data)
        feeds = pick('DUBS_FEED_BIND')
        binds = tuple(h.strip() for h in (feeds or '127.0.0.1').split(',') if h.strip())
        if any(b in ('0.0.0.0', '::', '*') for b in binds):
            raise ConfigError('DUBS_FEED_BIND must not contain wildcard binds: '
                              'the feed exposes a personal watchlist')
        torrent = pick('TORRENT_HOST') or (
            (Path(media) / 'torrents') if media else '/mnt/torrent')
        shows = pick('SHOWS_HOST') or (
            (Path(media) / 'shows') if media else '/mnt/shows')
        install_root = Path(pick('DUBS_INSTALL_ROOT', '/opt/dubs'))
        values.update(
            media_root=Path(media) if media else None,
            data_root=Path(data) if data else None,
            torrent_host=Path(torrent),
            shows_host=Path(shows),
            repo=overrides.get('repo') or install_root,
            install_root=install_root,
            compose_dir=overrides.get('compose_dir') or (install_root / 'compose'),
            user=pick('PANEL_USER', 'dubs'),
            group=pick('PANEL_GROUP') or pick('PANEL_USER', 'dubs'),
            puid=pick('PUID', '1000'),
            pgid=pick('PGID', '1000'),
            feed_bind=binds,
            feed_port=int(pick('DUBS_FEED_PORT', 9191)),
        )
        for key in ('config_root', 'state_root', 'worker_root', 'feed_state',
                    'covers', 'studios', 'python', 'instance'):
            if overrides.get(key) is not None:
                values[key] = overrides[key]
        d = Deployment(**{**values, **{k: v for k, v in overrides.items()
                                       if k in {'source_repo'} and v is not None}})
        # Adopted/external services live on the operator's network, not the
        # compose bridge: DUBS_INTERNAL_<NAME>=http://host:port overrides the
        # bridge-DNS default. Defaults are only used for compose-managed ones.
        internal = dict(d.internal)
        for svc in internal:
            override = env.get('DUBS_INTERNAL_' + svc.upper())
            if override:
                if not override.startswith('http://') and not override.startswith('https://'):
                    raise ConfigError('DUBS_INTERNAL_%s must be an http(s) URL (got %r)'
                                      % (svc.upper(), override))
                internal[svc] = override
        object.__setattr__(d, 'internal', internal)
        return d

    def env_block(self):
        """Environment= lines shared by the panel/automation systemd units."""
        studios = self.studios if str(self.studios).startswith('/') else str(self.studios)
        return [
            'SERIES_CONTROL_CONFIG_ROOT=%s' % self.config_root,
            'SERIES_CONTROL_STATE_ROOT=%s' % self.state_root,
            'SERIES_CONTROL_WORKER_ROOT=%s' % self.worker_root,
            'SERIES_CONTROL_FEED_STATE=%s' % self.feed_state,
            'SERIES_CONTROL_COVERS=%s' % self.covers,
            'SERIES_CONTROL_STUDIOS=%s' % studios,
            'PYTHONPATH=%s/src' % self.repo,
            'DUBS_SRC=%s/src' % self.repo,
        ]

    def connections(self):
        """Service connections from config_root for discovery/bootstrap."""
        import os
        old = None
        if os.environ.get('SERIES_CONTROL_CONFIG_ROOT') != str(self.config_root):
            old = os.environ.get('SERIES_CONTROL_CONFIG_ROOT')
            os.environ['SERIES_CONTROL_CONFIG_ROOT'] = str(self.config_root)
        try:
            import runtime as rt2
            rt2.reset_cache()
            out = {}
            for name in ('sonarr', 'radarr', 'lidarr', 'jellyfin',
                         'transmission', 'prowlarr', 'jackett'):
                try:
                    cfg = rt2.connection(name)
                    if cfg.get('base_url') or cfg.get('key'):
                        out[name] = cfg
                except FileNotFoundError:
                    continue
            return out
        finally:
            if old is None:
                os.environ.pop('SERIES_CONTROL_CONFIG_ROOT', None)
            else:
                os.environ['SERIES_CONTROL_CONFIG_ROOT'] = old
            import runtime as rt3
            rt3.reset_cache()
