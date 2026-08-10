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

- [ ] Базовая структура бота
- [ ] Подключение Discord
- [ ] SQLite
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
- [ ] `/config` (настройки сервера)
- [ ] README и `.env.example`

---

## Структура проекта

Планируемая (ещё не создана — репозиторий пустой):

```text
ErundaBot/
├── bot/
│   ├── __init__.py
│   ├── bot.py                 # класс бота, загрузка cogs, lifecycle
│   ├── cogs/
│   │   ├── birthdays.py
│   │   ├── statistics.py      # listeners + /profile + /top
│   │   ├── events.py
│   │   ├── quotes.py
│   │   ├── roles.py           # /role + /myrole
│   │   ├── democracy.py
│   │   └── config.py          # /config
│   ├── database/
│   │   ├── database.py        # connection, migrations/schema init
│   │   └── models.py          # SQL / dataclasses / row mappers
│   ├── services/
│   │   ├── birthday_service.py
│   │   ├── statistics_service.py
│   │   ├── event_service.py
│   │   ├── quote_service.py
│   │   ├── role_service.py
│   │   ├── rgb_manager.py     # централизованный менеджер RGB
│   │   ├── democracy_service.py
│   │   └── config_service.py
│   ├── views/                 # Buttons, Selects, Modals
│   │   ├── event_views.py
│   │   ├── top_views.py
│   │   ├── proposal_views.py
│   │   ├── birthday_views.py
│   │   └── role_views.py
│   ├── tasks/                 # фоновые задачи (один набор на lifecycle бота)
│   │   ├── scheduler.py
│   │   └── background.py
│   └── utils/
│       ├── embeds.py          # единый стиль Embed
│       ├── permissions.py
│       ├── timezones.py
│       └── formatting.py
├── data/                      # SQLite (в .gitignore)
├── main.py
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
└── PROJECT_CONTEXT.md
```

Фактическое состояние: в корне пока только `.gitignore` и `PROJECT_CONTEXT.md`.

---

## Архитектура

### Слои

| Слой | Ответственность |
|------|-----------------|
| `main.py` | Точка входа, загрузка `.env`, запуск бота |
| `bot/bot.py` | `commands.Bot`, intents, setup_hook, sync commands, старт/стоп tasks |
| `bot/cogs/*` | Slash Commands, listeners, Discord UI wiring |
| `bot/views/*` | Buttons / Selects / Modals |
| `bot/services/*` | Бизнес-логика (без прямого SQL в cogs) |
| `bot/database/*` | SQLite schema, queries |
| `bot/tasks/*` | Централизованные background loops |
| `bot/utils/*` | Embeds, permissions, timezone, форматирование |

### Принципы

- Не смешивать Discord-команды, бизнес-логику и DB в одном файле.
- Фоновые задачи запускаются один раз в lifecycle бота (не на каждое событие).
- Все серверные данные привязаны к `guild_id` (мультигильд-готовность).
- Токен Discord только в `.env`, никогда в БД.
- Админ-действия всегда проверяют Discord permissions + иерархию ролей.

### Background tasks (планируемые)

1. **Birthday notifier** — проверка дней рождения по timezone гильдии; поздравление + optional reminder.
2. **Event scheduler** — напоминания, уведомление перед стартом, перевод в `completed`, восстановление после restart.
3. **Proposal closer** — завершение голосований, публикация результата, optional auto-actions.
4. **RGB manager** — один центральный loop на все RGB-роли (не отдельный loop на роль).
5. **Voice session recovery** — при старте закрыть «осиротевшие» open sessions / переоткрыть активные voice members.

---

## База данных (план)

SQLite, путь из `DATABASE_PATH` (по умолчанию `./data/erunda.db`).

### Таблицы

#### `guilds`

Настройки сервера (единая точка `/config`).

Основные поля: `guild_id` PK, `timezone`, каналы (`birthday_channel_id`, `events_channel_id`, `proposals_channel_id`, `quotes_channel_id`), флаги (`statistics_enabled`, `personal_roles_enabled`, `auto_execute_proposals`, `rgb_enabled`), времена уведомлений (`birthday_announce_time`, `birthday_reminder_days`, `event_reminder_minutes`), RGB defaults (`rgb_interval_seconds`), правила голосования (`proposal_duration_hours`, `proposal_quorum`, `proposal_pass_ratio`), `afk_channel_id` (кэш/override при необходимости), timestamps.

#### `users`

Кэш участников: `guild_id`, `user_id`, `joined_at`, `display_name_cache` (опционально), PK `(guild_id, user_id)`.

#### `birthdays`

