# Установка с нуля

Целевые системы: **Debian 12 (bookworm), Debian 13 (trixie), Ubuntu 24.04**.
Требуется root (sudo) для шагов `install`/`configure`/`bootstrap`/`check`/`update`.
Docker и Compose v2 инсталлятор доустанавливает сам, не ломая уже работающий Docker.

> **Статус.** Команды ниже — реальные подкоманды инсталлятора (`installer/cli.py`,
> сверены с `build_parser`). Объём и границы тестов описаны в README («Статус и тесты»)
> и [.github/workflows/ci.yml](../.github/workflows/ci.yml); этот документ не заявляет
> ни одного прогона сам по себе. Инсталлятор идемпотентен: команды безопасно
> повторять, он печатает оставшиеся шаги оператора.

## 0. Репозиторий

Требования: git и python3. Репозиторий приватный — клонирование только после
приглашения и авторизации (`gh auth login` или деплой-ключ):

```bash
git clone https://github.com/chelaxian/dubs-media-stack.git
cd dubs-media-stack
```

Python-зависимость одна: `requests` (стабильная ветка 2.x; `requirements.txt`).
Для Debian/Ubuntu ставьте её из пакета `python3-requests` — `install` делает это сам.
`plan`/`doctor` работают и без неё (режим деградации на stdlib).

## 1. compose/.env — ДО установки

`install` читает пути хранилища из `compose/.env`, поэтому заполните его **до**
первого `install` (абсолютные пути обязательны — относительные отклоняются):

```bash
install -m 0600 compose/.env.example compose/.env   # сразу приватный 0600
editor compose/.env
```

```ini
MEDIA_ROOT=/srv/dubs/media       # пример; выберите свой путь и используйте его же ниже
DUBS_DATA_ROOT=/srv/dubs/data    # конфиги контейнеров jellyfin/ sonarr/ ...
PUID=1000                        # владелец хранилища (id -u / id -g)
PGID=1000
TZ=Europe/Moscow
```

Для **уже работающих** сервисов на других хостах задайте здесь же маршрутизируемые
внутренние адреса: `DUBS_INTERNAL_SONARR=http://192.0.2.10:8989` (и т.п.) —
контейнерные имена `http://sonarr:8989` действуют только внутри локального compose.

## 2. Осмотр (read-only)

```bash
python3 -m installer plan --no-mutate
python3 -m installer doctor
```

- `plan` показывает: дистрибутив, Docker/Compose, класс каждого сервиса
  (`ready` / `needs-setup` / `wrong-service` / `port-occupied` / `unavailable`),
  чего бы коснулась установка. `--no-mutate` принят и подразумевается: `plan` никогда не меняет систему.
- `doctor` — то же плюс health-проверки: sentinel хранилища, права на конфиги,
  наличие юнитов, доступность фида на `127.0.0.1:9191/health`.

Существующие сервисы определяются по их API/портам и **помечаются как принятые
(external)** — локально они устанавливаться не будут. Подробности: [adopt-existing.md](adopt-existing.md).

## 3. Хранилище: сначала монтирование, потом sentinel

Порядок важен и при первой настройке, и при каждой перезагрузке: диск должен быть
смонтирован **до** контейнеров (compose-сервисы зависят от одноразового
`storage-sentinel`, который проверяет файл на `MEDIA_ROOT`).

1. Локальный каталог — создайте явно: `sudo mkdir -p /srv/dubs/media`.
   Внешний диск — смонтируйте и **проверьте**, что путь — именно нужное
   монтирование: `findmnt /srv/dubs/media`.
2. Вооружите sentinel **один раз**, вписав путь буквально — тот же, что в
   `MEDIA_ROOT`. Не используйте `$MEDIA_ROOT` в shell: переменная не установлена,
   и файл молча появится в корне `/`:

```bash
sudo install -m 0644 /dev/null /srv/dubs/media/.dubs-storage-ok
```

Sentinel обязан лежать на **намеченном** диске: если «вооружить» путь до
монтирования, файл осядет на корневой ФС и будет защищать пустое место.
Sentinel ловит типичную аварию («Docker молча создал пустой каталог на /»),
но не доказывает на 100%, что смонтирована именно та точка — проверьте
монтирование сами (`findmnt`). Подробности: [storage-iGPU.md](storage-iGPU.md).

