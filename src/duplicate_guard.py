"""Keep panel-managed torrents from retaining a second copy of imported episodes.

Sonarr imports with copy mode, so every completed source file doubles disk use.
The upgrade path already removes an old release after a verified replacement;
this pass covers ordinary imports and stale duplicates left by earlier runs.
"""
import logging
import os
import re
import shutil
import stat
import time
from pathlib import Path

import runtime as rt


def _source_path(m, torrent, index, episodes):
    files = torrent.get('files') or []
    if not isinstance(index, int) or not 0 <= index < len(files):
        return None
    raw_base = torrent.get('downloadDir', '').rstrip('/')
    downloads = rt.media_paths()['downloads']
    if not raw_base.startswith(downloads + '/'):
        return None
    raw = raw_base + '/' + episodes.clean_file_path(files[index].get('name', ''))
    host = rt.media_paths()['torrent_host'] + raw.removeprefix(downloads)
    try:
        return m.safe_path(host, rt.media_paths()['torrent_host'])
    except (OSError, ValueError):
        return None


def _remove_path(m, path):
    """Remove a verified regular source or its Transmission .part placeholder."""
    candidates = [path, path.with_name(path.name + '.part')]
    removed = 0
    for candidate in candidates:
        try:
            if not candidate.is_file():
                continue
            size = candidate.stat().st_size
            expected = m.fingerprint(candidate)
            m.disable_source(path)
            m.unlink_media(candidate, expected)
            removed += size
        except (OSError, ValueError) as exc:
            logging.warning('Duplicate source cleanup deferred: %s', type(exc).__name__)
    return removed


def _remove_orphaned_job(m, entry):
    members = entry.get('episodes') or []
    if any(x.get('state') not in ('done', 'cancelled') for x in members):
        return []
    if not members or not any(x.get('state') == 'done' for x in members):
        return []
    job = next((x.get('job') for x in members if isinstance(x.get('job'), str)), None)
    if not job or not re.fullmatch(r'[0-9a-f]{32}', job):
        return []
    torrent_root = Path(rt.media_paths()['torrent_host'])
    root = torrent_root / 'complete' / 'tv' / 'Panel' / job
    if root.is_symlink() or not root.is_dir():
        entry['sourceOrphanCleanedAt'] = time.time()
        return []
    removed = []
    for path in root.rglob('*'):
        if path.is_file():
            removed.append({'torrent': job, 'path': str(path), 'bytes': path.stat().st_size})
    try:
        shutil.rmtree(root)
        entry['sourceOrphanCleanedAt'] = time.time()
        return removed
    except OSError as exc:
        logging.warning('Orphan source directory cleanup deferred: %s', type(exc).__name__)
        return []


def _episode_key(path):
    match = re.search(r'(?i)[s]([0-9]{1,2})[e]([0-9]{1,3})', path.name)
    return (int(match.group(1)), int(match.group(2))) if match else None


def _unlink_library_file(m, path, root):
    try:
        path = m.safe_path(path, root)
        with m.parent_fd(path, root) as (parent, name):
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
                raise ValueError('Not a positive regular file')
            os.unlink(name, dir_fd=parent)
            return info.st_size
    except (OSError, ValueError) as exc:
        logging.warning('Library duplicate cleanup deferred: %s', type(exc).__name__)
        return 0


