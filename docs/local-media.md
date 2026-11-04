# Local file metadata

`jellyfin_links.get_library(m)` returns a mapping keyed by `tvdbId:season:episode`.
Each value contains `jellyfinUrl` (a credential-free HTTPS details link or null)
and `localMedia` with `status`, `quality`, `audioTracks`, `verifiedAt`, and
`source`. `get_links(m)` retains the URL-only player contract.

Quality describes actual video dimensions, codec and dynamic range. Embedded
artwork never supplies dimensions. A Sonarr release quality name does not
establish the actual resolution. Audio tracks retain their separate language,
explicit title, codec, channel count and default flag. Only an explicit track
title matching one conservative configured studio pattern establishes a studio;
unknown studio tags remain visible as titles. Profile preferences, release
names, release groups and history do not establish audio provenance.

Sonarr fallback uses the nested `watching()[i].series` records and requires an
exact episode with `hasFile` and a registered `episodeFileId`. Summary audio
codec/channels apply only when exactly one stream exists. Multiple streams keep
unknown per-track codec/channels/default fields null. Language positions are
used only when the language count matches the stream count.

Fresh API facts are `verified`; absent or ambiguous facts are `unknown`.
Previously verified snapshots become `stale` when unavailable, removed, or
conflicting, preserving their original verification timestamp. Repeated API
failures retain the last verified cache. Jellyfin failure does not prevent
independent Sonarr fallback. Conflicting source versions are never merged.
Sonarr removal or changed registered file identity prevents old Jellyfin streams
from continuing to represent the new file: current Sonarr facts take priority,
otherwise the previous snapshot is stale. Basename/size and prior file ID are
internal comparisons; where neither source offers comparable identity, the
first successful round establishes an ID baseline. An API snapshot confirms
registration and metadata, not independent filesystem availability.

Metadata output excludes source paths, API credentials and raw API URLs.
Sensitive-looking display tags are suppressed. The cache contains internal
basename/size/file-ID fingerprints and must remain inside worker state. It is
atomically replaced with mode 0640, explicitly set even under umask 0077.
Jellyfin episode reads use pages of 1000, at most ten pages; the series query
has a 5000-item bound. Sonarr uses two requests per watched series. Tracks are
bounded to 32 per snapshot, display strings to 200 characters, and cached
entries to 50000. Libraries beyond these limits may remain incomplete/stale.

Offline verification:

```sh
python3 -m unittest discover -s tests -p 'test_local_media.py'
python3 -m unittest discover -s tests -p 'test_smoke_entrypoint.py'
```

The smoke entrypoint regression stubs Docker setup and all test bodies. Actual
integration requires a disposable Docker host and explicitly running:

```sh
DUBS_SMOKE=1 python3 tests/integration_smoke.py
```

Without the flag, the module exposes no integration tests and direct execution
exits 2. With the flag, its runner requires selection and execution of all six
methods. A zero-test run cannot report success.
