# PROJECT_CONTEXT — Ерунда

## Название проекта

**Ерунда** — Discord-бот для небольшого Discord-сервера «Ерундульки» (примерно 10–50 участников).

## Цель проекта

Ерунда — Discord-бот для небольшого сообщества, который делает сервер более живым, организованным и интерактивным.

Основные системы: дни рождения, статистика и топы, ивенты, цитаты, роли (включая RGB), серверная демократия.

Не добавлять несогласованные системы (музыка, XP, уровни, достижения, экономика, история, мини-игры, репутация и т.п.).

---

## Status

- [x] Базовая структура бота
- [x] Подключение Discord
- [x] SQLite
- [x] Дни рождения
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
│   ├── bot.py
│   ├── cogs/
│   │   ├── config.py
│   │   └── birthdays.py
│   ├── database/
│   │   ├── database.py
│   │   └── models.py
│   ├── services/
│   │   ├── config_service.py
│   │   └── birthday_service.py
│   ├── views/
│   │   ├── config_views.py
│   │   └── birthday_views.py
│   ├── tasks/
│   │   └── background.py
│   └── utils/
│       ├── embeds.py
│       ├── permissions.py
│       ├── timezones.py
│       └── formatting.py
├── data/
├── main.py
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
└── PROJECT_CONTEXT.md
```

---

## Архитектура

Слои: `main` → `ErundaBot` → `cogs/views` → `services` → `database`; фоновые задачи в `BackgroundTasks`.

### Background tasks

- `birthday_loop` (каждую минуту): поздравления и напоминания по timezone гильдии; дедуп через `birthday_notifications`.

---

## База данных

Таблицы: `guilds`, `users`, `birthdays`, `birthday_notifications`, `message_statistics`, `voice_sessions`, `reaction_statistics`, `events`, `event_participants`, `quotes`, `custom_roles`, `proposals`, `proposal_votes`.

`birthday_notifications`: PK `(guild_id, user_id, event_date, kind)` — `announce` / `reminder`.

RGB state в `custom_roles` (отдельной `role_animations` нет).

---

## Discord-команды

```text
/config
/birthday set
/birthday remove
/birthday list
/birthday next
```

План: `/profile`, `/top`, `/event *`, `/quote *`, context menu Add quote, `/role *`, `/myrole`, `/proposal *`.

---

## Intents / Permissions

Intents: guilds, members, guild_messages, message_content, guild_reactions, voice_states.

Permissions: см. README.

---

## Реализованные функции

### Ядро + `/config`

Статус: реализовано

### Дни рождения

Статус: реализовано

- Modal `/birthday set` (день, месяц, год опционально)
- remove / list (сортировка по ближайшей дате) / next
- автопоздравление в канал из `/config` в `birthday_announce_time`
- напоминание за `birthday_reminder_days` дней
- timezone гильдии; Feb 29 → Feb 28 в невисокосный год
- возраст в поздравлении, если указан год
- дедуп уведомлений в БД (устойчиво к restart)

### Остальные системы

Не начаты.

---

## Current Task

Дни рождения завершены.

Следующий шаг: статистика (сообщения, voice, реакции) → затем `/profile` и `/top`.

---

## TODO

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

- Global `tree.sync()` может задерживать появление slash-команд до нескольких минут.

---

## Technical Decisions

- Python 3.12+, discord.py 2.x, aiosqlite, python-dotenv, tzdata (Windows zoneinfo)
- Birthday loop: 1 мин; сравнение HH:MM в timezone гильдии
- RGB interval ≥ 10 сек (ещё не реализован loop)
- Personal roles: 1 на user/guild (позже)
- Auto-execute proposals: default off
- Vote defaults: 24h / quorum 3 / ratio 0.5
- Commit messages: English, lowercase, no trailing period

### Открытые решения

1. Формула «общей активности» для `/top`.
2. Набор `action_type` для автовыполнения предложений.
3. Дефолтная скорость `/myrole` RGB.

---

## Зависимости

`discord.py`, `aiosqlite`, `python-dotenv`, `tzdata`

---

## Правила разработки

- Не хранить секреты в коде.
- Не смешивать DB и Discord UI.
- Один набор background tasks на lifecycle.
- Админ-действия → permissions + иерархия ролей.
- Значимые изменения → обновление этого файла в той же операции.
- Не добавлять несогласованные крупные функции.
- Код — источник истины при расхождении с этим файлом.

---

## Recent Changes

- 2026-08-10 — система дней рождения (команды + notifier + `birthday_notifications`)
- 2026-08-10 — ядро бота, SQLite schema, `/config`, README
- 2026-08-10 — init git + PROJECT_CONTEXT

---

## Правило восстановления контекста

1. Прочитай этот файл первым.
2. Сверь структуру / Status / Current Task / TODO.
3. При противоречии верь коду и обнови этот файл.
