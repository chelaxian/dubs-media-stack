# Безопасность и reverse proxy

## Модель доступа

- **Все HTTP-эндпоинты стека биндятся только на loopback** (`BIND_IP=127.0.0.1`
  в `compose/.env`): Jellyfin 8096, Seerr 5055, Sonarr 8989, Radarr 7878,
  Lidarr 8686, Transmission 9091, Prowlarr 9696, Jackett 9117, FlareSolverr 8191,
  панель 9192/9193, фид MyShows 9191.
- Наружу — **только** reverse proxy на том же хосте с TLS и peer-порт BitTorrent
  **51413 TCP/UDP** (`PEER_BIND_IP=0.0.0.0` по умолчанию; можно сузить до LAN).
- Фид MyShows (личный список просмотра) биндится на loopback + шлюз docker-сети
  `dubs`, чтобы контейнерный Sonarr мог его читать. Wildcard-бинды (`0.0.0.0`,
  `::`, `*`) в `DUBS_FEED_BIND` **запрещены** и отклоняются инсталлятором.
- Никакого «открыть всё на файрволе» или catch-all доверия: каждый внешний
  доступ — через именованный vhost за прокси.

## Доступ без прокси: SSH-туннель

Для разового доступа с рабочего ноутбука публикать ничего не нужно —
пробросьте loopback-порты через SSH:

```bash
ssh -L 9192:127.0.0.1:9192 -L 8096:127.0.0.1:8096 user@media-host
# затем на локальной машине: http://127.0.0.1:9192 (панель), http://127.0.0.1:8096 (Jellyfin)
```

Вход в панель при этом работает как обычно (владельцы из `security.json`;
для loopback-адреса `allowed_networks`/`trusted_proxies` настроек не требуют).
Постоянный доступ для нескольких устройств — через reverse proxy с TLS (ниже).

## Что ожидает панель от прокси

Панель проверяет:

1. **Host** запроса — должен совпасть с `security.json → public_hosts`
   (канонические имена, например `panel.example.com`).
2. **Origin** у мутирующих запросов — должен быть из `allowed_origins`
   (`https://panel.example.com`).
3. **`X-Forwarded-*`** принимаются только от адресов из `trusted_proxies`.
   Запросы с прокси-заголовками от прочих адресов считаются подменой.
4. Доступ по сети ограничен `allowed_networks` (по умолчанию только loopback —
   то есть сам reverse proxy на этом же хосте).

Без заполненных `public_hosts`/`allowed_origins`/`trusted_proxies` вход через
прокси работать не будет — это осознанный fail-closed.

## Nginx: точный пример

Один vhost на панель (HTTPS обязателен; сертификат — ваш, например Let's Encrypt):

```nginx
server {
    listen 443 ssl;
    http2 on;
    server_name panel.example.com;          # = security.json public_hosts

    ssl_certificate     /etc/letsencrypt/live/panel.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/panel.example.com/privkey.pem;

    location / {
        proxy_pass         http://127.0.0.1:9192;
        proxy_set_header   Host              $host;      # панель сверяет с public_hosts
        proxy_set_header   X-Forwarded-For   $remote_addr;
        proxy_set_header   X-Forwarded-Proto https;
        proxy_redirect     off;
        proxy_read_timeout 120;
    }
}
```

`$remote_addr` прокси должен попасть в `trusted_proxies`. Если прокси
каскадный (CDN → локальный nginx) — перечислите **все** адреса цепочки, иначе
заголовки считаются подделанными.

Для Jellyfin и Seerr — аналогичные vhost-блоки на `127.0.0.1:8096` и
`127.0.0.1:5055` (например, `jellyfin.example.com`); заголовки те же
`Host` + `X-Forwarded-For` + `X-Forwarded-Proto`. В настройках Jellyfin укажите
публикуемый базовый URL при необходимости; `config.json → jellyfin_url` панели
должен совпадать с публичным https-адресом — из него строятся deeplink-переходы.

## Nginx Proxy Manager (NPM)

- Proxy Host → Forward Hostname `127.0.0.1`, порт `9192` (или 8096/5055), вкл. SSL
  (Request a new certificate), «Websockets Support» для Jellyfin.
- **Не включайте** опции вроде «Block Common Exploits»-белых списков, режущих
  заголовки, и не добавляйте `X-Forwarded-For` вручную поверх цепочки — NPM уже
  передаёт адрес клиента; этот адрес и внесите в `trusted_proxies`.

## Что запрещено

- Catch-all сервера с `proxy_pass` на панель «на всякий случай».
- Открытие 8096/8989/9696/... наружу «чтобы работало» — только loopback.
- Доверие `X-Forwarded-*` от всех подряд (`trusted_proxies: ["0.0.0.0/0"]` и т.п.).
- Отключение TLS «в локалке» без понимания: cookie сессии помечаются
  `Secure`, и HTTP-вход через прокси не будет принят.

## Сессии и защита панели

- Вход только по именам/UserId владельцев Jellyfin из `security.json`
  (`owner_names` + `owner_ids` должны совпасть) — доступ по одному паролю
  «всем домом» не предусмотрен.
- Сессии — подписанные cookie (ключ `signing_key`), CSRF-токены на мутирующих
  формах, персистентная троттлинг-таблица в `state_root/security.sqlite3`
  (rate-limit на логин и действия), сессии отзываются logout'ом.
- Токен MyShows передаётся панелью внешнему RPC через stdin `curl` — не в
  аргументах процесса и не в логах.

## Peer-порт и торренты

`51413 TCP/UDP` — единственный публично слушаемый порт стека; он нужен для
входящих пиров. Сузить до LAN можно через `PEER_BIND_IP` в `compose/.env`.
HTTP-интерфейс Transmission (9091) наружу не публикуется никогда.

## Удалённые существующие сервисы

Если часть сервисов уже работает на другой машине, панель/автоматизации ходят
туда по `base_url` из `config_root` — держите эти адреса маршрутизируемыми
(свой VPN/tailnet/локальная сеть), а в `indexer_endpoints` явно перечислите
доверенные хосты/порты для скачивания торрентов. Публикация этих сервисов
наружу через «доверенный» прокси без их собственной аутентификации не
поддерживается.
