# Архитектура

```text
CLI / interactive logon task
  ├── Config + current-user DPAPI
  ├── SQLite WAL/FULL journal + encrypted request outbox
  ├── HTTPS Api + server Clock + local rate limiter
  ├── Engine: heartbeat / claim / lease renewal / recovery / metrics
  └── per-device lock
       ├── MuMu Manager -> current ADB endpoint -> Android identity check
       ├── ADB media transfer / ffprobe
       └── Appium session -> PlatformPublisher -> versioned UI profile
```

`Api` записывает точный JSON и ключ до сетевого запроса. Ответ claim и assignment сохраняются в одной SQLite-транзакции; результат записывается до отправки серверу. DPAPI шифрует assignment/lease/body/response/result; открытые поля БД — идентификаторы, стадии и временные отметки.

`Guard` использует абсолютный lease deadline, серверное время heartbeat и monotonic clock. Перед каждым UI-действием выполняется проверка. Progress работает параллельно UI/ADB операциями через asyncio, блокирующие вызовы выполняются в потоках. Максимальный срок не расширяется за 1800 секунд; margin по умолчанию 20 секунд.

OS file lock исключает второй экземпляр/диагностику в той же учётной записи. SQLite сериализует критические записи. Async lock охватывает всю операцию устройства, включая cleanup Appium/ADB. Метрики используют тот же lock. Unknown сохраняет блокировку и прекращает новую выдачу до сверки/операторского решения.

Платформенная логика отделена от протокола. Общий PlatformPublisher реализует последовательность проверки аккаунта, передачи медиа, UI flow, ввода, однократного submit, доказательства поста и чтения метрик. TikTok/Instagram/YouTube ограничивают свои параметры и формат настоящего внешнего ID; YouTube требует audience и visibility и проверяет заголовок.

Профили — типизированные данные с schema_version, package, точной app_version, locale и селекторами. Общие flows не могут содержать финальный Submit. Неоднозначный locator или неизвестный интерфейс останавливает задание. HTTP retries Appium выключены, чтобы потеря ответа не приводила к повтору click.

Серверный backend и база не меняются. Google credentials отсутствуют на Worker. UI-диагностика хранится только локально и не отправляется в Git/Telegram автоматически.
