# Бэкап и обновления

## Команды

Обе подкоманды (`installer/cli.py → build_parser`), глобальные опции
`--env-file`/`--config-root`/`--state-root` работают и здесь:

```bash
sudo python3 -m installer backup --dest /var/backups
# с конфигами всех контейнерных сервисов (большой архив; см. ограничения ниже):
sudo python3 -m installer backup --dest /var/backups --with-compose-data
sudo python3 -m installer update
```

## Что архивирует `backup`

`dubs-backup-<штамп>-<hex>.tar.gz`, права **0600 с рождения** (создание через
`O_EXCL|O_NOFOLLOW` — существующий файл/симлинк никогда не перезаписывается;
случайные hex в имени — два запуска в одну секунду не collide). Внутри —
каталоги с **уникальными arcname** и файл `restore-manifest.json`:

| arcname | Источник |
|---|---|
| `config` | `config_root` (`/etc/series-control`) — подписной ключ и все API-ключи |
| `web-state` | `state_root` (`/var/lib/series-control`) |
| `worker-state` | `worker_root` (`/var/lib/series-control-worker`) |
| `feed-state` | `feed_state` (`/var/lib/myshows-sync`) |
| `covers` | `covers` (`/var/cache/series-control-covers`) |
| `compose-data` | только с `--with-compose-data`: `DUBS_DATA_ROOT` целиком |

Уникальные arcname исключают конфликт имён (каталоги-источники имеют одинаковые
базовые имена); перед архивацией проверяется, что логические имена и целевые
пути попарно различны, а два члена не указывают на один каталог.

SQLite: базы **web-state** (например, `security.sqlite3` с WAL) снимаются
консистентно через sqlite3 backup API ещё при работающей панели — WAL
«складывается» внутрь снимка, `-wal`/`-shm` в архив не попадают. SQLite-файлы
других членов архивируются как есть: для гарантированной консистентности
остановите юниты перед бэкапом (инсталлятор печатает предупреждение о каждом
таком файле).

### Ограничение `--with-compose-data`

Архив содержит **копирование файлов живых контейнерных SQLite** — это
неприемлемо, поэтому команда **отказывается работать**, пока compose-сервисы
запущены. Сначала остановите стек (`docker compose stop`) и повторите
— «quiesced»-режим единственный поддерживаемый для compose-данных.

## Восстановление — по манифесту, никогда не `tar -C /`

Архив содержит ключи — храните как секрет (0600, не в общий cloud без шифрования).

1. Остановите роли панели: `sudo systemctl stop dubs-panel@main dubs-worker@main dubs-operations@main dubs-myshows-feed`.
2. Распакуйте архив во **временный каталог**: `mkdir /tmp/restore && tar -xzf <архив> -C /tmp/restore`.
3. Прочитайте `/tmp/restore/restore-manifest.json` — поле `"restore"` содержит
   точную карту «arcname → путь назначения» именно той установки, с которой
   снят бэкап.
4. Скопируйте каждый каталог из staging по этому отображению **вручную и
   выборочно** (`config → /etc/series-control`, `web-state → /var/lib/series-control`
   и т.д.). Слепая распаковка в корень (`tar -C / -xzf`) **запрещена**: она
   размазывает каталоги по полученным именам, а не по реальным путям.
5. Проверьте права (0640 на конфиги, 0750 на каталог), `python3 -m installer doctor`.
6. Запустите роли заново, затем `sudo python3 -m installer check`.

Если корни конфигов/состояния нестандартные (`--config-root`/`--state-root`),
манифест это учёл — доверяйте ему, а не «типичным» путям из документации.

## Обновления

Поведение (`installer/cli.py → cmd_update`):

1. **Отказ при грязном репозитории**: локальные изменения в checkout → `update`
   не выполняется (`git status --porcelain` не пуст).
2. `git pull --ff-only` из origin.
3. Код повторно копируется в `/opt/dubs` (белый список; `.env`, данные и
   приватные файлы оператора не трогаются — владельцы и режимы сохраняются).
4. `docker compose pull`; при неудаче обновление останавливается с ошибкой,
   а не продолжает со устаревшими образами.
5. `docker compose up -d` — **только уже запущенные сервисы и с `--no-deps`**:
   осознанно остановленный сервис не запускается, зависимости и принятые/внешние
   приложения не затрагиваются. Нет запущенных — ничего не стартует.
6. `systemctl daemon-reload` и перезапуск **только активных** юнитов
   (`dubs-panel@main`, `dubs-worker@main`, `dubs-operations@main`,
   `dubs-myshows-feed`). Таймеры и остановленные роли остаются как есть.

Перед обновлением: бэкап (см. выше), прочтите изменения в репозитории —
миграции конфигов инсталлятор не выполняет за вас (скелеты новых ключей
добавляются `configure`, существующие файлы он не перезаписывает).

### Версии образов

По умолчанию в `compose/.env` — теги `:latest`. Для воспроизводимости
закрепляйте образы тегами/дайджестами прямо в `.env` (файл поддерживает
переопределение: `JELLYFIN_IMAGE`, `SONARR_IMAGE`, `RADARR_IMAGE`,
`LIDARR_IMAGE`, `TRANSMISSION_IMAGE`, `PROWLARR_IMAGE`, `JACKETT_IMAGE`,
`FLARESOLVERR_IMAGE`, `SEERR_IMAGE`, `SENTINEL_IMAGE`):

```ini
SONARR_IMAGE=lscr.io/linuxserver/sonarr:4.0.x@sha256:...
```

Роллбэк = возврат старого значения в `compose/.env` + `docker compose up -d`
и/или `git checkout <прошлый тег>` + `update` (после очистки рабочего дерева).
