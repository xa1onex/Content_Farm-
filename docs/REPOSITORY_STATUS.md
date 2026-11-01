# Состояние репозитория

В репозиторий включены исходники всех трёх компонентов, Windows bootstrap/autostart scripts, зависимости, API spec/OpenAPI, тесты и инструкции.

Рабочая серверная папка и контент-ферма остались в прежнем размещении. `server/app/*.py` совпадают с развёрнутой версией. В server добавлен только отдельный onboarding helper, обращающийся к существующему API; он не запускался для создания фиктивных production Workers.

Windows Worker подготовлен к установке и локальной калибровке. Linux и контрактные проверки прошли; реальные приложения/публикации требуют ПК и разрешения владельца. Состояние отражено в worker/TEST_REPORT.md.

Исключены `.env`, Google OAuth/service-account/client JSON, Telegram/Worker bearers, SQLite/SQL dumps, пользовательские jobs/media, локальные profiles/configs и generated runtime. Выполнена проверка staging по шаблонам ключей и совпадению с известными production-секретами: совпадений нет. Фоновые видео не являются исходным кодом и в public Git не загружаются.

Для Windows нужен Worker token, предоставленный администратором, а не admin/farm/Drive credentials. Существующий Google Drive и Telegram admin extension на сервере не подключались автоматически: прежние согласования не подменяются публикацией кода в GitHub.
