# PROJECT_CONTEXT — Ерунда

## Название проекта

**Ерунда** — Discord-бот для небольшого Discord-сервера «Ерундульки» (примерно 10–50 участников).

## Цель проекта

Ерунда — Discord-бот для небольшого сообщества, который делает сервер более живым, организованным и интерактивным.

Основные системы:

- 🎂 дни рождения
- 📊 статистика и топы
- 🎉 ивенты
- 💬 цитаты
- 🎨 управление ролями (включая персональные и RGB)
- 🗳️ серверная демократия

Не добавлять самостоятельно несогласованные системы (музыка, XP, уровни, достижения, экономика, серверная история, мини-игры, репутация и т.п.).

---

## Status

- [x] Базовая структура бота
- [x] Подключение Discord
- [x] SQLite
- [ ] Дни рождения
- [ ] Статистика
- [ ] Профиль
- [ ] Топы
- [ ] Ивенты
- [ ] Цитаты
- [ ] Управление ролями
- [ ] Пользовательские роли (`/myrole`)
- [ ] RGB-роли
- [ ] Демократия
- [ ] Автовыполнение решений демократии
- [x] `/config` (настройки сервера)
- [x] README и `.env.example`

---

## Структура проекта

```text
ErundaBot/
├── bot/
│   ├── __init__.py
│   ├── bot.py
│   ├── cogs/
│   │   ├── __init__.py
│   │   └── config.py
│   ├── database/
│   │   ├── __init__.py
│   │   ├── database.py
│   │   └── models.py
│   ├── services/
│   │   ├── __init__.py
│   │   └── config_service.py
│   ├── views/
│   │   ├── __init__.py
│   │   └── config_views.py
│   ├── tasks/
│   │   ├── __init__.py
│   │   └── background.py
│   └── utils/
│       ├── __init__.py
│       ├── embeds.py
│       ├── permissions.py
│       ├── timezones.py
│       └── formatting.py
├── data/
│   └── .gitkeep
├── main.py
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
└── PROJECT_CONTEXT.md
```

---

## Архитектура

### Слои

| Слой | Ответственность |
|------|-----------------|
| `main.py` | Точка входа, `.env`, логирование, `bot.run` |
| `bot/bot.py` | `ErundaBot`, intents, load cogs, sync commands, lifecycle |
| `bot/cogs/*` | Slash Commands, listeners, Discord UI wiring |
| `bot/views/*` | Buttons / Selects / Modals |
| `bot/services/*` | Бизнес-логика |
| `bot/database/*` | SQLite schema + queries |
| `bot/tasks/*` | Централизованные background loops |
| `bot/utils/*` | Embeds, permissions, timezone, форматирование |

### Принципы

- Не смешивать Discord-команды, бизнес-логику и DB в одном файле.
- Фоновые задачи запускаются один раз за lifecycle (`BackgroundTasks`, guard от дублей).
- Серверные данные привязаны к `guild_id`.
- Токен только в `.env`.
- Админ-действия проверяют permissions.

### Background tasks

Сейчас: каркас `BackgroundTasks` (start/stop без loops).

Планируются: birthday notifier, event scheduler, proposal closer, RGB manager, voice recovery.

---

## База данных

SQLite через `aiosqlite`, путь `DATABASE_PATH` (по умолчанию `./data/erunda.db`).

Схема создаётся в `Database.connect()` (`SCHEMA` в `database.py`).

### Таблицы

| Таблица | Назначение |
|--------|------------|
| `guilds` | Настройки сервера (`/config`) |
| `users` | Кэш участников |
| `birthdays` | Дни рождения (year nullable) |
| `message_statistics` | Сообщения по user/channel/date |
| `voice_sessions` | Voice-сессии (open = `ended_at IS NULL`) |
| `reaction_statistics` | Полученные реакции по user/date |
| `events` | Мероприятия |
| `event_participants` | Участники ивентов |
| `quotes` | Цитаты + JSON snapshot реакций |
| `custom_roles` | Managed/personal роли + RGB state |
| `proposals` | Предложения / голосования |
| `proposal_votes` | Голоса (один на user, можно менять) |

RGB хранится в `custom_roles` (отдельная `role_animations` не создана).

### `guilds` — основные поля

`timezone`, каналы (`birthday`/`events`/`proposals`/`quotes`), флаги (`statistics_enabled`, `personal_roles_enabled`, `auto_execute_proposals`, `rgb_enabled`), `birthday_announce_time`, `birthday_reminder_days`, `event_reminder_minutes`, `rgb_interval_seconds` (≥10), `proposal_duration_hours`, `proposal_quorum`, `proposal_pass_ratio`.