## 4. Установка

```bash
sudo python3 -m installer install --create-storage
```

Что делает:

1. apt-зависимости: Docker + Compose v2, `python3-requests`, `python3-yaml`, `curl`.
2. Учётки изоляции: web-uid `dubs` (без оболочки, без доступа к медиа и бэкенд-ключам)
   и worker-uid `dubs-worker` (приватная группа бэкенда; медиагруппа PGID — только
   ему, единственному, на запись).
3. Копия кода в `/opt/dubs` (белый список, root:dubs, 0755/0644 — панель только читает;
   `.env` и секреты не копируются).
4. Каталоги конфигов/состояния: `/etc/series-control` (0750),
   `/var/lib/series-control`, `/var/lib/series-control-worker`,
   `/var/lib/myshows-sync`, `/var/cache/series-control-covers`.
5. `compose/.env` — создаётся из `.env.example`, если отсутствует (0600 с рождения).
6. При `--create-storage`: подкаталоги `torrents/ shows/ movies/ music/`
   (02775, PUID:PGID) — **только если sentinel уже вооружён**; без него команда
   не создаёт заглушек и печатает напоминание.
7. Каталоги конфигов контейнеров в `DUBS_DATA_ROOT` (владелец PUID:PGID).
8. Compose поднимает **только отсутствующие** основные сервисы; принятые
   (external) и уже работающие не трогаются.
9. systemd-юниты из `templates/systemd/` рендерятся в `/etc/systemd/system` и
   **включаются (enable), но не запускаются**; юнит панели не включается, пока
   owner-allowlist в `security.json` пуст; для фида добавляется drop-in с биндами
   loopback + шлюз docker-сети `dubs` (ничего публичного).

Если что-то не заполнено — `install` не догадывается, а печатает это в
«PENDING OPERATOR STEPS». Повторный запуск добирает остальное.

## 5. Конфигурация (каркас + значения оператора)

```bash
sudo python3 -m installer configure
```

Пишет скелеты в `/etc/series-control/` (существующие файлы не перезаписываются,
для перезаписи — `--force`) и проверяет их через реальный рантайм. **Скелеты — не
рабочая конфигурация и не мастер настройки.** Заполните (файлы 0640, ключи никогда
не в git):

- `config.json` — `login` (ник аккаунта MyShows; **один аккаунт на инстанс
  панели**), `public_url`, `jellyfin_url`, `timezone`, при необходимости `proxy` (socks5).
- `security.json` — `owner_names` + `owner_ids` (реальные имена и UserId владельцев
  из вашего Jellyfin; без них вход на панель закрыт и юнит панели не включается),
  `allowed_origins`, `trusted_proxies`, `public_hosts`.
- `sonarr.json`, `prowlarr.json` — API-ключи (обязательны для `bootstrap`);
  `transmission.json` — user/password; `radarr.json`/`lidarr.json` — опционально;
  `jackett.json` — только для профиля extras.
- `jellyfin.json` — `base_url` + токен и userId **read-only пользователя
  метаданных** (User AccessToken, не глобальный админ-API-ключ). Готовый
  getpass-пример получения токена — [configuration.md](configuration.md).

Детали и примеры: [configuration.md](configuration.md).

## 6. Связывание сервисов

```bash
sudo python3 -m installer bootstrap
```

Bootstrap **сам запустит остановленный фид** `dubs-myshows-feed` и проверит,
что `/health` отдаёт валидный JSON; если фид недоступен — откажется что-либо
создавать (Sonarr валидирует фид при создании импорт-листа) и напечатает, как
его проверить. `--feed-url http://host:port` переопределяет базовый адрес фида.
В Custom List записывается `http://host:port/series`: Sonarr запрашивает
указанный URL напрямую, а `/health` используется для проверки доступности.

