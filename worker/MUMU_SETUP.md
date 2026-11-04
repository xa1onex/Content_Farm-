# MuMu, ADB и Appium

Отдельный SOCKS5 для всего Android: [схема изоляции и диагностика сети](NETWORK_ISOLATION.md). Пока реализован только сбор отчёта; включение моста и маршрутизация через прокси требуют проверки на ПК.

1. Установите подходящую MuMu с официального сайта и войдите в нужные социальные приложения вручную. Не обходите CAPTCHA, логин или блокировки. Начните с одного экземпляра.
2. Используйте установленный ADB (либо официальный Android Platform Tools). Worker не выполняет `adb kill-server`, wipe/clear-data, удаление/клонирование эмуляторов или APK.
3. Найдите путь MuMuManager.exe через `doctor`, Windows registry или каталог установки. Поддерживается JSON interface `info -v all` и `control -v INDEX launch`. У разных веток MuMu CLI отличается: если ваш интерфейс не поддерживает эти команды, не угадывайте формат — используйте явный `adb_endpoint` и запуск экземпляра вручную до проверки его CLI.
4. Автоматический поиск читает uninstall registry и известные пути внутри установочных директорий. Он не сканирует весь диск и не предполагает фиксированные порты.
5. `bind` получает `settings get secure android_id` и фиксирует external_id/index/identity. При работе endpoint повторно читается из MuMu JSON; изменение порта допустимо, изменение Android ID останавливает выполнение.
6. Для нескольких устройств каждый systemPort UiAutomator2 уникален. Appium работает на loopback, sessions содержат точный udid, noReset=true и fullReset=false. Данные аккаунтов не очищаются.
7. Видео отправляется только в `/sdcard/Movies/ShortPost/JOB_UUID.mp4`, проверяется размер и выполняется media-scan broadcast. Профиль picker должен выбирать точное уникальное имя файла, не «первое видео».

`record-locator` показывает только уникальные resource-id/accessibility-id из реального UI. `inspect-ui` сохраняет XML и screenshot в приватный каталог. Для XPath при необходимости используйте Appium Inspector, подтверждая однозначность. Жёсткие экранные координаты в адаптерах не используются.

Уровень B на реальном ПК: обнаружение → запуск одного экземпляра → ADB identity → Appium → account proof → staging → navigation перед Publish. Уровень D (2–3, затем 10) допускается после успешной проверки одного устройства; ни один из этих уровней не подменяется unit-тестами.

Источники: [MuMu ADB manual](https://www.mumuplayer.com/help/win/developers-essentials-manual.html), [UiAutomator2 compatibility](https://github.com/appium/appium-uiautomator2-driver), [Appium Python Client](https://github.com/appium/python-client).
