"""Bootstrap integrations through the apps' own schema APIs.

Payloads are derived from the live /schema endpoints (shapes captured in
/tmp/dubs-real-schema-examples.json and /tmp/dubs-lidarr-schema.json during
development): Sonarr/Radarr/Lidarr quality profiles with nested quality
groups, custom formats, Transmission download clients with lowercase fields,
remote path mappings, Prowlarr applications and the Sonarr CustomImport list.

Idempotent and managed-object-only: bootstrap creates/updates only objects
with Dubs-managed names (Dubs RU profiles, "Озвучка NN — ...", RU evidence
formats, "Dubs Transmission", the transmission remote-path mapping, "MyShows
watching (Dubs feed)", the Prowlarr "Dubs" indexer profile) and never touches
user-created objects (user download clients, remote mappings, quality/indexer
profiles, formats). Panel-created studio clones are refreshed only when they
reference Dubs-owned base profile ids (see Arr.refresh_profile_clones).
"""
import re

import requests

# Bridge-DNS endpoint written into the apps' own configs (container-to-container).
CONTAINER_HOST = 'transmission'

VOICE_FORMAT_RE = re.compile(r'^Озвучка \d+ — ')
SERIES_FORMAT_PREFIX = 'Выбор сериала — '
RU_EVIDENCE_TITLE = 'Русская дорожка / RU'
RU_EVIDENCE_LANGUAGE = 'Русская дорожка — язык метаданных'

# Prowlarr: minimumSeeders belongs to the managed indexer (sync) profile,
# never to quality profiles.
PROWLARR_INDEXER_PROFILE = 'Dubs'
MINIMUM_SEEDERS = 1

# Panel-created per-studio clones of a Dubs base profile
# (src/control.py chosen_profile: 'Сериал / база <baseId> / <studio> / mode').
CLONE_PREFIX = 'Сериал / база '
CLONE_BONUS = 1000000

# Common RU-audio markers (in addition to the named studio patterns).
RU_COMMON = (r'(?<![a-zа-яё0-9])(?:rus(?:sian)?|ru|русск(?:ий|ая|ое)|dvo|mvo'
             r'|newstudio|tvshows|baibako|jaskier|амедиа)(?![a-zа-яё0-9])')


class ArrError(RuntimeError):
    pass


def _strip_flags(pattern):
    return re.sub(r'^\(\?i[sS]?\)', '', pattern.strip())


def ru_evidence_regex(studios):
    """Union of named-studio patterns + common RU markers.

    studios[-1] is the negative catch-all fallback ("everything else russian")
    and is deliberately NOT evidence: its pattern matches by exclusion, so it
    would match plain English releases too.
    """
    named = [_strip_flags(s['pattern']) for s in studios[:-1]]
    return '(?is)(?:' + '|'.join(named) + '|' + _strip_flags(RU_COMMON) + ')'


def ru_spec(regex, negate=False, required=False):
    return {
        'name': 'Russian studio / audio evidence',
        'implementation': 'ReleaseTitleSpecification',
        'negate': negate, 'required': required,
        'fields': [{'name': 'value', 'value': regex}],
    }


def language_spec(negate=False, required=False):
    return {
        'name': 'Russian language',
        'implementation': 'LanguageSpecification',
        'negate': negate, 'required': required,
        'fields': [{'name': 'value', 'value': 11},       # Russian
                   {'name': 'exceptLanguage', 'value': False}],
    }


