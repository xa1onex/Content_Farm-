# Windows Worker API Specification v1

Это спецификация второй части проекта, а не реализация Windows Worker. Серверная база URL текущего развёртывания: `https://origin.xdoublegroup.online:8443/api/v1`. Все endpoints ниже относительны `/api/v1`. OpenAPI: `https://origin.xdoublegroup.online:8443/openapi.json`; сохранённая копия — `docs/openapi.json`.

## 1. Границы доверия и типы

- Только HTTPS с проверкой сертификата для внешнего подключения. Не используйте verify=false.
- Admin создаёт Worker и выдаёт отдельный случайный bearer-токен. Самостоятельная анонимная регистрация выключена.
- Worker видит только выданные ему задания, свои устройства и публикации аккаунтов, привязанных к нему. Admin/farm-токены ему не нужны.
- `Authorization: Bearer WORKER_TOKEN` нужен на всех worker API, включая загрузку видео. Google-ключи Worker не выдаются.
- UUID передаются строками. Время — RFC3339 с обязательной зоной (`Z`/`+00:00`/`+03:00`). Наивное время отвергается. Внутри сервера используется UTC.
- На каждом POST/PATCH обязателен `Idempotency-Key` длиной 8–100 символов. Рекомендуется UUID без дефисов. Ключ относится к bearer identity + HTTP method + path. Тело при повторах должно быть побайтно тем же JSON; иное тело с тем же ключом даёт 409.
- Отдельный `event_id` (8–100 символов, рекомендуется UUID) идентифицирует ACK/progress/result. Он уникален в пределах Worker, а статистический event_id — глобально.
- JSON с неизвестными полями отвергается. Максимальный JSON request — 1 MiB. Пагинация: `limit` 1–200 (50 по умолчанию), `offset>=0`.
- Лимит по умолчанию: 120 запросов/минуту на bearer и общий пятикратный лимит на IP. 429 содержит Retry-After: 60.

Полный токен храните в защищённом хранилище Windows. Не включайте bearer, lease_token и media ticket в логи/скриншоты/ошибки. Один токен соответствует одному физическому Worker; несколько процессов на нём допустимы, но серверный max_parallel и блокировки устройств остаются обязательными.

## 2. Административное provision

Эти методы выполняет администратор, не Worker.

### POST /workers

Заголовки: admin bearer + Idempotency-Key. Ответ 201:

```json
{
  "name": "Worker A",
  "max_parallel": 10,
  "capabilities": {"platforms": ["tiktok", "instagram", "youtube"]}
}
```

```json
{
  "worker": {
    "id": "a0000000-0000-4000-8000-000000000001",
    "created_at": "2026-11-01T09:00:00Z",
    "name": "Worker A",
    "enabled": true,
    "max_parallel": 10,
    "heartbeat_at": null,
    "capabilities": {"platforms": ["tiktok", "instagram", "youtube"]}
  },
  "token": "OPAQUE_WORKER_BEARER"
}
```

Ответ создания можно восстановить тем же Idempotency-Key в течение retention; зашифрованная копия есть только на сервере. `POST /workers/{worker_id}/token` выпускает новый токен и отзывает предыдущие токены данного Worker. `PATCH /workers/{id}` меняет enabled/max_parallel.

### POST /devices

```json
{
  "worker_id": "a0000000-0000-4000-8000-000000000001",
  "external_id": "mumu-01",
  "name": "MuMu emulator 01",
  "enabled": true,
  "capabilities": {"platforms": ["tiktok", "instagram", "youtube"]}
}
```

Ответ 201 — объект с UUID устройства и created_at. `external_id` — стабильный идентификатор, который будущий Worker сопоставляет с конкретным эмулятором. Это не обязательно ADB serial; отображение выбирает реализация Worker. Device уникален в Worker по external_id.

Аккаунт создаётся отдельно `/accounts` с platform/username и обеими привязками worker_id/device_id. Сервер проверяет принадлежность device данному Worker. На одном устройстве допускаются три аккаунта разных платформ. Несколько активных операций на одном устройстве не допускаются даже для разных платформ.

## 3. Подключение и heartbeat

### POST /workers/heartbeat

Вызывайте после запуска и каждые 30 секунд. Новый запрос — новый Idempotency-Key. Не повторяйте старый ключ для нового heartbeat.

