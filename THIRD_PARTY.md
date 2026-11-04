# Сторонние проекты (THIRD_PARTY)

Собственный код этого репозитория распространяется по MIT (см. [LICENSE](LICENSE)).
Сторонние продукты здесь **не перераспределяются**: стек использует официальные
Docker-образы из апстрим-реестров, а клиенты устанавливаются пользователем
из своих апстримов. Права и лицензии принадлежат их владельцам.

> Примечание: лицензии ниже приведены для ориентира; **авторитетный источник —
> файл LICENSE в соответствующем апстриме**. Сверяйтесь перед использованием.

## Сервисы стека (официальные образы)

| Компонент | Апстрим | Образ в стеке | Лицензия (сверяйтесь в апстриме) |
|---|---|---|---|
| Jellyfin | https://github.com/jellyfin/jellyfin | `jellyfin/jellyfin` | GPL-2.0 |
| Seerr | https://github.com/seerr-team/seerr (docs: https://docs.seerr.dev) | `ghcr.io/seerr-team/seerr` | см. апстрим |
| Sonarr | https://github.com/Sonarr/Sonarr | `lscr.io/linuxserver/sonarr` | GPL-3.0 |
| Radarr | https://github.com/Radarr/Radarr | `lscr.io/linuxserver/radarr` | GPL-3.0 |
| Lidarr | https://github.com/Lidarr/Lidarr | `lscr.io/linuxserver/lidarr` | GPL-3.0 |
| Prowlarr | https://github.com/Prowlarr/Prowlarr | `lscr.io/linuxserver/prowlarr` | GPL-3.0 |
| Jackett | https://github.com/Jackett/Jackett | `lscr.io/linuxserver/jackett` | GPL-3.0 |
| Transmission | https://github.com/transmission/transmission | `lscr.io/linuxserver/transmission` | GPL-2.0 / GPL-3.0 (по файлам) |
| FlareSolverr | https://github.com/FlareSolverr/FlareSolverr | `flaresolverr/flaresolverr` | см. апстрим |
| busybox (sentinel) | https://git.busybox.net/busybox/ | `busybox:1.36` | GPL-2.0 |

Образы `lscr.io/linuxserver/*` собираются проектом LinuxServer.io
(https://www.linuxserver.io/) поверх апстрим-приложений; условия использования
образов — в их документации.

## Deeplink-клиенты (устанавливаются пользователем, НЕ входят в этот репозиторий)

Эти приложения **не написаны** в dubs-media-stack, не бандлятся, не
компилируются и не подписываются здесь. Панель лишь формирует ссылки
 deeplink, которые открывают установленный у пользователя клиент:

| Клиент | Апстрим |
|---|---|
| Findroid (Android) | https://github.com/jarnedemeulemeester/findroid |
| Swiftfin (iOS/tvOS) | https://github.com/jellyfin/Swiftfin |
| Fladder | https://github.com/DonutWare/Fladder |
| Streamyfin (iOS) | https://github.com/streamyfin/streamyfin |
| Infuse (проприетарный) | https://firecore.com/infuse |

## Инструменты разработки (используются локально, не перераспределяются)

| Инструмент | Апстрим |
|---|---|
| Docker / Compose v2 | https://docs.docker.com/ |
| Android SDK / Gradle | https://developer.android.com/ , https://gradle.org/ |
| XcodeGen (iOS-проект) | https://github.com/yonaskolb/XcodeGen |
| Python `requests` | https://github.com/psf/requests (зависимость рантайма, ветка 2.x) |

Если вы сопровождающий и хотите добавить/уточнить лицензионную строку —
добавляйте её только после проверки LICENSE в апстриме.
