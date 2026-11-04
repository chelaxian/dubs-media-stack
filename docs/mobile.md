# Мобильные и ТВ-обёртки

Мобильные приложения — **необязательная** часть: сервер и панель полностью
работают в обычном браузере. Нативное приложение не нужно для запуска стека.

## Что здесь есть и чего здесь нет

В репозитории лежат **исходники собственных обёрток Dubs** (`mobile/`):

- **Android 9+** (`minSdk 28`, `compileSdk`/`targetSdk` 36, Java 17, AGP 8.10.1):
  WebView-обёртка панели — package **`app.dubs.panel`** (v1.2.0, versionCode 12,
  `mobile/android/app`) и вариант для **Android TV** — package
  **`app.dubs.panel.tv`** (v1.4.0-tv, versionCode 7, `mobile/android/tv`;
  режим навигации с пульта — `mobile/server/tv.css` подключается панелью
  как `dubs-tv`).
- **iOS 17+** (deployment target 17.0, bundle **`app.dubs.panel`**, v1.2.0):
  минимальная обёртка (`mobile/ios`, проект XcodeGen — `project.yml`;
  подписывание в проекте отключено — `CODE_SIGNING_ALLOWED=NO` — установка
  в контексте TrollStore, не App Store).

Обёртки — это тонкие WebView-шеллы: открывают **ваш** HTTPS-адрес панели,
добавляют в User-Agent маркер `DubsNative/1.2 Android` (+ `DubsTV/1.0` для ТВ)
и открывают deeplink-переходы к эпизодам. Панель по маркеру даёт обёртке
долгую сессию (persistent cookie) и показывает ТВ-режим.

**Чего здесь нет**: сторонние клиенты (Findroid, Swiftfin, Fladder, Streamyfin,
Infuse) **не написаны с нуля в этом проекте, не бандлятся, не компилируются и
не подписываются** в этом репозитории. Их происхождение и лицензии — по
апстрим-ссылкам в [THIRD_PARTY.md](../THIRD_PARTY.md). Готовые APK/IPA из этого
репозитория не распространяются; проверок на физических устройствах не
заявляется. Секретов подписывания в репозитории нет: release-сборка Android
берёт keystore и пароли из переменных окружения `DUBS_KEYSTORE` /
`DUBS_SIGNING_PASSWORD` (см. `build.gradle`).

## Deeplink-переходы к эпизодам

Кнопка «смотреть» на панели возвращает бессерверные ссылки (без кредов):

| Клиент | Ссылка | Примечание |
|---|---|---|
| Findroid (Android) | `jellyfin://item/<uuid>` | переход к серии; Android-обёртка пробует сначала свои варианты package (`dev.jdtech.jellyfin.dubs[.tv]`), затем апстримный `dev.jdtech.jellyfin` |
| Swiftfin (iOS/tvOS) | `swiftfin://<serverId>/<userId>/item/<itemId>` | в Info.plist — `LSApplicationQueriesSchemes` |
| Fladder | `fladder://details?id=…&userId=…&serverId=…` | |
| Streamyfin (iOS) | `streamyfin://items/page?id=…` | |
| Infuse | `infuse://series/<tmdbId>-<s>-<e>` | по TMDB-идентификатору сериала |
| Браузер | `https://jellyfin.example.com/web/#/details?id=…` | публичный адрес из `config.json → jellyfin_url` |

Обёртка открывает ссылки **только по строгому белому списку** схем/форматов —
произвольные URI не запускаются. Если подходящий клиент не установлен,
обёртка предложит установить его или открыть эпизод в браузере.

Новому пользователю: установите **популярный клиент из его апстрима**
(Findroid/Swiftfin/Fladder/Streamyfin), подключите в нём **свой** сервер
Jellyfin — deeplink заработает без дополнительной настройки на панели.

## Раздача сборок через панель (по желанию оператора)

Панель умеет раздачу ваших **собственных** артефактов (`/apps/`), но только
тех, что оператор явно перечислил в `config.json`:

```json
"mobile": {
  "dir": "/opt/dubs-mobile/releases",
  "artifacts": [ {"file": "dubs-1.2.apk", "label": "Android (собственная сборка)"} ]
}
```

Раздаются только файлы, реально присутствующие в каталоге `dir`; имена с
путями/точками отбрасываются — это белый список, не автоскан.

## Сборка из исходников

Gradle-wrapper в репозиторий не входит — используйте свой Gradle + Android SDK
(`compileSdk`/`targetSdk` 36, `minSdk` 28 = Android 9; TV-модуль — `mobile/android/tv`):

```bash
# Android (из каталога mobile/android):
gradle :app:assembleDebug        # телефон; :tv:assembleDebug — ТВ-вариант

# iOS (XcodeGen → проект Xcode):
cd mobile/ios && xcodegen && open Dubs.xcodeproj
```

В `mobile/tests` — вспомогательные скрипты разработки: smoke-проверки панели
(`panel-ui-smoke.cjs`, `tv-remote.cjs`, `tv-csp.cjs`, …), проверка
`ServerAddressTest.java`, сборочные помощники. Это инструменты локальной
разработки, а не поставляемые сборки.

Адрес панели вводится в самой обёртке («⚙ Сервер»), только HTTPS с
действительным сертификатом; смена сервера сбрасывает cookies и сессию.
