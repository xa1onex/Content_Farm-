# Content Farm / ShortPost

Единый репозиторий трёх компонентов:

| Каталог | Что находится внутри | Где работает |
|---|---|---|
| `server/` | Существующий ShortPost: FastAPI, PostgreSQL, очередь, расписание, аналитика и Telegram-модуль | Linux / Docker |
| `worker/` | Новый локальный исполнитель: API, SQLite journal, DPAPI, MuMu/ADB/Appium, платформенные адаптеры | Windows 10/11, интерактивный пользователь |
| `content_farm/` | Исходники действующей фермы нарезки, скопированные без изменения её работающего экземпляра | Linux; на Windows — WSL2 / Docker Desktop |

Сервер уже доступен по `https://origin.xdoublegroup.online:8443/api/v1`. Для Windows Worker **не нужно разворачивать сервер и PostgreSQL на ПК**. Backend и его контракт не переписывались. Спецификация: [WORKER_API_SPEC.md](docs/WORKER_API_SPEC.md), схема: [openapi.json](docs/openapi.json).

## Быстрый старт Windows

Нужны Git и Python 3.12+. В PowerShell:

```powershell
git clone https://github.com/xa1onex/Content_Farm-.git
cd Content_Farm-
powershell -NoProfile -File .\scripts\setup-windows.ps1
.\.venv-worker\Scripts\shortpost-worker.exe init --worker-id YOUR_WORKER_UUID
.\.venv-worker\Scripts\shortpost-worker.exe token set
.\.venv-worker\Scripts\shortpost-worker.exe doctor
```

UUID и отдельный Worker token выдаёт администратор серверной части. Токен вводится скрыто и хранится через Windows DPAPI; admin/farm/Google credentials на ПК не нужны.

Для Appium 2 можно использовать установленный сервер или отдельную проектную установку:

```powershell
powershell -NoProfile -File .\scripts\setup-windows.ps1 -InstallAppium
powershell -NoProfile -File .\scripts\start-appium.ps1
```

MuMu, Android SDK/JDK и FFmpeg устанавливаются/проверяются отдельно; имеющиеся установки не заменяются. Начинайте с **одного** эмулятора. Далее: [SETUP_WINDOWS.md](worker/SETUP_WINDOWS.md), [MUMU_SETUP.md](worker/MUMU_SETUP.md).

## Что проверено и что требует ПК

API client, durable journal, восстановление, контроль lease, скачивание, классификация ошибок и механизмы адаптеров проверяются автоматизированно. Дополнительно есть испытания Worker с настоящими неизменёнными FastAPI-маршрутами и отдельной PostgreSQL `shortpost_test`.

Доступный во время разработки хост — Linux. MuMu, Windows GUI и действующие TikTok/Instagram/YouTube приложения на нём не запускались. Поэтому платформенные адаптеры **ещё не прошли приёмку на настоящих приложениях**. Профили в `worker/profiles/*.example.json` — шаблоны, они не объявлены проверенными и не позволяют включить live-работу без локальной калибровки. Отчёт: [TEST_REPORT.md](worker/TEST_REPORT.md).

Сначала настройте версию приложения, язык, устойчивые селекторы, account proof и результат публикации; выполните `test-navigation`, который останавливается перед Publish. Живые публикации требуют `allow_publish=true` и явного `run --live`. Один только запуск установки или обычного `run` ничего не публикует. Инструкция: [PLATFORM_ADAPTERS.md](worker/PLATFORM_ADAPTERS.md).

По предыдущему развёртыванию Drive и включение Telegram-модуля на работающем сервере остаются отдельными подключениями, требующими разрешённых credentials/установки. Наличие исходников в GitHub не подключает эти интеграции автоматически.

## Управление Worker

```powershell
.\.venv-worker\Scripts\shortpost-worker.exe connection
.\.venv-worker\Scripts\shortpost-worker.exe emulators
.\.venv-worker\Scripts\shortpost-worker.exe status
.\.venv-worker\Scripts\shortpost-worker.exe pause
.\.venv-worker\Scripts\shortpost-worker.exe resume
.\.venv-worker\Scripts\shortpost-worker.exe stop
powershell -NoProfile -File .\scripts\start-worker.ps1
```

Обычный запуск обслуживает heartbeat/recovery/аналитику, но не забирает новые задания на публикацию. `stop` прекращает новые назначения и ожидает завершения активных операций. Автозапуск: `scripts/install-autostart.ps1`; используется вход пользователя, не Windows Service / Session 0. Для задания live-режима автозапуску нужен отдельный параметр `-Live`.

## Сохранность данных

В Git отсутствуют production `.env`, bearer/Google/Telegram credentials, SQLite/SQL dumps, задания пользователей и видео. Локальные токены, зашифрованный journal, логи и диагностика Windows находятся в `%LOCALAPPDATA%\ShortPost`. Локальные config/profile-файлы `*.local.toml`/`*.local.json` игнорируются Git. Перед Submit журнал фиксируется SQLite WAL + synchronous FULL; неизвестный результат никогда не превращается в повтор публикации.

Фоны и пользовательские медиа контент-фермы не публикуются в репозитории. Для её исходников см. [content_farm/README_REPOSITORY.md](content_farm/README_REPOSITORY.md). Для server — [server/README.md](server/README.md). Это отдельные окружения; Windows bootstrap устанавливает только Worker.

## Тесты

```powershell
.\.venv-worker\Scripts\python.exe -m pytest .\worker\tests -q
```

CI проверяет Worker на Linux и Windows; PostgreSQL contract job — отдельно на Ubuntu. Реальные публикации/эмуляторы в CI не используются. Дополнительные документы: [ARCHITECTURE](worker/ARCHITECTURE.md), [API_INTEGRATION](worker/API_INTEGRATION.md), [RECOVERY](worker/RECOVERY.md).
