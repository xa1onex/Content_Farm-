# Telegram Admin

Выбран совместимый модуль `app/telegram_admin/extension.py`: только aiogram 3 и стандартная библиотека Python. Он подключает middleware к существующему Dispatcher, перехватывает только `/ap`, `/autopost` и callbacks с префиксом `ap:`. Остальные сообщения передаются прежним обработчикам, включая catch-all обработчик видео контент-фермы. Второй getUpdates-процесс не нужен.

## Подключение к существующему боту

Изменение действующей контент-фермы требует разрешения владельца по ТЗ. До разрешения скрипт установки НЕ запускался. Проверяемые материалы:

- `deploy/clipper-extension.patch` — точка подключения после `dp = Dispatcher()`;
- `app/telegram_admin/extension.py` — полный модуль;
- `scripts/install_clipper_extension.py` — установка, закрытый конфиг, backup исходного bot.py и перезапуск только `tg-video-clipper.service`.

Настройки `/etc/shortpost/telegram.json`:

```json
{
  "api_url": "http://127.0.0.1:8108",
  "api_token": "NEW_BACKEND_ADMIN_TOKEN",
  "allow_all": true,
  "admin_ids": []
}
```

По прямому указанию владельца пока разрешены все пользователи бота. Режим применится после установки. Для ограничения: `allow_all=false` и числовые `admin_ids`; перезапустите бота. Доступ возможен только в приватных чатах. Telegram API token существующего бота модуль не копирует: он получает уже созданный объект Bot от Dispatcher.

## Интерфейс

`/ap` открывает меню: статистика, аккаунты, контент, расписание, очередь, Workers, устройства, ошибки, настройки. Списки имеют пагинацию. Кнопка аккаунта предлагает смену active/paused, кнопка задания — безопасную отмену; unknown требует явной проверки результата.

Все изменения показывают метод, URL и JSON и требуют inline-подтверждения. Оно привязано к Telegram user ID, действует 10 минут и исчезает после успешного выполнения. При повторном нажатии после сетевого сбоя используется тот же API Idempotency-Key. После перезапуска бота незавершённые подтверждения отбрасываются.

Команды:

```text
/ap account_add {AccountIn JSON}
/ap account_edit ACCOUNT_UUID {AccountPatch JSON}
/ap worker_add {"name":"Worker A","max_parallel":10}
/ap device_add {DeviceIn JSON}
/ap job_action JOB_UUID {JobAction JSON}
/ap schedule_generate
/ap scheduler_on
/ap scheduler_off
/ap report
/ap events
/ap settings
/ap notify_off
/ap api POST|PATCH /api/v1/PATH {JSON}
```

Пример изменения лимита и окон:

```text
/ap account_edit ACCOUNT_UUID {"daily_limit":5,"windows":[{"start":"08:00","end":"20:00"}],"min_interval_minutes":60}
```

Пример переноса:

```text
/ap job_action JOB_UUID {"action":"reschedule","scheduled_at":"2026-11-02T10:10:00+03:00","reason":"Перенос оператором"}
```

## Уведомления

Открытие `/ap` подписывает конкретный приватный чат; `/ap notify_off` отписывает после подтверждения. События Worker offline, shortage, failed/unknown publication, infeasible schedule и server/scheduler errors сохраняются в PostgreSQL. Outbox создаёт независимую доставку event/chat. Обработчик внутри бота раз в 5 секунд забирает одну доставку с арендой 90 секунд; ошибки повторяются с задержкой, максимум 10 попыток. Старые события до момента подписки не рассылаются. Семантика уведомлений at least once: при падении после принятия сообщения Telegram возможно повторение уведомления.

Живое Telegram-управление и доставка не объявляются проверенными до разрешённой установки и реальной команды пользователя. Юнит-тесты проверяют all-users/allowlist, подтверждение изменений и пропуск прежних сообщений бота.