Через schema-API приложений создаёт/обновляет **только объекты с управляемыми
именами**: профили «Dubs RU 2160p/1080p» (1080/2160, `upgradeAllowed=false` —
качество поднимает только ежедневный проход панели в 18:00), форматы
«Озвучка NN — …» по `studios.json` (13 именованных студий + fallback-запись),
RU-veto, клиент «Dubs Transmission» с отдельными каталогами загрузки
`/downloads/complete/tv`, `/downloads/complete/movies`, `/downloads/complete/music`
для Sonarr, Radarr и Lidarr соответственно. Поля Category очищены: Transmission
не допускает одновременно Category и Directory. Каталоги разделяют назначения
загрузок и очереди приложений. Сохраняются
remote-path mapping `transmission: /downloads/ → /torrents/`, приложения
«Sonarr/Radarr/Lidarr (Dubs)» в Prowlarr, профиль индексаторов «Dubs»
(minimumSeeders = 1) и импорт-лист «MyShows watching (Dubs feed)»
(только **будущие** эпизоды: `shouldMonitor=future`, `monitorNewItems=all`,
без бэклога, root folder `/tv`). Radarr получает ту же полную политику озвучки;
Lidarr работает через API v1 с музыкальными профилями — без сериальных
голосовых ограничений. Пользовательские объекты не трогаются.

Адреса внутри стека — контейнерные (`http://transmission:9091`); для принятых
удалённых сервисов задайте маршрутизируемые адреса заранее (раздел 1) —
см. [adopt-existing.md](adopt-existing.md).

## 7. Шаги оператора (инсталлятор их не делает)

Вывод `bootstrap` печатает этот список; коротко:

1. **Prowlarr** — добавьте свои трекеры вручную (учётки и CAPTCHA оператора;
   bootstrap учётные данные не хранит). Приложения Sonarr/Radarr/Lidarr (Dubs)
   уже подключены; для трекеров — профиль «Dubs» (мин. 3 сида), «Standard» не трогайте.
2. **Jellyfin** (`http://127.0.0.1:8096`) — создайте владельца-администратора,
   затем отдельного пользователя только для метаданных; его токен и userId —
   в `config_root/jellyfin.json`.
3. **Seerr** (`http://127.0.0.1:5055`) — пройдите веб-мастер: подключение Jellyfin,
   затем Sonarr (и Radarr при желании). Музыка — через Lidarr напрямую, не через Seerr.
4. **MyShows** — один свой аккаунт на инстанс панели; логин/токен вводятся в
   панели (Настройки) — отдельный механизм от входа на панель.
5. **`security.json`** — `owner_names`/`owner_ids` должны совпадать с реальными
   владельцами Jellyfin, иначе вход закрыт; `allowed_origins`/`trusted_proxies`/
   `public_hosts` — под ваш reverse proxy ([security-reverse-proxy.md](security-reverse-proxy.md)).
6. **`config.json`** — `login`, `public_url`, `jellyfin_url`.

## 8. Запуск и проверка

Юниты включены, но не запущены (`install` осознанно не стартует их). После
заполнения конфигурации:

```bash
sudo systemctl start dubs-panel@main dubs-worker@main dubs-operations@main dubs-myshows-feed
sudo systemctl start dubs-recovery.timer dubs-recent-episodes.timer dubs-jackett-sync.timer
sudo python3 -m installer check
```

`check` показывает готовность каждого сервиса, хранилища, фида и панели
(`HTTP на 9192`). Диагностику в любой момент — `python3 -m installer doctor`.

## Команды инсталлятора

Все подкоманды `python3 -m installer` (глобальные опции до подкоманды:
`--env-file`, `--config-root`, `--state-root`):

| Команда | Опции | Действие |
|---|---|---|
| `plan` | `--no-mutate` (принят) | read-only осмотр и план действий |
| `doctor` | — | read-only health-проверки |
| `install` | `--create-storage` | зависимости, учётки, каталоги, юниты, недостающие сервисы |
| `configure` | `--force` | скелеты конфигов (без `--force` не перезаписывает) |
| `bootstrap` | `--feed-url` | связывание Arr через schema-API |
| `check` | — | сквозная проверка готовности |
| `update` | — | git pull + обновление кода/образов (только работающее) |
| `backup` | `--dest`, `--with-compose-data` | архив конфигов и состояния с манифестом |

Подробности `update`/`backup` — [backup-updates.md](backup-updates.md).

## Не входит в задачи инсталлятора

- Обновления Proxmox VE, настройка прокси/VPN на хосте, «домашние» адреса в конфигах.
- Установка уже существующих сервисов (они определяются и принимаются как есть).
- Данные владельца, API-ключи, state и бинарники — всё живёт вне репозитория.
