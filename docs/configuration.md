# Конфигурация

Корень конфигурации — `config_root` (по умолчанию `/etc/series-control`), права 0750,
файлы 0640. Создаётся `python3 -m installer configure` (скелеты, никогда не
перезаписывает без `--force`). Все ключи и секреты живут **только здесь** — вне git,
вне чата, вне репозитория.

## Файлы в config_root

| Файл | Назначение |
|---|---|
| `config.json` | общие настройки панели/воркера |
| `security.json` | ключ подписи, владельцы Jellyfin, доверенные прокси/origin |
| `studios.json` | список студий озвучки (копия из репозитория, 0644) |
| `sonarr.json` `prowlarr.json` `radarr.json` `lidarr.json` `jackett.json` | `{"base_url", "key"}` |
| `transmission.json` | `{"base_url", "user", "password"}` |
| `jellyfin.json` | `{"base_url", "token", "userId"}` — read-only пользователь метаданных (см. ниже) |
| `seerr.json` | скелет (Seerr настраивается своим мастером) |

## config.json (скелет и значения по умолчанию)

```json
{
  "login": "",                        // ник вашего аккаунта MyShows (ОДИН аккаунт на инстанс панели)
  "proxy": "",                        // необязательный socks5 host:port; пусто = напрямую
  "timezone": "Europe/Moscow",        // часовой пояс ежедневного прохода обновления
  "owner_label": "",
  "public_url": "",                   // https URL панели за reverse proxy
  "jellyfin_url": "",                 // https URL публичного Jellyfin
  "cleanup_enable": false,            // очистка просмотренных: ТОЛЬКО явный opt-in
  "daily_upgrade_hour": 18,           // час ежедневного прохода (локальное время)
  "veto_format": "ОБЯЗАТЕЛЬНО RU — запрет без русского",
  "paths": {
    "shows_host": "/mnt/shows",
    "torrent_host": "/mnt/torrent",
    "sonarr_tv": "/tv",
    "sonarr_torrents": "/torrents",
    "downloads": "/downloads"
  },
  "panel": { "host": "127.0.0.1", "http": 9192, "https": 9193 },
  "indexer_endpoints": [],            // доп. доверенные endpoints загрузок торрентов
  "mobile": { "dir": "", "artifacts": [] }
}
```

Примечания:

- **Один аккаунт MyShows на инстанс панели**: `login` задаёт единственный
  аккаунт, чей токен подключается через панель (Настройки); state-файл
  `myshows-auth.json` хранит ровно один токен.
- `storage_sentinel` в скелет не пишется — это рантайм-умолчание (`true`):
  воркер требует «вооружённого» sentinel перед мутациями файлов; отключать его
  имеет смысл только принятым (не-compose) схемам — само монтирование
  проверяется в любом случае.
- `configure` при заполненном `compose/.env` засеивает `paths.movies_host` и
  `paths.music_host` из `MEDIA_ROOT`, а `paths.torrent_host`/`shows_host` — из
  `MEDIA_ROOT/torrents` и `MEDIA_ROOT/shows`.
- TLS-сертификаты панели по умолчанию ищутся в `config_root/server.crt` и
  `server.key` (переопределяется ключами `panel.cert` / `panel.key`).
- `indexer_endpoints` — единственный способ добавить доверенные хосты/порты для
  скачивания `.torrent` (например, удалённый Prowlarr): каждый элемент —
  `{"host", "ports", "path_prefixes", "scheme", "tls_verify"}`. Loopback для
  портов 9696/8989/9117 доверен всегда, остальное — только явным списком.
- `mobile.artifacts` — белый список имён файлов из каталога `mobile.dir`
  (по умолчанию `/opt/dubs-mobile/releases`); панель раздаёт только те, что
  присутствуют на диске. См. [mobile.md](mobile.md).

## security.json

Создаётся с автосгенерированным `signing_key`. Заполните вручную:

```json
{
  "signing_key": "<hex, генерируется configure>",
  "owner_names": ["ИмяВладельцаВJellyfin"],
  "owner_ids": ["<Jellyfin UserId владельца>"],
  "trusted_proxies": ["127.0.0.1/32"],
  "allowed_networks": ["127.0.0.1/32", "::1/128"],
  "allowed_origins": ["https://panel.example.com"],
  "public_hosts": ["panel.example.com"]
}
```

- **Вход на панель закрыт**, пока `owner_names`/`owner_ids` не совпадают с реальным
  владельцем Jellyfin (проверяется и его флаг администратора).
- `owner_ids` — **GUID реального пользователя из вашего Jellyfin**, не
  захардкоженный id: возьмите его из дашборда Jellyfin (Панель → Пользователи →
  нужный пользователь, идентификатор в адресе страницы) или
  `GET /Users` с админ-ключом вашего собственного инстанса.
- Вход на панель (Jellyfin-владелец) и подключение MyShows (Настройки) —
  **разные механизмы**: первый сверяет владельца с Jellyfin, второй сохраняет
  токен вашего MyShows-аккаунта. Секретов аккаунтов в репозитории нет и быть
  не должно.
