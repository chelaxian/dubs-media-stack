"""OS-level helpers: processes, packages, distro detection, systemd, docker."""
import os
import shutil
import subprocess
from pathlib import Path


class CommandError(RuntimeError):
    pass


def repo_root():
    return Path(__file__).resolve().parents[1]


def is_root():
    return os.geteuid() == 0


def run(cmd, check=True, capture=True, timeout=120, env=None, stdin_text=None):
    """Run a command; never passes secrets on the command line unless caller owns them."""
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    proc = subprocess.run(
        cmd, capture_output=capture, text=True, timeout=timeout,
        env=full_env, input=stdin_text,
    )
    if check and proc.returncode != 0:
        raise CommandError(
            '%s failed (rc=%d): %s' % (' '.join(cmd), proc.returncode,
                                       (proc.stderr or proc.stdout).strip()[:400])
        )
    return proc


def distro():
    """(id, version_id) from /etc/os-release, e.g. ('debian', '12')."""
    info = {}
    p = Path('/etc/os-release')
    if not p.exists():
        return None, None
    for line in p.read_text().splitlines():
        if '=' in line and not line.startswith('#'):
            k, _, v = line.partition('=')
            info[k.strip()] = v.strip().strip('"')
    return info.get('ID', ''), info.get('VERSION_ID', '')


def supported_distro():
    did, ver = distro()
    return (did, ver) in (('debian', '12'), ('debian', '13'), ('ubuntu', '24.04'))


def have(binary):
    return shutil.which(binary) is not None


def docker_info():
    """(docker_binary, compose_invocation|None, compose_version|None)."""
    if not have('docker'):
        return None, None, None
    proc = run(['docker', 'compose', 'version', '--short'], check=False)
    if proc.returncode == 0:
        return 'docker', ['docker', 'compose'], proc.stdout.strip()
    if have('docker-compose'):
        proc2 = run(['docker-compose', 'version', '--short'], check=False)
        if proc2.returncode == 0:
            return 'docker', ['docker-compose'], proc2.stdout.strip()
    return 'docker', None, None


def apt_install(pkgs, distro_id=None, distro_ver=None):
    """Install packages via apt. Never curl|sh; never removes packages.

    Failures raise CommandError so callers never continue half-installed.
    """
    env = {'DEBIAN_FRONTEND': 'noninteractive'}
    run(['apt-get', 'update', '-qq'], env=env, timeout=300)
    proc = run(['apt-get', 'install', '-y', '-qq'] + list(pkgs),
               check=False, env=env, timeout=600)
    if proc.returncode != 0:
        raise CommandError('apt-get install %s failed: %s'
                           % (' '.join(pkgs), (proc.stderr or proc.stdout).strip()[:300]))
    return proc


SUPPORTED = (('debian', '12'), ('debian', '13'), ('ubuntu', '24.04'))
DOCKER_CODENAME = {('debian', '12'): 'bookworm', ('debian', '13'): 'trixie',
                   ('ubuntu', '24.04'): 'noble'}


def requests_available():
    try:
        import requests  # noqa: F401
        return True
    except ImportError:
        return False


def ensure_docker_deps():
    """Install engine + Compose v2, preserving any working existing Docker.

    Order: stock apt packages first; official Docker apt repository
    (docs.docker.com) only for what stock repos lack — engine from the repo
    only when no engine exists at all, and never an upgrade/removal of a
    running docker.io.
    """
    did, ver = distro()
    if (did, ver) not in SUPPORTED:
        raise CommandError('unsupported distro %s %s (need Debian 12/13 or Ubuntu 24.04)'
                           % (did, ver))
    engine = have('docker')
    _, invocation, version = docker_info()
    compose_ok, _ = compose_v2_check(invocation, version)  # False when missing
    installed = []
    if not engine:
        # Stock engine first; official Docker repo only if stock repos lack it.
        try:
            apt_install(['docker.io'], did, ver)
            installed.append('docker.io')
        except CommandError:
            add_docker_repo(did, ver)
            apt_install(['docker-ce', 'docker-ce-cli', 'containerd.io',
                         'docker-buildx-plugin', 'docker-compose-plugin'], did, ver)
            installed.append('docker-ce stack (official Docker repo)')
    if not compose_ok:
        # Compose v2: stock package on trixie/noble; official Docker repo plugin
        # elsewhere (the plugin works alongside an existing docker.io engine
        # and never touches a working engine).
        try:
            apt_install(['docker-compose-v2'], did, ver)
            installed.append('docker-compose-v2')
        except CommandError:
            add_docker_repo(did, ver)
            apt_install(['docker-compose-plugin'], did, ver)
            installed.append('docker-compose-plugin (official Docker repo)')
    return installed


