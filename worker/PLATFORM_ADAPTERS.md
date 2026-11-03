# Платформенные адаптеры и профили

Реализации `platforms/tiktok.py`, `instagram.py`, `youtube.py` используют общий PlatformPublisher. Это исполняемая логика с versioned configuration, а не обещание универсальных селекторов для любой версии приложения. На доступном Linux-хосте реальные приложения не проверялись. Bundled profiles — **draft examples**, navigation_verified=false. Без локально проверенного профиля live preflight откажется запускаться.

## Калибровка на одном эмуляторе

1. Скопируйте PLATFORM.example.json в PLATFORM.local.json, укажите его в `[devices.profiles]`. Узнайте точный Android package, app_version (`adb shell dumpsys package PACKAGE`) и locale. Пакеты в примерах ориентировочные; выберите фактически установленный вариант.
2. При остановленном Worker запустите `inspect-ui --external-id mumu-01` или `record-locator --external-id mumu-01 --output selector.local.json`. Запись locator **ничего не нажимает**.
3. Заполните account_steps и account locator, который возвращает настоящий handle, не display name. Адаптер сравнивает его с assignment.account.username. UI ambiguous/missing → остановка.
4. Запишите upload_steps: native create/upload/picker и точный файл `{filename}`. Worker помещает видео в отдельную папку `/sdcard/Movies/ShortPost/<job_uuid>/<job_uuid>.mp4`. Если приложение скрывает имена файлов, выберите альбом `{media_album}` (UUID задания) и единственную миниатюру внутри него. Несколько совпадений останавливают навигацию. metadata_steps используют source=caption/title/filename/media_album. Для picker не используйте случайный первый thumbnail из общих «Недавних».
5. submit — отдельный финальный selector. Его нельзя повторно включить как click в account/upload/metadata/verification/metrics/parameter flows; валидация профиля это запрещает.
6. confirmation и verification_steps должны приводить к **именно новому** опубликованному ролику. caption_proof/author_proof проверяют содержимое и аккаунт. Для YouTube проверяется title; для TikTok/Instagram caption. published_url возвращает настоящую ссылку; альтернативно url_source=clipboard после контролируемого Copy link. Старый известный URL/clipboard запрещён как доказательство новой публикации.
7. errors содержит известные селекторы captcha/platform_block/login_required. Появление этих элементов вызывает остановку и код операторского вмешательства. Неизвестный UI не нажимается наугад.
8. max_duration_seconds, max_caption_chars и max_title_chars задаются под реально проверенную версию/тип публикации. Worker не угадывает меняющиеся лимиты приложений.

JSON schema: `profiles/schema.json`. Все параметры профиля типизированы, неизвестные поля отвергаются.

## Безопасная навигация — уровень B

```powershell
.\.venv-worker\Scripts\shortpost-worker.exe test-navigation --external-id mumu-01 --platform tiktok --username ACTUAL_HANDLE --video C:\Private\test.mp4
```

Команда не claim-ит задания, не отправляет result/statistics и не вызывает submit. Она проверяет реальный аккаунт, staging, upload/metadata flow, находит финальную кнопку и останавливается. Только успешный проход записывает navigation_verified=true. Останется проверить подтверждение/URL на реальной согласованной публикации уровня C; это не считается пройденным от одного navigation test.

## Параметры публикации

- TikTok: caption, visibility public/friends/private, allow_comments boolean.
- Instagram: caption, allow_comments boolean.
- YouTube: title, caption/description, **обязательные** visibility public/unlisted/private и made_for_kids boolean.

Неизвестный параметр отклоняется. Для опций кроме текста profile.parameter_steps должен содержать UI flow именно для требуемого значения, например visibility.private и made_for_kids.false. Нельзя молча использовать предыдущее UI-значение. default_parameters — явные настройки владельца профиля; job.parameters имеют приоритет. YouTube example использует private и not-made-for-kids только как пример конфигурации; владелец обязан выбрать верную аудиторию собственного контента.

## Подтверждение и аналитика

Успех — не факт нажатия. Нужны confirmation, proof автора/текста и настоящий platform URL. TikTok извлекает numeric video ID и сверяет handle в URL; Instagram — Reel shortcode; YouTube — настоящий 11-символьный video ID. Случайный UUID не принимается. Неоднозначный результат после Submit — unknown, новая попытка Submit запрещена.

metrics_steps возвращаются к конкретному URL, metric_post_proof проверяет внешний ID. Доступные selectors метрик читают точные целые значения; сокращённые/неоднозначные `1.2K`, `1,2 тыс.` не превращаются в придуманные числа. Retention принимается как видимый точный процент; недоступные значения не отправляются. Без настроенных metrics selectors Worker сообщает metrics_unavailable и повторяет измерение позже, не отправляя нули.

Изменение версии/языка приложения требует повторной калибровки. Не редактируйте профили на ходу при активной публикации. Все три платформы остаются **не принятыми на настоящем приложении** до раздельных тестов C с разрешения владельца.
