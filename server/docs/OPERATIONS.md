# Эксплуатация

## Состояние и логи

```bash
docker compose ps -a
docker compose logs --tail=100 api scheduler
systemctl status shortpost-backup.timer
systemctl list-timers shortpost-backup.timer
curl --fail https://origin.xdoublegroup.online:8443/api/v1/health/ready
```

`/health` проверяет PostgreSQL. `/health/ready` дополнительно требует heartbeat планировщика не старше 120 секунд. JSON-логи API содержат request ID, HTTP status и длительность; секретные заголовки и тела запросов не логируются. Scheduler пишет JSON-события тиков и коды ошибок. API `/events`, `/audit`, `/jobs/{id}/history` дают историю без доступа к консоли.

Контейнеры имеют ограничения памяти/CPU, `no-new-privileges`, read-only FS и tmpfs. API/worker-сервисы исполняются от UID 10001. PostgreSQL и API опубликованы только на loopback (55432 и 8108); наружу выходит Nginx TLS 8443. Установленный сертификат обслуживается существующим certbot; его срок проверяется штатной конфигурацией сервера. Выбор отдельного порта позволил не менять существующие server blocks.

## Обновление и миграции

```bash
bash scripts/backup.sh
docker compose build api
docker compose stop api scheduler
docker compose run --rm migrate
docker compose up -d api scheduler
curl --fail https://origin.xdoublegroup.online:8443/api/v1/health/ready
```

При неуспешной миграции не запускайте новую версию приложения; исследуйте причину, сохраните backup. Не применяйте downgrade на production без отдельной проверки потери данных. Начальный downgrade удаляет новую схему и не является безопасным способом обычного обновления.

Ревизии: `45623cf05e54` — фиксированная схема, `002_identity_guards` — последовательность паспортов и триггеры. Новые ревизии создавайте через Alembic autogenerate против тестовой БД и проверяйте SQL. Alembic upgrade повторно безопасен.

## Резервное копирование и восстановление

```bash
bash scripts/backup.sh
# Восстановление только в НОВУЮ отдельную БД:
docker compose exec -T db createdb -U shortpost_owner shortpost_restorecheck_new
docker compose exec -T db pg_restore -U shortpost_owner -d shortpost_restorecheck_new --no-owner --exit-on-error < backups/CHOSEN.dump
```

Скрипт пишет custom-format pg_dump через временный файл и переименовывает только после проверки pg_restore --list. Таймер запускает его ежедневно в 03:15 Europe/Moscow с задержкой до 5 минут; пропущенный запуск выполняется после включения сервера. Автоудаления нет. Настройте внешнюю закрытую копию и retention под требования владельца. Отдельно сохраните `.env` (в том числе encryption key), Drive credentials и bootstrap-токены в защищённом месте. Эти файлы исключены из исходного архива.

Проверка восстановления выполняется в отдельной БД; рабочая база при этом не заменяется. Потеря Fernet-ключа сделает сохранённые идемпотентные ответы нечитаемыми. Обычные метаданные не зашифрованы этим ключом.

## Потеря Worker и неизвестный результат

Heartbeat не продлевает lease задания. После ack Worker должен посылать progress каждые 30–60 секунд. Без ack истёкшая аренда переводится в retry_wait с backoff (до трёх автоматических попыток). После ack — только unknown. Unknown не возвращается в очередь автоматически и блокирует устройство.

Оператор проверяет соцаккаунт, затем выполняет resolve_published с внешним ID/временем либо resolve_not_published с основанием. Только после второго действия разрешён retry исходного задания. CAPTCHA, блокировка и необходимость логина возвращаются как отдельные коды, переводят аккаунт в attention и не обходятся.

## Telegram-режимы

На текущем сервере не запускать `docker compose --profile telegram up` с токеном существующей контент-фермы: она уже использует polling. Предпочтительный вариант — рассмотренный модуль внутри её процесса. Отдельный профиль допускается только для отдельного токена; ему нужен JSON-конфиг `secrets/telegram.json` с api_url/api_token/allow_all/admin_ids. Compose монтирует его read-only в путь `TELEGRAM_EXTENSION_CONFIG`. На текущем сервере этот конфиг подготовлен для нового backend-токена; старый Telegram-токен не использовался. На новом сервере создайте JSON после bootstrap (api_url внутри Compose: `http://api:8000`). Из коробки этот профиль выключен, поскольку отдельный бот не запрашивался.

## Ограничения проверки

Нет реальных социальных аккаунтов/Windows Worker, и реальные публикации не выполнялись. Drive и живые Telegram updates требуют разрешённого подключения. Тесты не являются нагрузочным тестированием 30 эмуляторов. Мониторинг доступности вне сервера и внешнее хранилище backup требуют отдельного назначения/сервиса.