---

## Discord-команды

Реализовано:

```text
/config
```

План (ещё нет):

```text
/birthday set|remove|list|next
/profile [user]
/top
/event create|list|info|join|leave|cancel
/quote add|random|list|user
(Message Context Menu) Add quote
/role create|edit|delete
/myrole
/proposal create|list|info|cancel
```

---

## Discord Intents

Включены в `ErundaBot`:

- `guilds`, `members` (privileged), `guild_messages`, `message_content` (privileged), `guild_reactions`, `voice_states`

---

## Discord Permissions

См. README: View Channels, Send Messages, Embed Links, Attach Files, Read Message History, Manage Roles, Manage Channels, Use Application Commands.

---

## Реализованные функции

### Ядро

Статус: реализовано

- структура модулей;
- `ErundaBot` + загрузка cogs + global command sync;
- SQLite schema для всех планируемых таблиц;
- `BackgroundTasks` lifecycle stub;
- ensure guild on ready / guild_join.

### `/config`

Статус: реализовано (базовый UX)

Реализовано:

- ephemeral overview embed;
- Select → каналы / флаги / timezone / время / голосования / RGB;
- ChannelSelect + Modals;
- права: Administrator или Manage Server;
- валидация timezone / HH:MM / числовых границ.

### Остальные системы

Статус: не начато (дни рождения, статистика, топы, ивенты, цитаты, роли, RGB, демократия).

---

## Current Task

Ядро и `/config` готовы.

Следующий шаг: система дней рождения (`/birthday` + автопоздравления).

---

## TODO

- [ ] Система дней рождения
- [ ] Статистика сообщений / voice / реакций
- [ ] `/profile` и `/top`
- [ ] Система ивентов
- [ ] Система цитат (+ context menu)
- [ ] Управление ролями + `/myrole`
- [ ] RGB manager
- [ ] Демократия + optional auto-actions
- [ ] Проверка restart-recovery (voice, events, RGB, proposals)

---

## Known Issues

- Global `tree.sync()` может задерживать появление slash-команд до нескольких минут; при необходимости позже добавить guild-sync для dev.

---

## Technical Decisions

- Python 3.12+ (локально 3.14.2)
- discord.py 2.x
- aiosqlite + schema bootstrap (без отдельного migration framework пока)
- `tzdata` в зависимостях — нужен для `zoneinfo` на Windows
- Slash Commands + UI components
- бизнес-логика в `services/`, UI в `cogs/` + `views/`
- фоновые задачи через `BackgroundTasks` (один старт)
- RGB interval минимум 10 секунд
- Personal roles: одна на пользователя на гильдию (при реализации)
- Auto-execute proposals: default off
- Дефолты голосований: 24ч, кворум 3, pass ratio 0.5
- Git remote: `https://github.com/K0DDO/ErundaBot.git`, ветка `main`
- Commit messages: English, lowercase, no trailing period

### Открытые решения

1. Точная формула «общей активности» для `/top`.
2. Набор `action_type` для автовыполнения предложений.
3. Дефолтная скорость `/myrole` RGB относительно `rgb_interval_seconds`.

---

## Зависимости

| Пакет | Назначение |
|-------|------------|
| `discord.py` | Discord API |
| `aiosqlite` | Async SQLite |
| `python-dotenv` | `.env` |
| `tzdata` | IANA timezones на Windows |

---

## Правила разработки

- Не хранить секреты в коде.
- Не смешивать DB-логику и Discord UI.
- Не создавать бесконтрольные background tasks.
- Учитывать Discord API rate limits.
- Админ-действия проверяют permissions + иерархию ролей.
- Значимые изменения отражаются в `PROJECT_CONTEXT.md` в той же операции.
- Не добавлять несогласованные крупные функции.
- При неоднозначности UX/архитектуры — сначала предложить варианты.
- Код — источник истины при расхождении с этим файлом.

---

## Recent Changes

- 2026-08-10 — базовая структура, SQLite schema, `ErundaBot`, `/config`, README, `.env.example`
- 2026-08-10 — добавлен `tzdata` для timezone на Windows
- 2026-08-10 — создан `PROJECT_CONTEXT.md`, git init + remote

---

## Правило восстановления контекста

Если начинается новый контекст:

1. **Первым делом прочитай этот файл.**
2. Сверь структуру проекта с разделом «Структура».
3. Посмотри Status / Current Task / TODO / Known Issues / Recent Changes.
4. Только после этого продолжай разработку.
5. При противоречии код vs этот файл — верь коду, обнови этот файл.
