# Контент-ферма в монорепозитории

Этот каталог — snapshot исходников действующей Linux-фермы из `/root/tg-video-clipper`. Работающий экземпляр не редактировался и не перезапускался. Алгоритмы нарезки, Gemini/Whisper models и существующие обработчики сохранены.

На Windows этот компонент запускается через WSL2/Docker Desktop, поскольку исходный bot использует fcntl и Linux fonts. Он не входит в Windows Worker virtualenv. Нативный Windows Worker запускается отдельно из `worker/`.

Для отдельной новой фермы: скопируйте `.env.example` в `.env`, задайте собственные разрешённые BOT_TOKEN/GEMINI_API_KEY/Drive credentials. Создайте `runtime/credentials` и положите туда разрешённый OAuth JSON либо service account. Не используйте одновременно один Telegram polling token в двух процессах.

```powershell
docker compose -f compose.content-farm.yaml up -d --build
```

Это CPU-конфигурация Docker; сборка/загрузка Whisper/Torch занимает время и не относится к лёгкой установке Worker. Новый Docker-образ в ходе этой задачи не запускался с настоящими ключами; действующая серверная ферма продолжает работать отдельно.

Пользовательские видео и фоновый файл из live-проекта не публикуются в Git. Для начала задайте `VIDEO_LAYOUT=portrait` (фон не нужен) либо положите свой разрешённый background.mp4 в `runtime/assets` и используйте split. Docker устанавливает DejaVu font и FFmpeg, необходимые существующему алгоритму.

Исходная инструкция: `README.md`. Передача метаданных в сервер: `server/scripts/register_clipper.py` и `server/docs/INTEGRATIONS.md`. Постоянные Google credentials в Worker не передаются.