```json
{
  "capabilities": {"platforms": ["tiktok", "instagram", "youtube"], "worker_version": "1.0"},
  "devices": [{
    "worker_id": "a0000000-0000-4000-8000-000000000001",
    "external_id": "mumu-01",
    "name": "MuMu emulator 01",
    "enabled": true,
    "capabilities": {"platforms": ["tiktok", "instagram", "youtube"]}
  }]
}
```

Ответ 200:

```json
{
  "worker_id": "a0000000-0000-4000-8000-000000000001",
  "server_time": "2026-11-01T09:00:00Z",
  "heartbeat_interval_seconds": 30,
  "lease_seconds": 180
}
```

Heartbeat upsert устройств: создаёт неизвестные external_id, обновляет name/capabilities известных. Изменять enabled существующего устройства может только admin. Пропущенные в списке устройства автоматически не удаляются и не отключаются; недоступные ресурсы выключаются через admin API. Не запускайте задания на физически недоступном эмуляторе: верните safe_failure до любого действия публикации.

Heartbeat старше 120 секунд делает Worker недоступным для новой выдачи. Сам heartbeat НЕ продлевает аренду отдельного задания. Отключённому Worker heartbeat возвращает 403; новые задания не выдаются. Уже совершившийся результат можно сообщить его действительным токеном и lease.

### GET /devices

Возвращает только собственные устройства, `items`, `limit`, `offset`. Права других Workers не раскрываются.

## 4. Получение задания

### POST /jobs/claim

Тело `{}` или пустое. Bearer + Idempotency-Key. Ответ 200 при отсутствии задания:

```json
{"job": null}
```

При наличии:

```json
{
  "job": {
    "id": "b0000000-0000-4000-8000-000000000001",
    "created_at": "2026-11-01T08:00:00Z",
    "clip_id": "c0000000-0000-4000-8000-000000000001",
    "account_id": "d0000000-0000-4000-8000-000000000001",
    "plan_id": "e0000000-0000-4000-8000-000000000001",
    "local_date": "2026-11-01",
    "slot": 1,
    "scheduled_at": "2026-11-01T09:00:00Z",
    "status": "assigned",
    "worker_id": "a0000000-0000-4000-8000-000000000001",
    "device_id": "f0000000-0000-4000-8000-000000000001",
    "lease_until": "2026-11-01T09:03:00Z",
    "attempts": 1,
    "max_attempts": 3,
    "parameters": {},
    "result": {},
    "updated_at": "2026-11-01T09:00:00Z"
  },
  "lease_token": "OPAQUE_FENCED_LEASE",
  "lease_expires_at": "2026-11-01T09:03:00Z",
  "account": {
    "id": "d0000000-0000-4000-8000-000000000001",
    "platform": "tiktok",
    "username": "example_account",
    "timezone": "Europe/Moscow"
  },
  "device": {
    "id": "f0000000-0000-4000-8000-000000000001",
    "external_id": "mumu-01"
  },
  "content": {
    "id": "c0000000-0000-4000-8000-000000000001",
    "passport": "ENTERTAINMENT-REACTION-000001",
    "title": "Заголовок",
    "description": "Подпись",
    "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "duration_seconds": 32.5
  }
}
```

Поля со значением null могут быть опущены. UUID и SHA в примерах иллюстративны. `parameters` — дополнительные параметры публикации администратора; заголовок и подпись берутся из content, если явное переопределение не согласовано реализацией Worker. Worker обязан проверить платформу/аккаунт в эмуляторе, не публиковать в другой аккаунт при несовпадении.

Claim атомарно блокирует Worker и выбранное задание, использует SKIP LOCKED, проверяет привязки, ресурсы, актуальные фильтры и фактический дневной лимит/интервал. Количество assigned/processing/unknown заданий не превышает max_parallel; устройство имеет не более одного такого задания. Аренда выдаётся на 180 секунд. Истёкшие scheduled задачи из прошлого локального дня при проверке приостанавливаются.

Если запрос claim оборвался, повторите ТОТ ЖЕ Idempotency-Key. Новый ключ может выдать другое задание. При `job=null` сделайте паузу 10–30 секунд с jitter, затем новый claim с новым ключом. Сервер не использует long polling.

## 5. ACK и право на внешнее действие

### POST /jobs/{job_id}/ack