class Arr:
    API = {'sonarr': '/api/v3', 'radarr': '/api/v3', 'lidarr': '/api/v1',
           'prowlarr': '/api/v1'}

    def __init__(self, name, base_url, key):
        self.name = name
        self.base = base_url.rstrip('/')
        self.key = key

    def call(self, path, method='GET', data=None, params=None):
        url = '%s%s/%s' % (self.base, self.API[self.name], path.lstrip('/'))
        r = requests.request(method, url, json=data, params=params,
                             headers={'X-Api-Key': self.key},
                             timeout={'POST': 60, 'PUT': 60}.get(method, 30))
        if r.status_code >= 400:
            raise ArrError('%s %s %s -> HTTP %s: %s' % (self.name, method, path,
                                                        r.status_code, r.text[:200]))
        if r.status_code == 204 or not r.content:
            return None
        return r.json()

    # -- custom formats ---------------------------------------------------

    def ensure_custom_format(self, name, specifications):
        for cf in self.call('customformat') or []:
            if cf.get('name') == name:
                cf['specifications'] = specifications
                self.call('customformat/%d' % cf['id'], 'PUT', cf)
                return cf['id']
        return self.call('customformat', 'POST', {
            'name': name, 'includeCustomFormatWhenRenaming': False,
            'specifications': specifications})['id']

    # -- quality profiles --------------------------------------------------

    def quality_schema(self):
        return self.call('qualityprofile/schema')

    def ensure_quality_profile(self, name, allowed_resolutions, format_scores,
                               min_format_score=100, upgrade_allowed=False):
        """Schema-driven profile over nested quality groups.

        - allowed flags set by quality resolution (no SD/480p/576p/720p);
        - traversal preserves schema order; cutoff is the LAST allowed
          top-level item's id (leaf quality id or group id), not a numeric max;
        - upgradeAllowed False: only the panel's daily Pareto-safe pass upgrades.
        """
        schema = self.quality_schema()
        definitions = {d['quality']['id']: d['quality']
                       for d in self.call('qualitydefinition') or []}

        def select(item):
            q = item.get('quality')
            if q:
                item['allowed'] = quality_resolution(q, definitions) in allowed_resolutions
            else:
                children = item.get('items') or []
                selected = [select(child) for child in children]
                item['allowed'] = bool(selected) and all(selected)
            return item['allowed']

        cutoff = 0
        for item in schema.get('items') or []:
            if select(item):
                cutoff = item['quality']['id'] if item.get('quality') else item['id']
        if not cutoff:
            raise ArrError('quality schema has no qualities in %s' % (allowed_resolutions,))
        formats = self.call('customformat') or []
        schema['formatItems'] = [
            {'format': f['id'], 'name': f['name'], 'score': format_scores.get(f['name'], 0)}
            for f in formats
        ]
        schema['name'] = name
        schema['cutoff'] = cutoff
        schema['upgradeAllowed'] = upgrade_allowed
        if 'minFormatScore' in schema:
            schema['minFormatScore'] = min_format_score
        if 'cutoffFormatScore' in schema:
            schema['cutoffFormatScore'] = min_format_score
        if 'minUpgradeFormatScore' in schema:
            schema['minUpgradeFormatScore'] = min_format_score
        existing = next((p for p in self.call('qualityprofile')
                         if p.get('name') == name), None)
        if existing:
            schema['id'] = existing['id']
            return self.call('qualityprofile/%d' % existing['id'], 'PUT', schema)['id']
        return self.call('qualityprofile', 'POST', schema)['id']

    # -- root folders / clients ---------------------------------------------

    def ensure_root_folder(self, path, name=None, defaults=None, fill_missing=False):
        """Create the root folder; on an existing one only fill UNSET defaults.

        Operator-chosen defaults on an existing folder are never overwritten
        (fill_missing only adds values that are empty/None).
        """
        existing = next((rf for rf in self.call('rootfolder') or []
                         if rf.get('path') == path), None)
        if existing:
            if not (defaults and fill_missing):
                return existing['id']
            payload = {key: value for key, value in defaults.items()
                       if value and not existing.get(key)}
            if not payload:
                return existing['id']
            payload['id'] = existing['id']
            return self.call('rootfolder/%d' % existing['id'], 'PUT', payload)['id']
        payload = {'path': path}
        if name:
            payload['name'] = name
        if defaults:
            payload.update(defaults)
        return self.call('rootfolder', 'POST', payload)['id']

    def ensure_transmission_client(self, name, category_field, directory_field,
                                   category, directory, user='', password=''):
        """Transmission client from the live schema (lowercase field names).

        Only the managed client `name` is created/updated; a user client named
        'Transmission' (or anything else) stays alone. A foreign client that
        carries our managed name is a conflict, never silently overwritten.
        """
        if category and directory:
            raise ArrError('Transmission Category and Directory are mutually exclusive')
        schemas = self.call('downloadclient/schema')
        template = next(s for s in schemas if s['implementation'] == 'Transmission')
        values = {'host': CONTAINER_HOST, 'port': 9091,
                  category_field: category, directory_field: directory}
        if user or password:
            values['username'] = user
            values['password'] = password
        _set_fields(template.get('fields'), values)
        template.update({'name': name, 'enable': True, 'protocol': 'torrent',
                         'removeCompletedDownloads': False,
                         'removeFailedDownloads': False})
        existing = next((c for c in self.call('downloadclient')
                         if c.get('name') == name), None)
        if existing and existing.get('implementation') != 'Transmission':
            raise ArrError(
                '%s already has a download client named %r implemented as %r; '
                'bootstrap will not overwrite it' %
                (self.name, name, existing.get('implementation')))
        if existing:
            template['id'] = existing['id']
            return self.call('downloadclient/%d' % existing['id'], 'PUT', template)['id']
        return self.call('downloadclient', 'POST', template)['id']

    def ensure_remote_path_mapping(self, host, remote_path, local_path):
        """Create the mapping only when host is unmapped; conflicting mappings fail."""
        existing = [m for m in self.call('remotepathmapping') or []
                    if m.get('host') == host]
        for m in existing:
            if m.get('remotePath') == remote_path and m.get('localPath') == local_path:
                return m['id']
        if existing:
            raise ArrError(
                '%s already has a remote path mapping for host %r with different '
                'paths (%s -> %s); resolve it manually, bootstrap will not overwrite'
                % (self.name, host, existing[0].get('remotePath'), existing[0].get('localPath')))
        return self.call('remotepathmapping', 'POST',
                         {'host': host, 'remotePath': remote_path,
                          'localPath': local_path})['id']

    # -- prowlarr applications / sonarr import list --------------------------

    def ensure_prowlarr_application(self, implementation, name, app_base_url,
                                    app_api_key, prowlarr_internal_url):
        schemas = self.call('applications/schema')
        template = next(s for s in schemas if s['implementation'] == implementation)
        _set_fields(template.get('fields'), {
            'baseUrl': app_base_url, 'prowlarrUrl': prowlarr_internal_url,
            'apiKey': app_api_key})
        template.update({'name': name, 'syncLevel': 'fullSync',
                         'tags': []})
        existing = next((a for a in self.call('applications')
                         if a.get('name') == name), None)
        if existing:
            template['id'] = existing['id']
            return self.call('applications/%d' % existing['id'], 'PUT', template)['id']
        return self.call('applications', 'POST', template)['id']

    def ensure_custom_import_list(self, name, list_url, quality_profile_id,
                                  root_folder_path=None):
        """Sonarr CustomImport ("Custom List") pointing at the MyShows feed.

        Real schema: implementation CustomImport, configContract CustomSettings,
        single field baseUrl ("List URL"). FUTURE monitoring only (verified
        against a live configured instance: shouldMonitor=future,
        monitorNewItems=all) — never a full backlog; searchForMissingEpisodes
        stays False, new series land in /tv with season folders.
        """
        schemas = self.call('importlist/schema')
        template = next(s for s in schemas
                        if s.get('implementation') == 'CustomImport')
        _set_fields(template.get('fields'), {'baseUrl': list_url})
        template.update({
            'name': name, 'enabled': True, 'enableAutomaticAdd': True,
            'searchForMissingEpisodes': False,
            'shouldMonitor': 'future', 'monitorNewItems': 'all',
            'seasonFolder': True,
            'qualityProfileId': quality_profile_id, 'tags': [],
        })
        if root_folder_path:
            template['rootFolderPath'] = root_folder_path
        if 'seriesType' in template:
            template['seriesType'] = 'standard'
        existing = next((l for l in self.call('importlist')
                         if l.get('name') == name), None)
        if existing:
            template['id'] = existing['id']
            return self.call('importlist/%d' % existing['id'], 'PUT', template)['id']
        return self.call('importlist', 'POST', template)['id']

    # -- lidarr default profiles -------------------------------------------

    def ensure_default_profile_id(self, kind, operator_id=None):
        """Resolve a default profile id for a Lidarr root folder (API v1).

        Schema objects (`qualityprofile/schema`, `metadataprofile/schema`)
        carry NO id, so one is never invented: an operator-configured id
        (config_root/lidarr.json quality_profile_id / metadata_profile_id)
        is validated against existing profiles, otherwise the first valid
        existing profile is used; only when no profile exists at all is a
        default created from the actual schema.
        """
        profiles = self.call(kind) or []
        if operator_id:
            if not any(p.get('id') == operator_id for p in profiles):
                raise ArrError('%s has no %s profile with id %r'
                               % (self.name, kind, operator_id))
            return operator_id
        for profile in profiles:
            if profile.get('id') is not None:
                return profile['id']
        schema = self.call(kind + '/schema')
        schema['name'] = 'Dubs Music (default)'
        return self.call(kind, 'POST', schema)['id']

    # -- prowlarr indexer profile -------------------------------------------

    def ensure_indexer_profile(self, name=PROWLARR_INDEXER_PROFILE,
                               minimum_seeders=MINIMUM_SEEDERS):
        """Managed Prowlarr indexer (sync) profile with minimumSeeders.

        API v1 /appprofile; indexers reference its id as appProfileId.
        Real shape: {name, enableRss, enableAutomaticSearch,
        enableInteractiveSearch, minimumSeeders, id}. minimumSeeders lives
        here (used for app sync / adding trackers), NOT in quality profiles.
        Only the managed `name` profile is created/updated; the user's
        'Standard' profile is never touched. Returns the profile id.
        """
        payload = {'name': name, 'enableRss': True, 'enableAutomaticSearch': True,
                   'enableInteractiveSearch': True, 'minimumSeeders': minimum_seeders}
        existing = next((p for p in self.call('appprofile') or []
                         if p.get('name') == name), None)
        if existing:
            payload['id'] = existing['id']
            return self.call('appprofile/%d' % existing['id'], 'PUT',
                             payload)['id']
        return self.call('appprofile', 'POST', payload)['id']

    # -- managed studio clone refresh -----------------------------------------

    def refresh_profile_clones(self, base_profile_id, base_scores, veto_format):
        """Refresh base scores in panel clones of a Dubs-owned base profile.

        Clones are named 'Сериал / база <baseId> / <studio> / только|приоритет'
        (src/control.py chosen_profile). Only clones whose embedded base id is
        the Dubs-owned `base_profile_id` are updated; the operator's explicit
        chosen Выбор сериала bonus (+CLONE_BONUS) and strict/fallback mode
        (minFormatScore, cutoff, upgradeAllowed) are preserved — only managed
        base-format scores (Озвучка NN, RU evidence, veto) are brought back in
        line when the studio order changed. User formats keep their scores.
        Returns the list of refreshed clone names.
        """
        refreshed = []
        prefix = CLONE_PREFIX + '%d / ' % base_profile_id
        for profile in self.call('qualityprofile') or []:
            if not str(profile.get('name', '')).startswith(prefix):
                continue
            items = profile.get('formatItems') or []
            selected = next((fi.get('format') for fi in items
                             if fi.get('name', '').startswith(SERIES_FORMAT_PREFIX)
                             and (fi.get('score') or 0) >= CLONE_BONUS), None)
            for fi in items:
                fname = fi.get('name', '')
                if fname.startswith(SERIES_FORMAT_PREFIX):
                    fi['score'] = CLONE_BONUS if fi.get('format') == selected else 0
                elif _managed_base_format(fname, veto_format):
                    fi['score'] = base_scores.get(fname, 0)
            updated = dict(profile)
            updated['formatItems'] = items
            self.call('qualityprofile/%d' % profile['id'], 'PUT', updated)
            refreshed.append(profile['name'])
        return refreshed


