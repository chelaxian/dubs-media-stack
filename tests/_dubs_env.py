"""Offline test environment for the Dubs test suite.

Importing this module (side effects intentional, like conftest.py):
  - points every SERIES_CONTROL_* runtime root at a throwaway temp dir,
  - puts src/ and automations/ on sys.path,
  - exposes load_script() for file-based modules (control.py, automations).

No test in this suite talks to a live service: all HTTP is stubbed or served
from local threads on ephemeral 127.0.0.1 ports.
"""
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / 'src'
AUTOMATIONS = REPO / 'automations'
INSTALLER = REPO / 'installer'

TMP = Path(tempfile.mkdtemp(prefix='dubs-test-'))

ENV = {
    'SERIES_CONTROL_CONFIG_ROOT': str(TMP / 'config'),
    'SERIES_CONTROL_STATE_ROOT': str(TMP / 'state'),
    'SERIES_CONTROL_WORKER_ROOT': str(TMP / 'worker'),
    'SERIES_CONTROL_UI_DIR': str(SRC),
    'SERIES_CONTROL_STUDIOS': str(REPO / 'studios.json'),
    'SERIES_CONTROL_FEED_STATE': str(TMP / 'feed'),
    'SERIES_CONTROL_COVERS': str(TMP / 'covers'),
    'DUBS_SRC': str(SRC),
    'SERIES_CONTROL_ROLE': 'test',
}
for key, value in ENV.items():
    os.environ[key] = value

# control.py -> security.py requires a fail-closed security.json at import;
# provide an empty-owner skeleton (logins stay closed, tests inject owners).
(_dubs_config := TMP / 'config').mkdir(parents=True, exist_ok=True)
import json
import secrets
_sec_file = _dubs_config / 'security.json'
if not _sec_file.exists():
    _sec_file.write_text(json.dumps({
        'signing_key': secrets.token_hex(48),
        'owner_names': [], 'owner_ids': [],
        'allowed_networks': ['127.0.0.1/32', '::1/128'],
    }))
os.chmod(_sec_file, 0o600)

# Service connection skeletons (loopback, empty keys) so offline tests never
# touch /etc or live hosts; individual tests override via patching.
_SERVICES = {
    'sonarr': 'http://127.0.0.1:8989', 'radarr': 'http://127.0.0.1:7878',
    'lidarr': 'http://127.0.0.1:8686', 'jellyfin': 'http://127.0.0.1:8096',
    'seerr': 'http://127.0.0.1:5055', 'transmission': 'http://127.0.0.1:9091',
    'prowlarr': 'http://127.0.0.1:9696', 'jackett': 'http://127.0.0.1:9117',
}
for _name, _base in _SERVICES.items():
    _svc = _dubs_config / (_name + '.json')
    if not _svc.exists():
        _entry = {'base_url': _base, 'key': ''}
        if _name == 'jellyfin':
            _entry = {'base_url': _base, 'token': '', 'userId': ''}
        elif _name == 'transmission':
            _entry.update({'user': '', 'password': ''})
        _svc.write_text(json.dumps(_entry))
        os.chmod(_svc, 0o600)

for p in (str(SRC), str(AUTOMATIONS)):
    if p not in sys.path:
        sys.path.insert(0, p)


def fresh_runtime():
    import runtime as rt
    rt.reset_cache()
    return rt


def load_script(name, path):
    """Import a file-based module (scripts with dashes etc.) as `name`."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod
