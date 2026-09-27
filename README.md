# Roblox Account Hub

**Локальная панель для нескольких Roblox-аккаунтов.** Просматривайте игры из Continue, состояние аккаунтов, Robux и друзей и запускайте игру от выбранного аккаунта.

**A local dashboard for multiple Roblox accounts.** View Continue games, account status, Robux and friends, then launch a game using the selected account.

[Русский](#русский) · [English](#english)

> Независимый проект, не связанный с Roblox Corporation. Работает на вашем компьютере; для доступа к аккаунту использует его `.ROBLOSECURITY` cookie. Используйте только собственные аккаунты.

## Русский

### Возможности

- Добавление одного аккаунта или импорт нескольких cookies из `.txt`; проверка аккаунтов и удаление дублей.
- Отдельный список Continue для каждого аккаунта с фильтром по PlaceId из `config.json`.
- Статус офлайн / онлайн / в игре с автоматическим обновлением каждые 30 секунд.
- Баланс и ожидающие зачисления Robux, расходы за год и за всю доступную историю.
- Просмотр и обработка входящих заявок в друзья, отправка заявки по username.
- Список друзей в игре, подключение к другу или VIP-серверу.
- Запуск Continue-игры от выбранного аккаунта; тёмная и светлая темы.
- Копирование cookie выбранного аккаунта после отдельного подтверждения.

### Требования и запуск

- Python **3.11+** и доступ к Roblox API.
- Roblox Player для запуска игр.
- Готовые сценарии `install.bat` и `start.bat` предназначены для Windows.

1. Скачайте исходный код и распакуйте архив в отдельную папку.
2. Запустите `install.bat` один раз. Он создаст `.venv` и установит зависимости.
3. Запустите `start.bat`. Откроется `http://127.0.0.1:8765`.
4. Добавьте cookie собственного аккаунта или импортируйте `.txt` с несколькими cookies.

Для ручного запуска в Windows:

```text
py -m pip install -r requirements.txt
py app.py
```

Остановить панель можно сочетанием `Ctrl+C` в окне запуска. При повторном запуске аккаунты добавляются заново. Если порт `8765` занят, закройте запущенный экземпляр панели либо измените `port` в `config.json`.

### Настройка

В `config.json` укажите **PlaceId**, которые нужно искать в Continue:

```json
"target_place_ids": [920587237, 142823291]
```

Это именно PlaceId, а не UniverseId. После изменения файла перезапустите приложение. `launch_method: "browser"` открывает запуск через браузер; `"system"` вызывает зарегистрированный протокол напрямую. Остальные параметры, включая локальный адрес и порт, находятся в том же файле.

### Друзья, VIP и Robux

Для VIP-серверов поддерживаются ссылки `https://www.roblox.com/share?code=...&type=Server` и полные ссылки на игру с `privateServerLinkCode`, `linkCode` или `accessCode`. Подключение к друзьям зависит от их настроек приватности и доступности сервера.

Сумма расходов за всю историю сначала запрашивается как сводка. Если сводка недоступна, приложение просматривает страницы истории покупок; на крупных аккаунтах это может занять время.

### Данные и ограничения

- Панель слушает локальный адрес `127.0.0.1`. Cookies хранятся в оперативной памяти процесса, не записываются в `config.json` или базу данных и исчезают после остановки программы.
- Кнопка копирования выдаёт cookie в браузер и буфер обмена **только после подтверждения**. Не пересылайте cookie: она предоставляет доступ к сессии аккаунта.
- Не добавляйте в репозиторий `.txt` с cookies, даже если файл не совпадает с шаблонами в `.gitignore`.
- Приложение не обрабатывает обновление `.ROBLOSECURITY` из заголовка `Set-Cookie`. Если Roblox обновит cookie и запросы начнут возвращать `401`, импортируйте действующую cookie заново. [Пояснение Roblox](https://devforum.roblox.com/t/upcoming-roblosecurity-cookie-format-changes/4328913).
- Работа зависит от Roblox API и формата ссылок запуска; они могут измениться. Одновременный запуск нескольких экземпляров Roblox Player здесь не настраивается.
- Интерфейс приложения сейчас на русском языке. Инструкция ниже также доступна на английском.

Проверка тестов:

```text
py -m unittest discover -s tests -q
```

Тесты используют подменённые ответы Roblox API; они не проверяют вход и запуск игры на живом аккаунте.

---

## English

> Independent project, not affiliated with Roblox Corporation. It runs on your computer and uses the account's `.ROBLOSECURITY` cookie for access. Use it only with accounts you own.

### Features

- Add one account or import several cookies from a `.txt` file; validate accounts and remove duplicates.
- Separate Continue results for each account, filtered by PlaceId values in `config.json`.
- Offline / online / in-game presence, refreshed automatically every 30 seconds.
- Robux balance, pending Robux, yearly spending and spending over the available purchase history.
- View and respond to incoming friend requests; send a request by username.
- See friends in games and join a friend or a VIP server.
- Launch a Continue game as the selected account; dark and light themes.
- Copy the selected account's cookie after a separate confirmation.

### Requirements and setup

- Python **3.11+** and access to Roblox APIs.
- Roblox Player to launch games.
- The included `install.bat` and `start.bat` scripts are for Windows.

1. Download the source code and extract it into its own folder.
2. Run `install.bat` once. It creates `.venv` and installs dependencies.
3. Run `start.bat`. The dashboard opens at `http://127.0.0.1:8765`.
4. Add a cookie for an account you own, or import a `.txt` file containing multiple cookies.

To start it manually on Windows:

```text
py -m pip install -r requirements.txt
py app.py
```

Press `Ctrl+C` in the launcher window to stop the app. You will need to add accounts again after restarting. If port `8765` is in use, close the running instance or change `port` in `config.json`.

### Configuration

Set the **PlaceId** values to match against Continue in `config.json`:

```json
"target_place_ids": [920587237, 142823291]
```

Use PlaceId values, not UniverseId values. Restart the app after editing the file. `launch_method: "browser"` starts via the browser; `"system"` opens the registered protocol directly. Other options, including the local address and port, are in the same file.

### Friends, VIP servers and Robux

The app accepts `https://www.roblox.com/share?code=...&type=Server` and full game URLs containing `privateServerLinkCode`, `linkCode` or `accessCode`. Joining friends depends on their privacy settings and server availability.

For lifetime spending, the app requests a summary first. If one is unavailable, it reads purchase-history pages, which may take some time for accounts with a long history.

### Data and limitations

- The dashboard binds to `127.0.0.1`. Cookies are kept in process memory, are not saved to `config.json` or a database, and disappear when the program stops.
- The copy button places a cookie in the browser and clipboard **only after confirmation**. Do not share it: it grants access to the account session.
- Do not commit `.txt` files containing cookies, even if their names do not match a `.gitignore` pattern.
- The app does not process `.ROBLOSECURITY` updates from `Set-Cookie`. If Roblox rotates the cookie and requests start returning `401`, import a current cookie again. [Roblox announcement](https://devforum.roblox.com/t/upcoming-roblosecurity-cookie-format-changes/4328913).
- The app relies on Roblox APIs and launcher URL formats, which may change. It does not enable running multiple Roblox Player instances at once.
- The app interface is currently in Russian; these instructions are also available in English.

Run the tests with:

```text
py -m unittest discover -s tests -q
```

Tests use mocked Roblox API responses; they do not verify live account access or game launching.