`guild_id`, `user_id`, `day`, `month`, `year` NULLABLE, PK `(guild_id, user_id)`.

#### `message_statistics`

Счётчики сообщений: `guild_id`, `user_id`, `channel_id`, `date` (день в timezone гильдии), `count`.  
Уникальность: `(guild_id, user_id, channel_id, date)`.

#### `voice_sessions`

Сессии voice: `id`, `guild_id`, `user_id`, `channel_id`, `started_at`, `ended_at` NULLABLE, `duration_seconds` NULLABLE.  
Открытые сессии (`ended_at IS NULL`) восстанавливаются/закрываются при restart.

#### `reaction_statistics`

Полученные реакции: `guild_id`, `user_id` (автор сообщения), `emoji`, `date`, `count` — или агрегат `count` без emoji, если достаточно для топов.  
Минимально для ТЗ: количество полученных реакций по пользователю/дате.

#### `events`

`id`, `guild_id`, `title`, `description`, `starts_at`, `max_participants`, `channel_id`, `organizer_id`, `message_id`, `status` (`scheduled`/`cancelled`/`completed`), timestamps.

#### `event_participants`

`event_id`, `user_id`, `joined_at`, PK `(event_id, user_id)`.

#### `quotes`

`id`, `guild_id`, `content`, `author_id`, `channel_id`, `message_id` NULLABLE, `added_by`, `created_at` (дата исходного сообщения), `saved_at`, `reactions_snapshot` (JSON: emoji→count).

#### `custom_roles`

Связь Discord-ролей с логикой бота: `id`, `guild_id`, `role_id`, `owner_id` NULLABLE (персональная), `kind` (`managed`/`personal`), `rgb_enabled`, `rgb_speed`, `rgb_hue`, timestamps.

#### `role_animations`

Состояние RGB: `guild_id`, `role_id`, `enabled`, `hue` (0–360), `interval_seconds`, `updated_at`.  
Может быть объединено с `custom_roles`, если дублирование не нужно — решение при реализации: **предпочесть одну таблицу `custom_roles` + поля анимации**, отдельную `role_animations` только если понадобится история/несколько режимов.

#### `proposals`

`id`, `guild_id`, `number`, `title`/`content`, `author_id`, `channel_id`, `message_id`, `status` (`open`/`passed`/`rejected`/`cancelled`), `ends_at`, `action_type` NULLABLE, `action_payload` JSON NULLABLE, timestamps.

#### `proposal_votes`

`proposal_id`, `user_id`, `vote` (`yes`/`no`), `updated_at`, PK `(proposal_id, user_id)` — один голос, можно менять.

### Связи

- Всё ключевое → `guilds.guild_id`
- `event_participants.event_id` → `events.id`
- `proposal_votes.proposal_id` → `proposals.id`
- `custom_roles.owner_id` → персональная роль пользователя

---

## Discord-команды (план)

```text
/birthday set
/birthday remove
/birthday list
/birthday next

/profile [user]
/top

/event create
/event list
/event info
/event join
/event leave
/event cancel

/quote add
/quote random
/quote list
/quote user
(Message Context Menu) Add quote

/role create
/role edit
/role delete
/myrole

/proposal create
/proposal list
/proposal info
/proposal cancel

/config
```

---

## Discord Intents

Обязательные:

- `guilds`
- `members` (Privileged) — профили, joined_at, роли
- `guild_messages`
- `message_content` (Privileged) — текст для статистики/цитат
- `guild_reactions` — полученные реакции
- `voice_states` — voice-статистика

---

## Discord Permissions (приглашение бота)

Минимально необходимые:

- View Channels
- Send Messages
- Embed Links
- Attach Files (если понадобится для иконок/вложений)
- Read Message History
- Add Reactions (опционально)
- Manage Roles (роли + RGB + auto-actions)
- Manage Channels (auto-actions демократии: создание каналов)
- Use Application Commands

Бот-роль должна стоять выше управляемых ролей.

---

## Реализованные функции

Пока ничего не реализовано (пустой репозиторий). Ниже — целевое описание по ТЗ.

### Дни рождения

Статус: не начато

Цель: set/remove/list/next; автопоздравление; reminder; timezone гильдии; год опционален.

### Статистика / Профиль / Топы

Статус: не начато

Цель: сообщения (user/channel/date), voice (без AFK), реакции; `/profile`; `/top` с Select (категория + период).

### Ивенты

Статус: не начато

Цель: CRUD + join/leave; Buttons; лимит; напоминания; restore после restart; статус `completed`.

### Цитаты

Статус: не начато

