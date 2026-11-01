# Проверки Windows Worker

Результаты подготовки от 1 ноября 2026. Доступный рабочий хост — Linux; действующая серверная часть не переписывалась и не перезапускалась для добавления Worker.

| Набор | Результат |
|---|---|
| Worker, уровень A (httpx transport, fake platform/device adapters) | **69 passed**, 3 skipped на Linux |
| Worker + настоящие FastAPI-маршруты и PostgreSQL shortpost_test | **2 passed** |
| Скопированный серверный backend, прежние regression tests | **47 passed** |
| Исходники контент-фермы, прежние unittest regressions | **19 passed** |
| Windows DPAPI round trip / PowerShell parse | На Linux пропущены, выполняются Windows job в GitHub Actions |
| Уровень B: реальный MuMu / ADB / Appium / аккаунт / навигация | Требуется Windows-компьютер владельца |
| Уровень C: настоящая публикация TikTok / Instagram / YouTube | Не выполнялась; требуется отдельное разрешение владельца |
| Уровень D: 2–3, затем 10 эмуляторов | Не выполнялся; после приёмки одного устройства |

Три skips Linux: DPAPI требует настоящего Windows-пользователя; PowerShell parser требует Windows; отдельный модуль contract tests запускается только с явным SHORTPOST_INTEGRATION_DATABASE_URL и поэтому пропускается обычным unit-запуском. Отдельный contract run успешно выполнил оба теста.

Проверено: точные Idempotency-Key/body при сетевом повторе, HTTP 401/403/409/429/503, heartbeat/server time, потоковая загрузка и SHA-256, запрет redirect чужому origin, отказ от неполного видео, SQLite WAL/FULL, защищённые payloads, атомарный claim journal, сохранение Submit marker после открытия БД, конфликт идентичности устройства, отсутствие второго Submit после сбоя, сериализация разных заданий одного устройства вместе с cleanup session, повтор результата без перепубликации, outbox аналитики, реальные форматы ID/URL, YouTube audience/visibility/title proof, ошибка аккаунта/версии приложения и неоднозначных метрик, отсутствие Appium HTTP retries.

В контрактных тестах используются настоящие серверные auth, queue/lease/ack/result и PostgreSQL constraints. Подменены только Drive/video и Appium/platform actions. Production база/аккаунты/статистика не используются. Задания и mock-публикации находятся только в shortpost_test.

Примерные profiles/*.example.json не являются проверенными адаптерами текущих приложений. navigation_verified=false запрещает live preflight. Для реального использования обязательны калибровка и отдельная приёмка каждой платформы; наличие тестов с fake adapter не считается фактом успешной публикации.

CI: `.github/workflows/worker.yml`, `server-contract.yml`, `content-farm.yml`. Текущие результаты конкретного commit видны в Actions репозитория. CI не использует реальные соцсети или MuMu и не требует production secrets.