def compose_v2_check(invocation, version):
    """Our compose file needs Compose v2+ (top-level name:, profiles).

    Accepts any major >= 2 — the compose v2 bundled with Docker and newer
    standalone v5.x releases alike. Version strings may carry a leading 'v'
    (e.g. 'v2.39.2', 'v5.7.0'). Missing compose (invocation None) is False.
    """
    if invocation is None:
        return False, 'Docker Compose v2 not found; this compose file requires it'
    try:
        major = int(str(version).strip().lstrip('vV').split('.')[0])
    except (ValueError, AttributeError, TypeError):
        major = 0
    if major < 2:
        return False, 'Docker Compose v1 is not supported (found %s); install v2+' % version
    return True, ''


def add_docker_repo(distro_id, distro_ver):
    """Configure the official Docker apt repo (stdlib only, no curl|sh)."""
    import urllib.request
    codename = DOCKER_CODENAME[(distro_id, distro_ver)]
    keyring = Path('/usr/share/keyrings/docker.asc')
    if not keyring.exists():
        url = 'https://download.docker.com/linux/%s/gpg' % distro_id
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = resp.read()
        keyring.parent.mkdir(parents=True, exist_ok=True)
        keyring.write_bytes(data)
        os.chmod(keyring, 0o644)
    arch = run(['dpkg', '--print-architecture']).stdout.strip()
    source = ('deb [arch=%s signed-by=%s] https://download.docker.com/linux/%s %s stable'
              % (arch, keyring, distro_id, codename))
    listing = Path('/etc/apt/sources.list.d/docker.list')
    if not listing.exists() or source not in listing.read_text():
        listing.write_text(source + '\n')
        os.chmod(listing, 0o644)
    run(['apt-get', 'update', '-qq'],
        env={'DEBIAN_FRONTEND': 'noninteractive'}, timeout=300)


def systemctl(action, unit, check=True):
    return run(['systemctl', action, unit], check=check, timeout=60)


def unit_state(unit):
    """(active_state, enabled_state) as reported by systemctl (may be absent)."""
    active = run(['systemctl', 'is-active', unit], check=False).stdout.strip()
    enabled = run(['systemctl', 'is-enabled', unit], check=False).stdout.strip()
    return active or 'inactive', enabled or 'not-found'


def port_in_use(port, host='127.0.0.1'):
    """True if something accepts TCP connections on host:port."""
    import socket
    for family, addr in [(socket.AF_INET, (host, port))] + \
            ([(socket.AF_INET6, ('::1', port))] if host == '127.0.0.1' else []):
        try:
            with socket.create_connection(addr, timeout=1):
                return True
        except OSError:
            continue
    return False


def docker_services():
    """Map of com.dubs.service label -> container name for running containers."""
    if not have('docker'):
        return {}
    proc = run(['docker', 'ps', '--format',
                '{{.Names}}\t{{.Label "com.dubs.service"}}\t{{.Ports}}'],
               check=False)
    out = {}
    if proc.returncode != 0:
        return out
    for line in proc.stdout.splitlines():
        parts = line.split('\t')
        if len(parts) == 3 and parts[1]:
            out[parts[1]] = {'name': parts[0], 'ports': parts[2]}
    return out


def http_probe(url, timeout=3, method='GET', headers=None, json_body=None):
    """(status|None, body_snippet, dict(headers_lower)) using ONLY the stdlib.

    plan/doctor must run before python3-requests is installed, so this never
    imports requests. Never raises.
    """
    import urllib.request
    import urllib.error
    req = urllib.request.Request(url, method=method, headers=headers or {})
    data = None
    if json_body is not None:
        import json as _json
        data = _json.dumps(json_body).encode()
        req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
            return r.status, r.read(2048), {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        try:
            body = e.read(2048)
        except Exception:
            body = b''
        return e.code, body, {k.lower(): v for k, v in e.headers.items()}
    except (urllib.error.URLError, OSError, ValueError):
        return None, b'', {}
