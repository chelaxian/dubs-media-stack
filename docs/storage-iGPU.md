# Хранилище и iGPU

Пути ниже — **иллюстративный пример**: выберите свои и используйте одно и то же
значение везде (`compose/.env`, sentinel, команды). В примерах локальный каталог
`/srv/dubs/media`.

## Схема хранилища

```
MEDIA_ROOT (compose/.env, абсолютный путь)
├── torrents/     # загрузки Transmission  (02775, PUID:PGID)
├── shows/        # библиотека сериалов   (02775, PUID:PGID)
├── movies/       # библиотека фильмов    (02775, PUID:PGID)
├── music/        # библиотека музыки     (02775, PUID:PGID)
└── .dubs-storage-ok   # sentinel-файл (0644), «вооружается» один раз вручную
DUBS_DATA_ROOT   # конфиги контейнеров: jellyfin/ seerr/ sonarr/ radarr/
                 # lidarr/ transmission/ prowlarr/ jackett/ (0775, PUID:PGID)
```

`install --create-storage` создаёт подкаталоги только **после** вооружённого
sentinel — заглушек на несмонтированной точке не создаёт никогда (без sentinel
команда печатает напоминание и откладывает создание).

## Порядок монтирования (настройка и каждая перезагрузка)

Диск должен быть смонтирован **до** старта контейнеров и «вооружения»:

1. Локальный путь — явный `sudo mkdir -p /srv/dubs/media`; внешний диск —
   смонтируйте и проверьте: `findmnt /srv/dubs/media`.
2. Вооружите sentinel один раз, **буквальным** путём (не `$MEDIA_ROOT` — shell
   не установит переменную, и файл молча возникнет в корне `/`):

```bash
sudo install -m 0644 /dev/null /srv/dubs/media/.dubs-storage-ok
```

3. Только затем `install --create-storage` / `docker compose up -d`.

Sentinel обязан лежать **на намеченном диске**: если вооружить путь до
монтирования, файл осядет на корневой ФС и будет «защищать» пустое место.

## Sentinel — защита от «тихой» неправильной точки монтирования

Типичная авария: путь в `MEDIA_ROOT` опечатан, Docker молча создаёт пустой
каталог на корневой ФС, стек «работает» с пустым диском. Защита — fail-closed:

- Каждый bind в `docker-compose.yml` использует **длинный синтаксис с
  `create_host_path: false`**: при отсутствующем пути на хосте контейнер
  падает с ошибкой вместо тихого создания пустого каталога Docker'ом.
- Контейнер `storage-sentinel` (одноразовый) проверяет наличие
  `.dubs-storage-ok` на смонтированном `MEDIA_ROOT`; **все** медиасервисы
  compose зависят от его успешного завершения и не стартуют иначе.
- Воркер панели перед любыми мутациями файлов вызывает `storage_ready()`:
  монтирование существует + sentinel «вооружён» (кандидаты: сам каталог и
  его родитель). Иначе отказ.

**Честные границы**: sentinel ловит типичную аварию, но не является
универсальной гарантией корректности на 100% — он не отличит «намеченный диск»
от другого смонтированного диска с подложенным маркером. Однократно проверьте
монтирование сами (`findmnt`) и держите sentinel на целевом диске. Для принятых
(не-compose) схем можно отключить требование: `"storage_sentinel": false` в
`config.json` (само наличие монтирования всё равно проверяется).

## Same-path: один путь — разные точки зрения

Ключевое правило стека: **каталог загрузок и библиотеки видят все под тем же
содержимым, но под своими именами**. Двигать файлы между «download» и «library»
не нужно — Arr импортируют жёсткими ссылками/переименованием внутри одного ФС.

| Хост (MEDIA_ROOT) | Transmission | Sonarr | Radarr | Lidarr | Jellyfin |
|---|---|---|---|---|---|
| `torrents/` | `/downloads` | `/torrents` | `/torrents` | `/torrents` | — |
| `shows/`    | — | `/tv` | — | — | `/media/shows` (read-only) |
| `movies/`   | — | — | `/movies` | — | `/media/movies` (read-only) |
| `music/`    | — | — | — | `/music` | `/media/music` (read-only) |

Согласование имён делает remote-path mapping, который создаёт `bootstrap`:
`transmission: /downloads/ → /torrents/` (конфликтующий ручной mapping не
перезаписывается). Категории загрузок (уникальные, по типу медиа):
`dubs-tv` (`/downloads/complete/tv`), `dubs-movies` (`.../movies`),
`dubs-music` (`.../music`).

## Безопасные UID/GID

- Все контейнеры работают с `PUID`/`PGID` из `compose/.env` — файлы на хранилище
  имеют одного владельца; world-writable нигде не используется.
- Разделение uid: **web-uid панели (`dubs`) не имеет доступа к медиа вообще**
  (ни владельца, ни членства в медиагруппе); запись в медиа есть только у
  **worker-uid** (`dubs-worker`) — ему медиагруппа PGID добавлена как
  дополнительная. Код панели — root:dubs, 0755/0644, роли только читают.
- Право записи в `torrent_host`/`shows_host` — только у роли `worker`
  (см. `ReadWritePaths` в юнитах); веб-роль и operations — нет. Удаление файлов
  библиотеки идёт через API Sonarr.

## iGPU: Intel/AMD VA-API (опционально)

Аппаратное транскодирование Jellyfin через `/dev/dri` — **опциональный**
override-файл, по умолчанию выключен. Гарантий по конкретным GPU нет —
поддерживаются только Intel/AMD VA-API; NVIDIA не входит в проект.

```bash
# узнайте GID группы render (обычно 104 на Debian/Ubuntu):
getent group render | cut -d: -f3
# впишите в compose/.env:
#   RENDER_GID=104
cd compose
docker compose -f docker-compose.yml -f docker.igpu.yml up -d jellyfin
```

`docker.igpu.yml` добавляет jellyfin `devices: /dev/dri:/dev/dri` и
`group_add: ["$RENDER_GID"]`. Затем включите транскодирование в дашборде
Jellyfin (VA-API) вручную. Проверка доступности устройства — на операторе
(`ls -l /dev/dri`, права группы render).

## Проверки

```bash
python3 -m installer plan --no-mutate   # storage: sentinel armed/MISSING
python3 -m installer doctor             # проблемы хранилища и прав
sudo python3 -m installer check         # storage: ready / NOT READY: причина
```