def run_library(m, episodes):
    """Keep the Sonarr-registered file when stale episode copies remain on disk."""
    root = Path(rt.media_paths()['shows_host'])
    groups = {}
    for path in root.rglob('*'):
        if not path.is_file() or path.suffix.lower() not in {'.mkv', '.mp4', '.avi', '.m4v', '.ts', '.m2ts', '.webm'}:
            continue
        number = _episode_key(path)
        if not number:
            continue
        series_dir = path.parent.parent if path.parent.name.lower().startswith('season') else path.parent
        groups.setdefault((series_dir, number), []).append(path)
    duplicates = {key: paths for key, paths in groups.items() if len(paths) > 1}
    removed = []
    unresolved = []
    if duplicates:
        try:
            series = m.api('series')
        except Exception as exc:
            logging.warning('Library duplicate lookup failed: %s', type(exc).__name__)
            series = []
        for (series_dir, number), paths in duplicates.items():
            current = None
            for item in series:
                raw = item.get('path', '')
                if not raw.startswith(rt.media_paths()['sonarr_tv'] + '/'):
                    continue
                candidate = Path(root) / raw.removeprefix(rt.media_paths()['sonarr_tv'] + '/')
                if candidate == series_dir:
                    current = item
                    break
            if not current:
                unresolved.append({'series': str(series_dir), 'episode': 'S%02dE%02d' % number})
                continue
            episodes_for_series = m.api('episode?seriesId=' + str(current['id']))
            registered = [x for x in episodes_for_series if (x.get('seasonNumber'), x.get('episodeNumber')) == number and x.get('hasFile') and x.get('episodeFileId')]
            if len(registered) != 1:
                unresolved.append({'series': current.get('title'), 'episode': 'S%02dE%02d' % number})
                continue
            media = m.api('episodefile/' + str(registered[0]['episodeFileId']))
            raw = media.get('path', '')
            if not raw.startswith(rt.media_paths()['sonarr_tv'] + '/'):
                unresolved.append({'series': current.get('title'), 'episode': 'S%02dE%02d' % number})
                continue
            keep = Path(root) / raw.removeprefix(rt.media_paths()['sonarr_tv'] + '/')
            if keep not in paths:
                unresolved.append({'series': current.get('title'), 'episode': 'S%02dE%02d' % number, 'reason': 'registered file not in duplicate group'})
                continue
            for path in paths:
                if path == keep:
                    continue
                size = _unlink_library_file(m, path, root)
                if size:
                    removed.append({'series': current.get('title'), 'episode': 'S%02dE%02d' % number, 'path': str(path), 'bytes': size})
    result = {'state': 'Дубли в медиатеке не найдены' if not duplicates and not unresolved else ('Очистка завершена' if removed or not unresolved else 'Требуется ручная проверка'), 'groups': len(duplicates), 'removed': len(removed), 'bytes': sum(x['bytes'] for x in removed), 'time': time.time(), 'files': removed, 'unresolved': unresolved}
    episodes.atomic(m.WORKER_STATE / 'duplicate-library-status.json', result, 0o600)
    return result


def run(m, episodes):
    """Deselect and delete source files for episodes this panel already imported."""
    removed = []
    try:
        with episodes.DOWNLOAD_LOCK:
            managed = episodes.read(m.WORKER_STATE / 'managed-downloads.json', {})
            if not managed:
                return {'state': 'Нет управляемых раздач', 'removed': 0}
            torrents = m.trpc('torrent-get', {'fields': ['id', 'name', 'hashString', 'status', 'downloadDir', 'files', 'fileStats']})['torrents']
            by_hash = {str(x.get('hashString', '')).lower(): x for x in torrents}
            changed = False
            for digest, entry in managed.items():
                torrent = by_hash.get(str(digest).lower())
                if not torrent:
                    orphan_removed = _remove_orphaned_job(m, entry)
                    if orphan_removed:
                        removed.extend(orphan_removed)
                        changed = True
                    continue
                members = entry.get('episodes') or []
                finished = [x for x in members if x.get('state') == 'done']
                indexes = sorted({x.get('video') for x in finished if isinstance(x.get('video'), int) and 0 <= x.get('video') < len(torrent.get('files') or [])})
                if not indexes:
                    continue
                try:
                    m.trpc('torrent-set', {'ids': [torrent['id']], 'files-unwanted': indexes})
                except Exception as exc:
                    logging.warning('Duplicate source deselect failed: %s', type(exc).__name__)
                    continue
                for member in finished:
                    path = _source_path(m, torrent, member.get('video'), episodes)
                    if path is None:
                        continue
                    before = len(removed)
                    size = _remove_path(m, path)
                    if size:
                        removed.append({'torrent': torrent.get('name'), 'path': str(path), 'bytes': size})
                    if not path.exists() and not path.with_name(path.name + '.part').exists():
                        member['sourceCleanedAt'] = time.time()
                        changed = True
                    elif before != len(removed):
                        changed = True
                active = any(x.get('state') not in ('done', 'cancelled') for x in members)
                if not active:
                    try:
                        m.trpc('torrent-stop', {'ids': [torrent['id']]})
                    except Exception as exc:
                        logging.warning('Duplicate torrent stop failed: %s', type(exc).__name__)
            if changed:
                episodes.atomic(m.WORKER_STATE / 'managed-downloads.json', managed, 0o600)
        result = {'state': 'Проверка завершена', 'removed': len(removed), 'bytes': sum(x['bytes'] for x in removed), 'time': time.time(), 'files': removed[-100:]}
        episodes.atomic(m.WORKER_STATE / 'duplicate-source-status.json', result, 0o600)
        return result
    except Exception as exc:
        logging.warning('Duplicate source guard failed: %s', type(exc).__name__)
        try:
            episodes.atomic(m.WORKER_STATE / 'duplicate-source-status.json', {'state': 'Ошибка проверки', 'error': type(exc).__name__, 'time': time.time()}, 0o600)
        except Exception:
            pass
        return {'state': 'Ошибка проверки', 'removed': 0}