```json
{
  "event_id": "4bf033cc67fd4c0ea64c2c8013266621",
  "lease_token": "OPAQUE_FENCED_LEASE"
}
```

Успех 200:

```json
{
  "job_id": "b0000000-0000-4000-8000-000000000001",
  "status": "processing",
  "lease_expires_at": "2026-11-01T09:03:15Z"
}
```

**Нельзя выполнять действия в соцсети до подтверждённого ACK со status=processing и ещё действующей арендой.** До ACK разрешена подготовка, не имеющая внешнего эффекта (например, загрузка файла). При неопределённом ответе повторите запрос с теми же key/event_id/body. Кэшированный ответ НЕ означает продление аренды: всегда проверяйте возвращённое абсолютное время. Повтор старого ACK после завершения может вернуть published/unknown; это запрещает повторное выполнение.

Рекомендуемый порядок: claim → ACK → media-ticket → download с параллельными progress → проверка файла → проверка аккаунта → публикация → result. При падении после ACK даже во время подготовки сервер выбирает консервативный unknown.

## 6. Продление и прогресс

### POST /jobs/{job_id}/progress

```json
{
  "event_id": "f63807596a7246d6ace45097097a47b0b",
  "lease_token": "OPAQUE_FENCED_LEASE",
  "phase": "uploading",
  "detail": "Video selected in the intended account"
}
```

`phase` — строка 1–80 символов, `detail` — до 4000. Ответ как ACK. Каждое новое событие использует новый event_id и Idempotency-Key. Повтор прежнего события не продлевает lease. Сохраняется журнал прогресса, не содержащий lease_token.

Посылайте progress каждые 30–60 секунд во время загрузки, UI-операций и ожидания ответа платформы. Lease продлевается максимум до `processing_started_at + MAX_PROCESSING_SECONDS` (по умолчанию 1800 секунд). 409 означает, что продление не разрешено. После потери права исполнения не начинайте и не повторяйте публикацию. Если внешнее действие уже могло произойти, сохраните локальные доказательства и передайте unknown/фактический результат.

## 7. Временная загрузка видео

### POST /jobs/{job_id}/media-ticket

Тело LeaseEvent (event_id и lease_token), bearer + Idempotency-Key. Требуется действующий assigned/processing lease. Ответ:

```json
{
  "url": "https://origin.xdoublegroup.online:8443/api/v1/jobs/b0000000-0000-4000-8000-000000000001/video",
  "ticket": "ENCRYPTED_TEMPORARY_TICKET",
  "expires_at_unix": 1793523795,
  "required_headers": ["Authorization", "X-Media-Ticket"]
}
```

Ticket связан с Worker, job и конкретной арендой; живёт не дольше 300 секунд и текущего lease deadline. Получение нового ticket — отдельный новый ключ. При продлении lease старый ticket не получает новый срок автоматически.

### GET /jobs/{job_id}/video

```http
Authorization: Bearer WORKER_TOKEN
X-Media-Ticket: ENCRYPTED_TEMPORARY_TICKET
```