Цель: slash + context menu; snapshot реакций; цитата живёт после удаления исходного сообщения.

### Роли / myrole / RGB

Статус: не начато

Цель: admin `/role`; `/myrole` с лимитами; центральный RGB manager; HSV; безопасный интервал; restore из БД.

### Демократия

Статус: не начато

Цель: предложения, один голос с возможностью смены; авторезультат; настраиваемые правила; optional auto-actions (отключаемые).

### Config

Статус: не начато

Цель: централизованный `/config` для каналов, timezone, флагов, RGB, голосований, personal roles.

---

## Current Task

Первоначальная инициализация проекта: создан `PROJECT_CONTEXT.md`, зафиксированы архитектура и план.

Следующий шаг:

1. Создать базовую структуру (`main.py`, `bot/`, `requirements.txt`, `.env.example`).
2. Подключить Discord + SQLite schema.
3. Реализовать `/config` (минимум) и ядро lifecycle/tasks.
4. Далее по порядку модулей (см. TODO).

---

## TODO

- [ ] Базовая структура проекта + `requirements.txt` + `.env.example` + README
- [ ] SQLite schema + database layer
- [ ] Ядро бота (intents, cogs load, task lifecycle)
- [ ] `/config` (базовые настройки гильдии)
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

Пока нет (код не написан).

---

## Technical Decisions

- Python 3.12+ (локально обнаружен Python 3.14.2)
- discord.py 2.x
- SQLite (stdlib `aiosqlite` для async)
- Slash Commands + Discord UI (Buttons, Selects, Modals, Context Menus)
- Бизнес-логика в `services/`, Discord UI в `cogs/` + `views/`
- Фоновые задачи централизованы в `bot/tasks/`, старт в `setup_hook` / `on_ready` с guard от дублей
- Timezone: `zoneinfo` + настройка на гильдию (`DEFAULT_TIMEZONE=Europe/Moscow`)
- RGB: HSV hue step; **один** manager loop; default interval **≥ 10 секунд** на роль (настраиваемо); discord.py сам уважает 429/`retry_after`; не хардкодить bucket Discord
- «Общая активность» для `/top`: взвешенная метрика (сообщения + voice-минуты + реакции) — точные веса зафиксировать при реализации модуля топов (техническая деталь, не новая подсистема)
- Personal roles: одна персональная роль на пользователя на гильдию (если иначе — согласовать)
- Auto-execute proposals: по умолчанию **выключено**; опасные действия только при флаге + проверке permissions
- Git remote: `https://github.com/K0DDO/ErundaBot.git`, ветка `main`
- Commit messages: English, lowercase, no trailing period (по ТЗ §42)

### Открытые решения (не блокируют старт ядра)

При реализации соответствующих модулей при необходимости уточнить у пользователя, если выбор меняет UX:

1. Точная формула «общей активности».
2. Дефолтные правила голосования (кворум / % «за» / длительность).
3. Набор action_type для автовыполнения предложений.
4. Минимальный/максимальный интервал RGB и дефолтная скорость `/myrole`.

---

## Зависимости (план)

| Пакет | Назначение |
|-------|------------|
| `discord.py` | Discord API / bots |
| `aiosqlite` | Async SQLite |
| `python-dotenv` | `.env` |

Версии зафиксировать в `requirements.txt` при создании структуры.

---

## Правила разработки

- Не хранить секреты в коде / Git.
- Не смешивать DB-логику и Discord UI.
- Не создавать бесконтрольные background tasks.
- Учитывать Discord API rate limits.
- Все административные действия проверяют permissions + иерархию ролей.
- Все значимые изменения отражаются в `PROJECT_CONTEXT.md` в той же операции.
- Не добавлять несогласованные крупные функции.
- При неоднозначности, влияющей на UX/архитектуру — сначала предложить варианты пользователю.
- Код — источник истины при расхождении с `PROJECT_CONTEXT.md`; затем исправить контекст.

---

## Recent Changes

- 2026-08-10 — репозиторий пустой; создан `PROJECT_CONTEXT.md` с архитектурой и планом по ТЗ
- 2026-08-10 — добавлен `.gitignore`
- 2026-08-10 — инициализация Git (`main`) и remote `origin` → `https://github.com/K0DDO/ErundaBot.git`

---

## Правило восстановления контекста

Если начинается новый контекст:

1. **Первым делом прочитай этот файл.**
2. Сверь структуру проекта с разделом «Структура».
3. Посмотри Status / Current Task / TODO / Known Issues / Recent Changes.
4. Только после этого продолжай разработку.
5. При противоречии код vs этот файл — верь коду, обнови этот файл.
