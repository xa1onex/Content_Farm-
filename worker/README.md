# ShortPost Windows Worker

Python 3.12+ executor для существующего API. Приоритет — отсутствие повторных публикаций при потере соединения или перезапуске Windows.

- HTTPS/httpx, устойчивые ключи запросов и event IDs.
- SQLite WAL/FULL и DPAPI: assignment, lease и outbox зашифрованы.
- Heartbeat 30 секунд, progress 30 секунд, отдельный контроль абсолютного срока lease.
- Стабильная связь external_id → MuMu index → Android ID; ADB port может изменяться.
- Один OS process и одна активная операция на эмулятор; default max_parallel=1.
- Appium/UiAutomator2, версионированные профили; неизвестный/неоднозначный UI останавливает задание.
- Отдельные TikTok/Instagram/YouTube adapters с проверкой аккаунта, метаданных и настоящего post URL/ID.
- Outbox результата/аналитики, recovery без повторного Submit, метрики на 24/48 часов.

Инструкции: [SETUP_WINDOWS.md](SETUP_WINDOWS.md). Реальные платформы пока не приняты на Windows: [TEST_REPORT.md](TEST_REPORT.md). Шаблоны профилей не являются рабочими селекторами конкретной версии приложения.
