# PROJECT_CONTEXT — Ерунда

## Название проекта

**Ерунда** — Discord-бот для сервера «Ерундульки».

## Status

- [x] Базовая структура бота
- [x] Подключение Discord
- [x] SQLite
- [x] Дни рождения
- [x] Статистика / профиль / топы
- [x] Ивенты
- [x] Цитаты
- [x] Управление ролями
- [x] Пользовательские роли (`/myrole`)
- [x] RGB-роли
- [x] Демократия
- [x] Автовыполнение решений (опционально, `/config`)
- [x] `/config`
- [x] README и `.env.example`

## Структура проекта

```text
ErundaBot/
├── bot/
│   ├── bot.py
│   ├── cogs/
│   │   ├── config.py
│   │   ├── birthdays.py
│   │   ├── statistics.py
│   │   ├── events.py
│   │   ├── quotes.py
│   │   ├── roles.py
│   │   └── democracy.py
│   ├── database/
│   │   ├── database.py
│   │   └── models.py
│   ├── services/
│   │   ├── config_service.py
│   │   ├── birthday_service.py
│   │   ├── statistics_service.py
│   │   ├── event_service.py
│   │   ├── quote_service.py
│   │   ├── role_service.py
│   │   ├── rgb_manager.py
│   │   └── democracy_service.py
│   ├── views/
│   │   ├── config_views.py
│   │   ├── birthday_views.py
│   │   ├── top_views.py
│   │   ├── event_views.py
│   │   ├── role_views.py
│   │   └── proposal_views.py
│   ├── tasks/background.py
│   └── utils/
├── data/
├── main.py
├── requirements.txt
├── .env.example
├── README.md
└── PROJECT_CONTEXT.md
```

## Discord-команды

```text
/config
/birthday set|remove|list|next
/profile [user]
/top
/event create|list|info|join|leave|cancel
/quote add|random|list|user
Apps → Add quote (context menu)
/myrole edit|delete
/proposal create|list|info|cancel
```

## Background tasks

- `birthday_loop` — поздравления и напоминания
- `event_loop` — напоминания, старт, auto-complete (+2ч)
- `proposal_loop` — закрытие голосований, результат, auto-actions
- `RgbManager` — централизованный HSV loop (≥10 сек)

## База данных

Таблицы: `guilds` (+ `birthday_board_message_id`), `birthdays`, `birthday_notifications`, … `quotes` (+ `author_display`), …

## Реализованные функции

### Дни рождения
Команды + доска в канале ДР (инструкция + список без @), авто-edit при set/remove и при выборе канала в `/config`.

### Цитаты
Slash + context menu; автор показывается текстом (`author_display`), без упоминания; `/quote add` — параметры `name` и/или `author`.

### Роли
Только персональные: `/myrole edit`, `/myrole delete` (нужен флаг в `/config`). RGB через `custom_roles`.

### Ивенты
Modal create, list/info/join/leave/cancel, кнопки на embed, лимит участников, restore views после restart, напоминания по `event_reminder_minutes`.

### Демократия
Предложения с 👍/👎, смена голоса, кворум и % из config, auto-actions (`create_role`, `delete_role`, `create_channel`, `bot_config`) если `auto_execute_proposals` включён.

## Current Task

Все системы по ТЗ реализованы. Дальнейшая работа — багфиксы по feedback с сервера.

## Known Issues

- Global `tree.sync()` может задерживать slash-команды
- `/top` view timeout 180с
- Auto-actions предложений без UI — только через democracy service payload (расширение при необходимости)

## Technical Decisions

- Overall activity = messages + voice_minutes + reactions
- RGB: один `RgbManager`, hue step по speed, min interval 10s
- Auto-execute: default off
- Vote defaults: 24h / quorum 3 / ratio 0.5

## Recent Changes

- 2026-08-11 — ивенты, цитаты, роли/RGB, демократия
- 2026-08-10 — статистика, дни рождения, ядро

## Правило восстановления контекста

Прочитай этот файл первым; при расхождении верь коду и обнови файл.