def quality_resolution(quality, definitions):
    """Resolve API quality IDs via qualitydefinition; unknown/raw fail closed."""
    known = definitions.get(quality.get('id')) or {}
    if (not known.get('id') or known.get('source') == 'televisionRaw'
            or known.get('modifier') in ('rawhd', 'brdisk')
            or known.get('name', '').casefold() in ('raw-hd', 'br-disk')):
        return 0
    return known.get('resolution', 0)


def _resolution(quality_name):
    m = re.search(r'(2160|1080|720|576|480)', quality_name or '')
    return int(m.group(1)) if m else 0


def _set_fields(fields, values):
    for f in fields or []:
        if f.get('name') in values:
            f['value'] = values[f['name']]


def _managed_base_format(name, veto_format):
    """True for base formats whose scores bootstrap owns in managed profiles."""
    return (name == RU_EVIDENCE_TITLE or name == RU_EVIDENCE_LANGUAGE
            or name == veto_format or name.startswith('Озвучка '))


def _ru_format_set(studios, veto_name, sonarr_arr):
    """Create the RU evidence/veto + per-studio + per-series format families.

    Scores (on Dubs profiles):
      RU evidence title  +100   RU evidence language +100
      Озвучка NN — <name>       2**(12-index)*100 (named studios, best first)
      Другая русская озвучка    0 (fallback)
      Выбор сериала — <name>    0 in base; chosen_profile grants the
                                selected one +1000000 (strict min 1000001)
      veto (no RU at all)       -10000000
    """
    regex = ru_evidence_regex(studios)
    scores = {}
    sonarr_arr.ensure_custom_format(RU_EVIDENCE_TITLE, [ru_spec(regex)])
    scores[RU_EVIDENCE_TITLE] = 100
    sonarr_arr.ensure_custom_format(RU_EVIDENCE_LANGUAGE, [language_spec()])
    scores[RU_EVIDENCE_LANGUAGE] = 100
    sonarr_arr.ensure_custom_format(veto_name, [
        ru_spec(regex, negate=True, required=True),
        language_spec(negate=True, required=True)])
    scores[veto_name] = -10000000
    for index, studio in enumerate(studios[:-1]):
        name = 'Озвучка %02d — %s' % (index + 1, studio['name'])
        sonarr_arr.ensure_custom_format(name, [ru_spec(studio['pattern'])])
        scores[name] = 2 ** (12 - index) * 100
    fallback = 'Озвучка %02d — %s' % (len(studios), studios[-1]['name'])
    sonarr_arr.ensure_custom_format(fallback, [ru_spec(studios[-1]['pattern'])])
    scores[fallback] = 0
    for studio in studios:
        name = SERIES_FORMAT_PREFIX + studio['name']
        sonarr_arr.ensure_custom_format(name, [ru_spec(studio['pattern'])])
        scores[name] = 0
    return scores


