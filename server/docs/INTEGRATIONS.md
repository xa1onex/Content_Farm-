# Интеграции

## Google Drive

Адаптер поддерживает service account и OAuth authorized-user JSON. Сервер обращается только к фиксированному Google Drive v3 URL; произвольные внешние URL не используются. Worker не получает Google credentials, refresh token или прямой credential-bearing URL.

Для service account: передайте доступ на чтение к папке/Shared Drive нужному principal, сохраните JSON в `secrets/drive.json`, задайте `DRIVE_AUTH_TYPE=service_account`. Для обычного My Drive подходит OAuth владельца с refresh token и доступом к файлам; `DRIVE_AUTH_TYPE=oauth`. На текущем сервере OAuth-файл контент-фермы найден, но не копировался: автоматическая проверка требует отдельного разрешения на его повторное использование.

Конфигурация подключается read-only Compose secret. Процесс UID 10001 должен читать файл; пример после получения разрешённого файла:

```bash
chown 10001:10001 secrets/drive.json
chmod 0400 secrets/drive.json
docker compose up -d --force-recreate api scheduler
```

Авторизованная сессия обновляет access token в памяти, не изменяет существующий credential-файл контент-фермы. Для OAuth со сменой refresh token оператор обновляет отдельную копию секрета. `POST /content/{id}/verify` проверяет тип video, доступность, canDownload, отсутствие удаления, размер до 2 GiB и SHA-256. Если Drive не возвращает SHA-256, сервер вычисляет его потоковым чтением; отдельная постоянная копия файла не создаётся. Для больших файлов HTTP-клиент регистрации должен иметь достаточный timeout и повторять исходный Idempotency-Key при неопределённом ответе.

Сбой verify возвращает HTTP 200 с `verified=false`, кодом ошибки и сохранённым status=unavailable. GET информации/загрузка при сбое возвращают 503. После исправления используйте НОВЫЙ Idempotency-Key для новой проверки; старый ключ вернёт сохранённый исходный результат.

## REST для контент-фермы

Все запросы используют `Authorization: Bearer FARM_TOKEN` и на мутациях уникальный `Idempotency-Key`. Farm не может создавать Workers, менять аккаунты или получать admin API.

1. Admin настраивает справочники `/taxonomy/categories`, `/taxonomy/topics`, `/taxonomy/formats` (коды UPPER_SNAKE_CASE).
2. Farm получает справочники и создаёт исходник `/sources` с собственным external_id.
3. Создаёт `/content` либо `/content/batch` (1–100 записей, атомарно). Новый клип всегда draft.
4. Проверяет `/content/{id}/verify`; при успехе ready, иначе unavailable.
5. PATCH `/content/{id}` меняет метаданные. Passport, Drive identity и известный SHA-256 неизменяемы.

Пример регистрации (UUID справочников замените реальными):

```json
{
  "source_id": "9abf6c73-cb98-4be0-a420-337f2660f6ce",
  "drive_file_id": "1RealDriveFileId",
  "title": "Название ролика",
  "description": "Подпись к публикации",
  "category_id": "d4432276-abba-42cb-97b9-ad0218f75713",
  "format_id": "d947a070-ec4c-4a67-9dd7-f869cbde844d",
  "duration_seconds": 32.5,
  "platforms": ["tiktok", "instagram", "youtube"],
  "extra": {"farm_job_id": "job-123", "clip_index": 1}
}
```

Passport можно не передавать: сервер создаёт CATEGORY-FORMAT-000001 через PostgreSQL sequence и не меняет его при правке категории. Внешний passport, если задан, тоже проверяется на уникальность. Повторная регистрация с другим ключом даёт 409 при совпадении Drive File ID, passport или SHA-256. Для сверки доступны GET `/sources?external_id=...` и `/content?drive_file_id=...`.

## Совместимость с tg-video-clipper

`scripts/register_clipper.py` читает только конкретный выбранный job-каталог и использует `state.drive_files[clip.file].id` и `manifest.clips`. Внешний source ID: `clipper:{state.id}`. Ключи запросов детерминированы; повторный запуск находит уже созданные записи. Категория и формат задаются оператором; скрипт не придумывает классификацию.

```bash
# FARM_API_TOKEN передайте через защищённое окружение процесса.
.venv/bin/python scripts/register_clipper.py \
  --api-url https://origin.xdoublegroup.online:8443 \
  --job-dir /root/tg-video-clipper/jobs/SELECTED_JOB \
  --category-id CATEGORY_UUID --format-id FORMAT_UUID --verify
```

Автоматическое добавление вызова в текущую ферму не выполнялось. Для дальнейшей интеграции её обработчик может посылать тот же JSON после успешной архивации; неполная/отсутствующая запись drive_files означает, что клип ещё не готов к регистрации.
