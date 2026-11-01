# Совместимость с сервером

Основные источники: [WORKER_API_SPEC](../docs/WORKER_API_SPEC.md), [реальная OpenAPI](../docs/openapi.json), неизменённый `server/app`. Все внешние запросы идут по HTTPS с проверкой сертификата. Redirects при media download выключены, ticket URL должен иметь тот же origin и точный путь назначенного задания.

Endpoints: heartbeat, jobs/claim, jobs/{id}/ack, progress, media-ticket, video, result, jobs/{id}, jobs, devices, publications, analytics/snapshots. На ПК нет admin/farm токенов. Административное provision отдельно выполняется владельцем на сервере.

На каждом POST сохраняются Idempotency-Key и исходный JSON; сетевой повтор использует те же байты и event_id. Новый progress — новое событие; повтор старого события не считается продлением. Локальный limiter допускает до 90 запросов/минуту, оставляя запас относительно серверных 120.

401/403 останавливают новые claims и лишают активные Guards права новых UI-действий. 409 не даёт права на повтор публикации. 429 учитывает Retry-After; 503/сеть сохраняют pending request/result для повторного соединения. API response body и HTTP headers не пишутся в логи.

## Зафиксированные особенности реального контракта

1. В документе пустой claim показан как `{job:null}`; фактический response_model_exclude_none сериализует его как `{}`. Worker использует `response.get('job')` и принимает оба варианта. Backend не исправлялся.
2. Heartbeat не продлевает lease. ACK/progress возвращают абсолютный deadline; кэшированный идемпотентный ответ не предоставляет новую аренду.
3. Сервер принимает safe_failure только после ACK (processing). Если ACK не состоялся или аренда уже стала unknown, Worker не пытается обойти состояние и оставляет сверку оператору/lease recovery.
4. Claim не фильтруется по локально свободному device. Existing enabled у устройств нельзя выключить через heartbeat. Поэтому при локальном unresolved/unknown Worker консервативно прекращает новые claims; неисправное устройство/аккаунт выключается администратором на сервере. Новые endpoints не вводятся.
5. max_parallel контролируется сервером при выдаче и локально конфигурацией. Worker не использует admin GET /workers для чтения настройки.
6. List publications не возвращает account username/platform/device. Для собственной отложенной аналитики используются сохранённые assignment и подтверждённый publication_id. Исторические публикации без локальной привязки автоматически не интерпретируются как знакомые.

## Последовательность

claim → durable assignment → ACK processing → parallel renew → temporary ticket → stream to `.part` → size/SHA check + atomic rename → ffprobe → identity-checked MuMu/ADB → Appium/profile/account proof → metadata → durable irreversible marker → one Submit → real post proof → durable result → POST result.

Если после Submit нельзя подтвердить настоящий URL/ID, outcome=unknown. UUID локальной операции никогда не подставляется как внешний ID. После published создаются локальные задачи измерений на 24 и 48 часов; недоступная метрика не подменяется нулём.

Контрактные тесты с PostgreSQL используют только shortpost_test. В них Drive и Appium заменены; сама маршрутизация, авторизация, очередь и запись публикаций — настоящие серверные реализации.