def _wire_app(arr, deployment, conn, category_field, directory_field, category,
              directory, root_path, root_defaults=None, root_name=None,
              prowlarr=None, app_implementation=None,
              client_name='Dubs Transmission'):
    arr.ensure_transmission_client(client_name, category_field, directory_field,
                                   category, directory,
                                   conn['transmission'].get('user') or '',
                                   conn['transmission'].get('password') or '')
    arr.ensure_remote_path_mapping(CONTAINER_HOST, '/downloads/', '/torrents/')
    arr.ensure_root_folder(root_path, name=root_name, defaults=root_defaults,
                           fill_missing=bool(root_defaults))
    if prowlarr is not None and app_implementation:
        prowlarr.ensure_prowlarr_application(
            app_implementation, '%s (Dubs)' % app_implementation,
            deployment.internal[arr.name], conn[arr.name]['key'],
            deployment.internal['prowlarr'])


def bootstrap(deployment, studios, veto_format, feed_url, log=lambda m: None):
    """Wire integrations; returns a report dict. Managed objects only."""
    conn = deployment.connections()
    for required in ('sonarr', 'prowlarr', 'transmission'):
        cfg = conn.get(required) or {}
        if not cfg.get('base_url'):
            raise ArrError('%s connection not configured (config_root/%s.json)'
                           % (required, required))
        if required != 'transmission' and not cfg.get('key'):
            raise ArrError('%s API key missing (config_root/%s.json)'
                           % (required, required))
    report = {}
    sonarr = Arr('sonarr', conn['sonarr']['base_url'], conn['sonarr']['key'])
    prowlarr = Arr('prowlarr', conn['prowlarr']['base_url'], conn['prowlarr']['key'])

    # 1. RU / voice custom formats: the SAME 14-studio preference family for
    # Sonarr AND Radarr (identical scores), not a RU-only mirror.
    scores = _ru_format_set(studios, veto_format, sonarr)
    report['formats'] = len(scores)

    # 2. Dubs RU profiles: 2160+1080 (default MyShows profile) and 1080-only.
    allowed_all = {1080, 2160}
    allowed_1080 = {1080}
    p2160 = sonarr.ensure_quality_profile('Dubs RU 2160p', allowed_all, scores)
    p1080 = sonarr.ensure_quality_profile('Dubs RU 1080p', allowed_1080, scores)
    report['profiles'] = {'Dubs RU 2160p': p2160, 'Dubs RU 1080p': p1080}
    report['refreshed_studio_clones'] = (
        sonarr.refresh_profile_clones(p2160, scores, veto_format)
        + sonarr.refresh_profile_clones(p1080, scores, veto_format))

    # 3. Explicit directories isolate app queues as well as download destinations.
    # Transmission rejects Category + Directory together; clear schema category
    # defaults explicitly rather than relying on their current default values.
    # Clients/mappings/root folders + Prowlarr applications.
    _wire_app(sonarr, deployment, conn, 'tvCategory', 'tvDirectory',
              '', '/downloads/complete/tv', '/tv', prowlarr=prowlarr,
              app_implementation='Sonarr')
    report['sonarr'] = 'wired'
    if 'radarr' in conn and conn['radarr'].get('base_url'):
        radarr = Arr('radarr', conn['radarr']['base_url'], conn['radarr']['key'])
        radarr_scores = _ru_format_set(studios, veto_format, radarr)
        rp2160 = radarr.ensure_quality_profile('Dubs RU 2160p', allowed_all,
                                               radarr_scores)
        rp1080 = radarr.ensure_quality_profile('Dubs RU 1080p', allowed_1080,
                                               radarr_scores)
        report['refreshed_studio_clones'] += (
            radarr.refresh_profile_clones(rp2160, radarr_scores, veto_format)
            + radarr.refresh_profile_clones(rp1080, radarr_scores, veto_format))
        _wire_app(radarr, deployment, conn, 'movieCategory', 'movieDirectory',
                  '', '/downloads/complete/movies', '/movies',
                  prowlarr=prowlarr, app_implementation='Radarr')
        report['radarr'] = 'wired'
    if 'lidarr' in conn and conn['lidarr'].get('base_url'):
        lidarr = Arr('lidarr', conn['lidarr']['base_url'], conn['lidarr']['key'])
        # Music: no TV voice/language restriction. Root folder defaults from
        # EXISTING profiles (schema objects carry no id — never invented):
        # operator-configurable ids win, else first valid existing profile.
        cfg = conn['lidarr']
        defaults = {
            'defaultQualityProfileId': lidarr.ensure_default_profile_id(
                'qualityprofile', cfg.get('quality_profile_id')),
            'defaultMetadataProfileId': lidarr.ensure_default_profile_id(
                'metadataprofile', cfg.get('metadata_profile_id')),
        }
        if cfg.get('default_monitor_option'):
            defaults['defaultMonitorOption'] = cfg['default_monitor_option']
        if cfg.get('default_new_item_monitor_option'):
            defaults['defaultNewItemMonitorOption'] = \
                cfg['default_new_item_monitor_option']
        if cfg.get('default_tags'):
            defaults['defaultTags'] = list(cfg['default_tags'])
        _wire_app(lidarr, deployment, conn, 'musicCategory', 'musicDirectory',
                  '', '/downloads/complete/music', '/music',
                  root_defaults=defaults, root_name='Dubs Music',
                  prowlarr=prowlarr, app_implementation='Lidarr')
        report['lidarr'] = 'wired'

    # 4. Managed Prowlarr indexer (sync) profile: minimumSeeders lives here,
    # never in quality profiles. Its id is for tracker setup / Jackett sync.
    report['prowlarr_indexer_profile'] = {
        'name': PROWLARR_INDEXER_PROFILE,
        'id': prowlarr.ensure_indexer_profile(),
        'minimumSeeders': MINIMUM_SEEDERS,
    }

    # 5. CustomImport fetches the supplied URL directly; feed_url is the
    # server base (also used for /health), while the JSON list lives at /series.
    # FUTURE monitoring only, never backlog.
    report['import_list'] = sonarr.ensure_custom_import_list(
        'MyShows watching (Dubs feed)', feed_url.rstrip('/') + '/series',
        p2160, root_folder_path='/tv')
    return report


