# Реляционная модель PostgreSQL

Начальная миграция фиксирует структуру в Alembic (не импортирует модели при применении). Вторая миграция создаёт sequence и DB triggers для неизменяемой идентичности. UUID — внутренние PK; UTC timestamptz — время событий; `local_date` хранит дату плана в зоне аккаунта.

| Таблицы | Назначение и ограничения |
|---|---|
| sources | Уникальный внешний ID исходника; категория/тематика; связанные clips |
| categories, topics, formats | Настраиваемые справочники с уникальным code |
| clips | Постоянный уникальный passport, уникальные Drive File ID и известный SHA-256; source FK, taxonomy FK, readiness, JSONB метаданные |
| workers | Включение, max_parallel, heartbeat, capabilities |
| devices | Уникальная пара worker/external_id; составная уникальность id/worker |
| accounts | Платформа/username уникальны; FK Worker/device; timezone, windows, limits; partial unique device/platform для неудалённых аккаунтов |
| account_categories/topics/formats | Нормализованные фильтры, составные PK |
| schedule_plans | Единственный план account/local_date; сохранённые часы и настройки |
| jobs | UNIQUE(account_id,clip_id) навсегда; UNIQUE(account_id,local_date,slot); lease, attempts, result |
| attempts | UNIQUE(job_id,number), история стадий и ошибок |
| job_events | UNIQUE(worker_id,event_id), payload без lease token |
| publications | UNIQUE(job_id), UNIQUE(account_id,clip_id), UNIQUE(account_id,external_id); составной FK обеспечивает соответствие job/account/clip |
| snapshots | UNIQUE(event_id), UNIQUE(publication_id,measured_at); nullable метрики; история измерений |
| api_tokens | Уникальный SHA-256 bearer, роль и worker FK, отзыв |
| idempotency | Составной PK scope/key, request digest и зашифрованный ответ; retention 30 дней |
| rate_buckets | Атомарные PostgreSQL upsert счётчики, минутные окна |
| settings | Состояние scheduler и heartbeat |
| events | Системные предупреждения и ошибки, dedup_key |
| audit | Кто и какой API-вызов выполнил; основания операторского решения по заданиям |
| telegram_subscribers | Подписка конкретного приватного чата на уведомления |
| notification_deliveries | Outbox event/chat, попытки, backoff, результат доставки |

На FK/состояниях/планируемом времени и аналитических временах созданы индексы. CHECK-ограничения ограничивают платформы, состояния, положительные длительности, допустимые метрики и количество параллельных операций.

DB trigger запрещает менять passport, Drive File ID и уже известный SHA-256. Категорию, тематику, формат и описание менять можно — паспорт остаётся прежним. Job trigger запрещает менять account/clip. Известный SHA-256 уникален глобально, поэтому одинаковый файл с другим Drive ID нельзя повторно зарегистрировать с тем же хэшем. До проверки неизвестного хэша гарантируется дедупликация по passport и Drive ID; клип не получает ready до успешной проверки.

Частичный уникальный индекс `busy_device` разрешает лишь одно assigned/processing/unknown задание на устройство. Unknown намеренно удерживает устройство до проверки. PostgreSQL row locks сериализуют операции одного Worker и аккаунта; SKIP LOCKED исключает одновременную выдачу задания. Последнее слово остаётся за DB-ограничениями, а не за Python-проверками.

Удаление исторических записей через API не предусмотрено. Аккаунты удаляются мягко. Повтор безопасного задания меняет его состояние и увеличивает номер попытки, не создавая новую пару account/clip.
