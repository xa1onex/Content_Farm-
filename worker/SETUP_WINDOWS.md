# Развёртывание Windows

## 1. Требования и аудит

Windows 10/11 x64, Python 3.12+, Git, Node.js LTS, Java JDK, Android SDK Platform Tools/SDK, MuMu и FFmpeg (`ffprobe`). Для первого запуска нужен один эмулятор; не открывайте сразу десять. Включите виртуализацию в BIOS/UEFI согласно требованиям установленной версии MuMu.

`doctor` выводит ОС, CPU/RAM/свободное место, доступность виртуализации через CIM, Python, Node, ADB, ffprobe, найденные пути MuMu, Appium и HTTPS сервера. Аудит выполняется на вашем ПК; разработчик не заявляет проверку Windows по результатам Linux.

Установите инструменты через официальные установщики. Для Appium 2 + UiAutomator2 4.2.9 требуется Android SDK и JAVA_HOME; ANDROID_HOME/ANDROID_SDK_ROOT должны указывать на фактический SDK. Не устанавливайте случайный последний UiAutomator2: начиная с major 5 он рассчитан на Appium 3. Проект фиксирует Appium 2.19.0 + UiAutomator2 4.2.9.

## 2. Клонирование и установка

```powershell
git clone https://github.com/xa1onex/Content_Farm-.git
cd Content_Farm-
powershell -NoProfile -File .\scripts\setup-windows.ps1
```

Скрипт проверяет Python, создаёт `.venv-worker`, устанавливает зафиксированные зависимости и CLI, ограничивает ACL только собственного `%LOCALAPPDATA%\ShortPost` текущим пользователем и SYSTEM. Не меняет чужие каталоги или системные установки. Если PowerShell запрещает запуск локальных скриптов, для текущего окна: `Set-ExecutionPolicy -Scope Process RemoteSigned`.

Опциональный Appium устанавливается в `worker/tools/node_modules`, драйвер — в отдельный `%LOCALAPPDATA%\ShortPost\appium`:

```powershell
powershell -NoProfile -File .\scripts\setup-windows.ps1 -InstallAppium
powershell -NoProfile -File .\scripts\start-appium.ps1
```

Если Appium 2 уже работает на loopback, используйте его и не выполняйте `-InstallAppium`. Проверьте `doctor` и `appium driver doctor uiautomator2` для выбранного APPIUM_HOME. Разные Appium sessions используют разные systemPort.

## 3. Получение Worker token

Администратор выполняет `POST /workers` на сервере (admin bearer) и получает `worker.id` + отдельный Worker token. Удобный server-side helper в репозитории:

```bash
python server/scripts/provision_worker.py --name Windows-PC --max-parallel 1 \
  --bootstrap-file /PATH/TO/EXISTING/secrets/bootstrap.json \
  --output /PRIVATE/PATH/secrets/windows-pc.worker.json
```

Команда выполняется **на сервере**, обращается к loopback API 8108 и сохраняет новый Worker token в закрытый файл; admin token остаётся на сервере. Для существующего размещения bootstrap расположен в `/root/Documents/Codex/2026-11-01/files-pasted-by-the-user-root/outputs/autopost/secrets/bootstrap.json`. Не публикуйте onboarding JSON и не помещайте его в Git. Помощник не меняет backend-контракт.

На Windows из корня репозитория:

```powershell
.\.venv-worker\Scripts\shortpost-worker.exe init --worker-id WORKER_UUID
.\.venv-worker\Scripts\shortpost-worker.exe token set
.\.venv-worker\Scripts\shortpost-worker.exe doctor
.\.venv-worker\Scripts\shortpost-worker.exe connection
```

Токен вводится через getpass, не аргумент командной строки. DPAPI привязан к текущему Windows-пользователю: запуск из другой учётной записи не расшифрует секрет. Файл конфигурации — `worker/config.local.toml`. Если adb/ffprobe не в PATH, задайте абсолютные пути. При неоднозначной установке MuMu укажите `mumu_manager` вручную по результату `emulators`/`doctor`.

## 4. Одно устройство

Запустите один MuMu вручную, включите его ADB и определите реальный loopback endpoint через MuMu Manager/настройки. Не угадывайте диапазоны портов.

```powershell
.\.venv-worker\Scripts\shortpost-worker.exe emulators
.\.venv-worker\Scripts\shortpost-worker.exe bind --external-id mumu-01 --instance 0 --serial 127.0.0.1:ACTUAL_PORT
```

Команда проверяет Android ID и дописывает стабильное соответствие в config/journal. Повторный heartbeat регистрирует устройство на сервере. Администратор привязывает реальные аккаунты к полученным server Worker/device UUID. Windows хранит только Worker bearer.

В `[[devices]]` добавьте `[devices.profiles]` с путями к локально настроенным профилям. Для копий используйте `tiktok.local.json` и т. п. Пройдите [PLATFORM_ADAPTERS.md](PLATFORM_ADAPTERS.md), войдите в аккаунты самостоятельно и выполните безопасный `test-navigation`.

## 5. Запуск

```powershell
powershell -NoProfile -File .\scripts\start-worker.ps1
```

Это соединение, heartbeat, recovery и аналитика без новых публикаций. Когда владелец разрешает реальную проверку, выставьте `allow_publish=true` и явно запустите:

```powershell
powershell -NoProfile -File .\scripts\start-worker.ps1 -Live
```

Live preflight требует настроенных и navigation-verified профилей. Это разрешает настоящие публикации из серверной очереди; сначала оставьте один согласованный аккаунт и одно задание. Перед этим Drive и сам серверный аккаунт должны быть действительно настроены. Ни установка, ни тесты в репозитории не дают разрешения публиковать автоматически.

## 6. Управление и автозапуск

`status`, `pause`, `resume`, `stop` вызываются отдельным CLI. `pause` прекращает новые назначения, активные попытки сохраняют lease. `stop` ждёт завершения активных операций. Не удаляйте journal для устранения unknown.

```powershell
powershell -NoProfile -File .\scripts\install-autostart.ps1 -IncludeAppium
```

Создаются задачи Task Scheduler для текущего интерактивного пользователя при входе, не Windows Service. По умолчанию новые публикации выключены. Параметр `-Live` нужен отдельно, если владелец разрешает постоянное выполнение. Уже существующие задачи скрипт не перезаписывает. Appium должен успеть запуститься; при недоступности Worker завершает попытку до публикации безопасно и сообщает ошибку.

## 7. Обновление

Сначала `pause`, затем `stop`; дождитесь выхода процесса и разберите unknown. Сохраните приватную копию `%LOCALAPPDATA%\ShortPost` и локальных config/profiles. Выполните `git pull --ff-only`, затем setup-windows.ps1. Существующее virtualenv обновляется, credentials/journal не перезаписываются. После изменения версии мобильного приложения профили проверяются заново. SQLite schema version больше поддерживаемой не открывается с автоматической очисткой.

## 8. Масштабирование

Сначала подтвердите одну платформу/один эмулятор, затем 2–3 экземпляра. Каждому устройству нужны уникальные external_id, instance_index, Android ID, systemPort. Поднимайте local/server max_parallel постепенно, наблюдая RAM и место. Worker прекращает новые claims при недостатке ресурсов. Начальный порог — 2 GiB доступной RAM и 5 GiB диска. Не клонируйте одинаковый Android ID без явной перепривязки.
