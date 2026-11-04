# Notifications

The worker maintains a bounded durable SQLite event journal in WORKER_STATE.
The web process opens this database read-only; account settings and hashed,
revocable device credentials live in STATE. Worker/web Unix users must share
the deployment group: journal mode 0640, worker directory group-readable and
traversable. Web must have no write permission to the worker directory. The
worker initializes the journal independently of MyShows authentication. Until
then device registration returns 503; retry after the worker starts.

Session APIs:

- GET/POST `/api/notifications/settings`: account enabled/type/privacy settings.
- GET/POST `/api/notifications/devices`: redacted list or register device.
- POST `/api/notifications/revoke`: revoke an owned device.
- GET `/api/notifications/history?before=ID&limit=1..100`: newest-first history.
- GET `/api/notifications/events?after=ID&limit=1..100`: ascending feed.

POST forms use exact contract fields, session CSRF and exact Origin. Registration
returns a single opaque token; only its hash is stored. Bearer credentials are
accepted only by the GET events route. Tokens expire after 180 days; at most eight
active registrations per account are retained. Registration is rate limited.

`instanceId` is an opaque 32-character hexadecimal string owned by the worker
journal. Clients must never parse it as an integer. A recreated journal changes
this epoch. A cursor above the current journal tail returns `reset:true` and the
current tail; clients should persist the returned cursor without delivering old
notifications. Device registration starts at the current tail, avoiding backlog.

Account settings are separate from each device's local opt-in and OS permission.
Default lock-screen presentation is generic; show titles only when `showTitles`
is explicitly enabled. The feed contains only bounded sanitized display fields
and numeric navigation IDs, with no media URLs or filesystem paths.

The observer issues read-only Sonarr and Transmission requests for tracked
MyShows episodes. Initial queue and show baselines stay silent. Transfer start
requires observed selected-file bytes; completion requires all selected bytes;
library readiness requires a changed registered nonempty Sonarr file. Queue
removal or an import command alone never establishes readiness. Pending imports
survive worker restarts. Panel-managed hashes belong to panel hooks to prevent
duplicate events. A later same-hash automatic grab has a new generation.

Newly aired episode baselines are persisted separately from retained history.
Daily checks run once per local date at the configured IANA timezone and hour
(`daily_upgrade_hour`, default 18). Dry runs emit no events. Outcomes distinguish
improvements, none, and source/search errors.

No APNs or external push service is required. Native clients deliver local OS
notifications through foreground polling and opportunistic background work.
iOS background refresh is not guaranteed, especially after force-quit; Android
periodic jobs may be delayed by the OS. Browser OS notifications require explicit
permission while the page is open. Notification generation never initiates media
actions. Navigation uses validated episode IDs in the panel.

## Adopted deployments: Jellyfin login transport

Before deploying to a web unit that hides the private `jellyfin.json`, set
`jellyfin_api_url` in the web-readable `config.json` to the operator's actual
Jellyfin API base URL (including any reverse-proxy base path). It is a
transport address only: never put a username, password, token, query string,
or fragment in this field. HTTP and HTTPS are supported; HTTPS uses normal
certificate verification. Login and temporary-token logout do not follow redirects,
so configure the final endpoint directly.

The generated default is empty. Empty or omitted preserves portable installs by
using the existing `jellyfin.json` connection base, including remote servers;
it does not override that connection with a localhost address. An explicit
invalid transport URL fails closed without falling back to the private file.
The separate `jellyfin_url` setting remains the public playback address.

Keep the web service's existing `InaccessiblePaths` protection for the private
Jellyfin metadata identity. No additional web access to metadata credentials is
needed when `jellyfin_api_url` is set. Existing `config.json` files are preserved
by normal config generation; add this adoption setting before restarting the web
service. Operator-supplied addresses stay outside the repository.