Ответ 200 — поток bytes с Content-Type video/*, Content-Length, Content-Disposition и Cache-Control: no-store. Постоянных копий видео на сервере нет. Скачайте файл во временный каталог Worker, проверьте размер и SHA-256, если он есть в assignment. Удалите временную копию по своей политике после подтверждённого результата. Не передавайте ticket в URL/query string. Range/resume не поддерживается; при обрыве скачайте файл заново с действующим ticket. При большом файле продлевайте lease отдельным progress.

Ошибки: 403 — чужой Worker, недействительный ticket или неактивный lease; 503 — Drive недоступен/не настроен. Ошибка в середине потока может оборвать соединение после HTTP 200; неполный файл нельзя публиковать.

## 8. Результат

### POST /jobs/{job_id}/result

Сначала сохраните итог в локальный durable journal. Затем отправляйте до получения подтверждения с тем же event_id/key/body. Никогда не повторяйте саму публикацию ради повторной отправки результата.

Успешная публикация:

```json
{
  "event_id": "a5673e39f32e4b1682d34f8c900c64a8",
  "lease_token": "OPAQUE_FENCED_LEASE",
  "outcome": "published",
  "external_id": "PLATFORM_POST_ID",
  "url": "https://www.tiktok.com/@example_account/video/PLATFORM_POST_ID",
  "published_at": "2026-11-01T09:01:45Z"
}
```

`external_id` обязателен (1–200), published_at обязателен, timezone-aware и не в будущем; URL необязателен, но при наличии должен начинаться с https://. Ответ:

```json
{
  "job_id": "b0000000-0000-4000-8000-000000000001",
  "status": "published",
  "publication_id": "10000000-0000-4000-8000-000000000001"
}
```

Безопасная ошибка ДО любого действия публикации:

```json
{
  "event_id": "7c51bdd2a33f446f9cbf88bb2398f053",
  "lease_token": "OPAQUE_FENCED_LEASE",
  "outcome": "safe_failure",
  "no_publish_attempted": true,
  "error_code": "drive_unavailable",
  "error_detail": "File download failed before opening the publish action"
}
```

Ответ status=failed, publication_id отсутствует. Никакого автоматического повторного внешнего действия; оператор может безопасно повторить исходное задание. Для `captcha`, `platform_block`, `login_required` аккаунт также получает attention. Рекомендованные коды: `device_unavailable`, `account_mismatch`, `drive_unavailable`, `file_hash_mismatch`, `unsupported_format`, `captcha`, `platform_block`, `login_required`. error_code до 80, error_detail до 4000, без токенов/паролей. CAPTCHA и защиты платформ не обходятся.

Неопределённый результат:

```json
{
  "event_id": "64df79084ee64b268524119599dc0383",
  "lease_token": "OPAQUE_FENCED_LEASE",
  "outcome": "unknown",
  "error_code": "connection_lost_after_submit",
  "error_detail": "Submission may have been accepted; reconciliation required"
}
```

Ответ status=unknown. Не используйте safe_failure после нажатия Publish/Submit или другого внешнего действия, если результат может существовать. Unknown блокирует устройство. Поздний успех с прежним правильным lease может автоматически подтвердить тот же unknown attempt; чужой/заменённый lease отвергается. Поздний safe_failure для unknown запрещён: нужна проверка оператора.

## 9. Проверка состояния и восстановление Worker

- GET `/jobs/{id}` — состояние конкретного собственного задания без lease hash/token.
- GET `/jobs?status=processing&limit=50&offset=0` — собственные выданные задания.
- GET `/publications?account_id=UUID` — публикации собственных привязанных аккаунтов.
- GET `/devices` — собственные устройства.

Сохраняйте локальный журнал job_id, attempt number, lease_token (защищённо), event_id, ключи запросов, момент начала внешнего действия и финальный payload результата. После перезапуска Worker сначала сверяет журнал с сервером. Состояние processing/unknown не даёт разрешения повторять публикацию, если локально она уже могла начаться. Старое выполнение должно быть остановлено до ручного разрешения повтора.

Сервер перезапускается без потери заданий: всё состояние и попытки в PostgreSQL. Lease recovery выполняется каждые 30 секунд:

| Состояние до истечения | Действие сервера |
|---|---|
| assigned, ACK не было | retry_wait + backoff 30×2^attempt секунд, до 300 секунд; после max_attempts — failed |
| processing, ACK был | unknown, автоматического повторного выполнения нет |
| unknown | только поздний однозначный published той же попытки или решение оператора |

Из-за внешней платформы невозможно атомарно объединить запись PostgreSQL и факт публикации. Протокол гарантирует единственную серверную выдачу действующего задания и консервативную остановку при неопределённости; Worker обязан соблюдать запрет на повтор внешнего действия.

## 10. Аналитика

### POST /analytics/snapshots

Bearer + Idempotency-Key. Ответ 201 — сохранённый объект с UUID/created_at.

```json
{
  "publication_id": "10000000-0000-4000-8000-000000000001",
  "event_id": "dbb03bc431fc435184d479a44fbe0a39",
  "measured_at": "2026-11-02T09:01:45Z",
  "views": 1200,
  "likes": 83,
  "comments": 5,
  "shares": 7,
  "saves": null,
  "follower_delta": null,
  "retention": 0.68
}
```

Все метрики необязательны/nullable. Не заменяйте недоступные значения нулём. views/likes/comments/shares/saves — неотрицательные целые. follower_delta — целое со знаком. retention — доля 0..1, не проценты 0..100. measured_at не раньше published_at и не в будущем. Доступ только к публикациям принадлежащих Worker аккаунтов. Снимки за разные моменты сохраняются отдельно. Одинаковый event_id с тем же payload возвращает прежний снимок; с другим — 409. Второй event_id для того же publication/measured_at тоже отвергается DB-ограничением.

Сервер не предписывает недостоверную частоту сбора. Рекомендуется согласовать измерения примерно через 24 и 48 часов, если Worker действительно может получить эти метрики: адаптивная эвристика использует первое доступное измерение в возрасте 24–48 часов. Нельзя отправлять фиктивные значения.

## 11. Жизненный цикл и действия оператора

```text
pending -> scheduled -> assigned -> processing -> published
                         | lease expiry before ACK
                         +-> retry_wait -> assigned
                                      -> failed (max attempts)
processing -> failed (explicit safe_failure)
processing -> unknown (timeout / possible side effect)
unknown -> published (late same-attempt proof / operator)
unknown -> failed (operator proves not published and stops original attempt)
failed -> scheduled (operator safe retry of SAME job)
scheduled/retry_wait/paused -> cancelled/paused/scheduled (operator)
```

`pending` зарезервирован моделью; автоматический планировщик создаёт сразу scheduled. Admin POST `/jobs/{id}/action`: cancel, pause, retry, reschedule, resolve_published, resolve_not_published. Обязательное reason (5–1000). Для retry/reschedule — будущий scheduled_at. Для resolve_published — external_id и published_at; URL необязателен. Published не редактируется. Assigned/processing нельзя отменять через admin action: сначала остановка Worker и истечение аренды/однозначный результат. Перед resolve_not_published оператор обязан остановить прежнюю попытку и проверить аккаунт.

Уникальная пара account/clip сохраняется после cancelled/failed. Повторная регистрация такого же задания не является способом retry. Перенос между компьютерами производится изменением binding аккаунта только после завершения/проверки активных заданий.

## 12. Ошибки HTTP

| HTTP | Значение и поведение клиента |
|---|---|
| 200/201 | Успешная операция; проверяйте JSON status и lease deadline |
| 400 | Нет корректного Idempotency-Key |
| 401 | Bearer отсутствует/отозван/неверен; прекратить запросы и запросить новый токен |
| 403 | Чужой ресурс, недостаточная роль, disabled Worker, недействительный media ticket |
| 404 | Неизвестный UUID ресурса |
| 409 | Просроченный/чужой lease, неподходящее состояние, дубль, конфликт события/идемпотентности или DB relation constraint |
| 413 | JSON request больше 1 MiB |
| 422 | Ошибка схемы/времени/метрики/диапазона; исправить данные, новый ключ для нового payload |
| 429 | Rate limit; пауза Retry-After, сохранение исходного key/body при повторе операции |
| 500 | Серверная ошибка с request_id; не повторять публикацию, повторить запрос результата тем же key |
| 503 | PostgreSQL/Drive недоступен либо scheduler readiness не пройден; backoff с jitter |

Обычный ответ ошибки `{"detail":"..."}`; для validation detail — массив структурированных ошибок Pydantic. Не парсите английские строковые сообщения для бизнес-решений: ориентируйтесь на HTTP, endpoint и состояние задания. Drive-коды: drive_not_configured, drive_invalid_credentials, drive_file_inaccessible, drive_file_unavailable, drive_not_video, drive_invalid_size, drive_hash_mismatch, drive_download_failed, drive_truncated_download, drive_unavailable.

## 13. Mock и приёмка будущего Worker

`scripts/mock_worker.py` выполняет heartbeat, claim, ticket/download (при наличии тестового Drive), ACK, progress, result и snapshot, не обращаясь к соцсетям. Он отказывается работать с production и любым адресом вне loopback. Пример только для отдельно поднятого test API:

```bash
# MOCK_WORKER_TOKEN — токен Worker из тестовой БД
.venv/bin/python scripts/mock_worker.py --base-url http://127.0.0.1:8119 --skip-download
```

Приёмочные сценарии: повтор сетевого запроса; два конкурентных claim; устаревший lease; потеря связи до/после ACK; повтор result; рестарт Worker после Submit; CAPTCHA/login; ошибка SHA; недоступный Drive; неизвестная метрика; попытка чужого Worker читать job/media/statistics. Критические серверные сценарии уже покрыты тестами `tests/` с реальным PostgreSQL и отдельным локальным HTTP-сервером.