- `trusted_proxies` — адреса reverse proxy, которым разрешено выставлять
  `X-Forwarded-*`; без них реальный IP клиента не определяется.
- `allowed_origins`/`public_hosts` — канонические Origin/Host за прокси; без них
  вход из браузера через прокси не пройдёт. Никаких «доверять всем».

## jellyfin.json — read-only токен метаданных

Панель использует **отдельного пользователя Jellyfin с правами только на
метаданные** (создайте его в Jellyfin вручную, без прав администратора и без
доступа к управлению). Его User AccessToken — НЕ глобальный админ-API-ключ;
кладите в `jellyfin.json` именно пользовательский токен.

Пароль не должен попадать в командную строку или логи — получите токен
интерактивно (getpass) и запишите файл приватным с рождения:

```bash
sudo python3 - <<'PY'
import getpass, json, os, requests
base = "http://127.0.0.1:8096"   # ваш jellyfin.json base_url
user = input("Jellyfin metadata username: ")
pw = getpass.getpass("Jellyfin password: ")
r = requests.post(base + "/Users/AuthenticateByName", timeout=10,
                  headers={"Authorization": 'MediaBrowser Client="Dubs setup", '
                                             'Device="setup", DeviceId="dubs-token", Version="1.0"'},
                  json={"Username": user, "Pw": pw})
r.raise_for_status()
result = r.json()
path = "/etc/series-control/jellyfin.json"    # ваш config_root
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump({"base_url": base, "token": result["AccessToken"],
               "userId": result["User"]["Id"]}, f, indent=2)
os.chmod(path, 0o600)
print("written", path)
PY
sudo python3 -m installer configure   # выровняет владельца/режим по политике (root:<группа панели>, 0640)
```

`userId` берётся из ответа реального Jellyfin (`User.Id`) — никаких
захардкоженных GUID. Отдельные CLI-команды для этого у инсталлятора нет —
только панель и этот пример.

## studios.json

14 записей: 13 именованных студий + последняя запись «Другая русская озвучка» —
выступает **catch-all fallback** (не является доказательством русской дорожки).
Порядок задаёт приоритет озвучек. Предпочтения панель хранит по **имени** студии,
поэтому перестановка записей не ломает выбранные политики.

## Переменные окружения рантайма

Все опциональны — юниты systemd выставляют их из конфига инсталляции:

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `SERIES_CONTROL_CONFIG_ROOT` | `/etc/series-control` | каталог конфигов |
| `SERIES_CONTROL_STATE_ROOT` | `/var/lib/series-control` | состояние веб-роли |
| `SERIES_CONTROL_WORKER_ROOT` | `/var/lib/series-control-worker` | состояние воркера |
| `SERIES_CONTROL_UI_DIR` | `/etc/series-control-ui` | код панели (`render.py`, `ui.js`, `style.css`) |
| `SERIES_CONTROL_STUDIOS` | `config_root/studios.json` | путь к студиям |
| `SERIES_CONTROL_FEED_STATE` | `/var/lib/myshows-sync` | состояние фида MyShows |
| `SERIES_CONTROL_COVERS` | `/var/cache/series-control-covers` | кэш постеров |
| `SERIES_CONTROL_TIMEZONE` | `Europe/Moscow` | резервный пояс |
| `SERIES_CONTROL_ROLE` | `web` | роль `control.py`: `web`/`worker`/`operations` |
| `DUBS_SRC` | репозиторий | путь к `src/` для автоматизаций |
| `DUBS_FEED_PORT` | `9191` | порт фида MyShows |
| `DUBS_FEED_BIND` | `127.0.0.1` | бинды фида через запятую; **wildcard запрещены** |

## Инсталлятор и `.env`

Глобальные опции (до подкоманды): `--env-file`, `--config-root`, `--state-root`.
Основные значения инсталлятор берёт из `compose/.env`:
`MEDIA_ROOT`, `DUBS_DATA_ROOT` (обязательные абсолютные пути), `PUID`, `PGID`,
`DUBS_INSTALL_ROOT` (по умолчанию `/opt/dubs`), `PANEL_USER`/`PANEL_GROUP`
(по умолчанию `dubs`), `TORRENT_HOST`/`SHOWS_HOST`, `DUBS_FEED_PORT`, `DUBS_FEED_BIND`,
а также `DUBS_INTERNAL_<СЕРВИС>` (например `DUBS_INTERNAL_SONARR`) — явный
маршрутизируемый адрес принятого удалённого сервиса вместо контейнерного имени;
`bootstrap` пропишет именно его в управляемые объекты.

Сервисы и порты для `base_url`-скелетов: Sonarr 8989, Radarr 7878, Lidarr 8686,
Jellyfin 8096, Seerr 5055, Transmission 9091, Prowlarr 9696, Jackett 9117.
Внутри compose-сети используются контейнерные имена (`http://sonarr:8989` и т.д.),
на хосте — loopback-публикации (`http://127.0.0.1:8989`); это **разные** классы
адресов, не смешивайте их в конфигах приложений.
