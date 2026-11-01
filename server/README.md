# ShortPost Server — существующий backend

Здесь сохранена серверная реализация из предыдущего развёртывания. Её API/бизнес-логика не менялись для разработки Windows Worker. Production продолжает работать в прежнем каталоге сервера; клонирование монорепозитория ничего в нём не перезапускает.

Base URL: `https://origin.xdoublegroup.online:8443/api/v1`, [Swagger](https://origin.xdoublegroup.online:8443/docs). Worker protocol и фактическая схема находятся в корневом `docs/`.

Разделы: FastAPI API, PostgreSQL metadata/queue, immutable content passport, accounts, leased jobs/attempts, publications/statistics, scheduler, Drive adapter и aiogram admin extension. Google Drive и подключение extension к действующему боту ожидают разрешённых credentials/установки; исходники не означают, что эти внешние подключения включены.

## Новый отдельный сервер

Не выполняйте эти команды поверх существующей production-конфигурации без плана обновления.

```bash
python scripts/init-server.py --public-url https://YOUR_HTTPS_HOST
cd server
docker compose up -d api scheduler
umask 077
set -o noclobber
docker compose exec -T api python -m app.cli bootstrap > secrets/bootstrap.json
```

Перед внешним доступом настройте свой TLS reverse proxy. `.env` и secrets отсутствуют в Git; initializer создаёт новые секреты, не перезаписывая существующие. `secrets/drive.json` изначально `{}`; предоставьте разрешённые credentials, которые читает UID 10001. Telegram профиль по умолчанию выключен. Для нового независимого бота подготовьте отдельный токен и `secrets/telegram.json`; не запускайте второй polling действующей фермы.

Runtime commands: `docker compose ps -a`, `docker compose logs --tail=100 api scheduler`, `docker compose restart api scheduler`. Не удаляйте volumes.

Документы: [DATABASE](docs/DATABASE.md), [OPERATIONS](docs/OPERATIONS.md), [INTEGRATIONS](docs/INTEGRATIONS.md), [TELEGRAM](docs/TELEGRAM.md). Исторические абсолютные пути в deploy-примерах относятся к действующему серверу; для нового размещения измените WorkingDirectory в backup unit.

## Выдача Worker token

`provision_worker.py` — новый отдельный onboarding helper, использующий существующий POST /workers; он не меняет backend. Запускается на сервере, читает private bootstrap JSON и сохраняет только Worker token для передачи оператору Windows. См. `worker/SETUP_WINDOWS.md` в корне проекта.

## Тесты

Нужна отдельная база `shortpost_test`; автопроверка запрещает очищать иную базу. Установите `requirements.lock`, задайте DATABASE_URL/ENCRYPTION_KEY/ENVIRONMENT=test, примените `alembic upgrade head`, затем `python -m pytest`. При предыдущем развёртывании 47 серверных тестов прошли; CI повторяет их и Worker/PostgreSQL contract tests. Реальные публикации в тестах не выполняются.