def human_steps(report, deployment):
    """Pending onboarding the installer cannot do for the operator."""
    return [
        'Prowlarr: добавьте трекеры вручную (учётные данные оператора bootstrap '
        'не хранит) — приложения Sonarr/Radarr/Lidarr (Dubs) уже подключены; '
        'для трекеров используйте управляемый профиль «%s» (минимум сидов: %s, '
        'id из отчёта bootstrap: %s), профиль Standard не трогайте.'
        % (PROWLARR_INDEXER_PROFILE, MINIMUM_SEEDERS,
           (report.get('prowlarr_indexer_profile') or {}).get('id')),
        'Seerr (%s): пройдите веб-мастер настройки и подключите Jellyfin.'
        % deployment.published['seerr'],
        'Jellyfin (%s): создайте владельца, затем отдельного пользователя ТОЛЬКО '
        'для метаданных; его токен и userId -> config_root/jellyfin.json '
        '(админ-токен не используйте).' % deployment.published['jellyfin'],
        'security.json: заполните owner_names (логины владельцев в Jellyfin) и '
        'owner_ids (их Jellyfin UserId) — без них вход на панель закрыт; '
        'allowed_origins/trusted_proxies/public_hosts — под ваш reverse proxy.',
        'config.json: login (MyShows), public_url, jellyfin_url.',
        'Проверка: `python3 -m installer check`.',
    ]
