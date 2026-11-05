"""Render the real panel using isolated public fixture data, without config reads."""
import os
import json
from pathlib import Path
import sys
import tempfile
import types
root = Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory(prefix='dubs-client-render-') as directory:
    state = Path(directory)
    (state / 'config').mkdir()
    (state / 'feed').mkdir()
    (state / 'feed/status.json').write_text('{"updated":"2026-11-04T12:00:00"}')
    os.environ['SERIES_CONTROL_STATE_ROOT'] = str(state / 'web')
    os.environ['SERIES_CONTROL_WORKER_ROOT'] = str(state / 'worker')
    os.environ['SERIES_CONTROL_CONFIG_ROOT'] = str(state / 'config')
    os.environ['SERIES_CONTROL_FEED_STATE'] = str(state / 'feed')
    os.environ['SERIES_CONTROL_UI_DIR'] = str(root / 'src')
    sys.path.insert(0, str(root / 'src'))
    import render
    fixture = types.SimpleNamespace(STUDIOS=json.loads((root / 'studios.json').read_text()), load=lambda name, default: default,
                                    watching=lambda: [], mobile_artifacts=lambda: json.loads(os.environ.get("DUBS_TEST_ARTIFACTS", "[]")))
    print('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
          '<link rel="stylesheet" href="/style.css"><script src="/ui.js" defer></script><main>'
          + render.render(fixture, 'x' * 43) + '</main>')
