# Ерунда

Discord-бот для небольшого сервера «Ерундульки».

Делает сервер живее: дни рождения, статистика и топы, ивенты, цитаты, роли (включая RGB) и серверная демократия.

## Требования

- Python 3.12+
- Discord Application с ботом

## Установка

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
```

Открой `.env` и укажи токен:

```env
DISCORD_TOKEN=your_token_here
DATABASE_PATH=./data/erunda.db
DEFAULT_TIMEZONE=Europe/Moscow
```

## Создание Discord Application

1. Открой [Discord Developer Portal](https://discord.com/developers/applications).
2. **New Application** → имя, например `Ерунда`.
3. Вкладка **Bot** → **Add Bot**.
4. Скопируй токен в `.env` (`DISCORD_TOKEN`).
5. Отключи **Public Bot**, если бот только для вашего сервера (по желанию).

## Intents

В Developer Portal → Bot → Privileged Gateway Intents включи:

- **Server Members Intent**
- **Message Content Intent**

Обычные intents (guilds, messages, reactions, voice states) бот запрашивает в коде.

## Permissions

При приглашении бота нужны как минимум:

| Permission | Зачем |
|---|---|
| View Channels | Читать каналы |
| Send Messages | Ответы и уведомления |
| Embed Links | Embed-сообщения |
| Attach Files | Вложения (иконки ролей и т.п.) |
| Read Message History | Контекст / цитаты |
| Manage Roles | Роли и RGB |
| Manage Channels | Автодействия демократии |
| Use Application Commands | Slash Commands |

Роль бота должна быть **выше** ролей, которыми он управляет.

Пример invite URL (подставь `CLIENT_ID`):

```text
https://discord.com/oauth2/authorize?client_id=CLIENT_ID&permissions=268823632&scope=bot%20applications.commands
```

`268823632` ≈ View Channels + Send Messages + Embed Links + Attach Files + Read Message History + Manage Roles + Manage Channels.

## Запуск

```bash
python main.py
```

После старта команды синхронизируются глобально. Первое появление `/config` в Discord может занять до нескольких минут.

## Первая настройка

На сервере выполни `/config` (нужны Administrator или Manage Server):

- каналы дней рождения / ивентов / голосований / цитат;
- timezone;
- флаги статистики, personal roles, RGB, auto-execute;
- время уведомлений и правила голосований.

## Структура

См. `PROJECT_CONTEXT.md` — актуальная архитектура, статус и TODO.

## Лицензия

Приватный бот для сервера «Ерундульки».
