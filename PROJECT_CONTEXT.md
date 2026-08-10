# PROJECT_CONTEXT — Ерунда

## Название проекта

**Ерунда** — Discord-бот для небольшого Discord-сервера «Ерундульки».

## Цель проекта

Бот для живого небольшого сообщества: дни рождения, статистика и топы, ивенты, цитаты, роли (RGB), демократия.

Не добавлять несогласованные системы (музыка, XP, уровни, экономика и т.п.).

---

## Status

- [x] Базовая структура бота
- [x] Подключение Discord
- [x] SQLite
- [x] Дни рождения
- [x] Статистика
- [x] Профиль
- [x] Топы
- [ ] Ивенты
- [ ] Цитаты
- [ ] Управление ролями
- [ ] Пользовательские роли (`/myrole`)
- [ ] RGB-роли
- [ ] Демократия
- [ ] Автовыполнение решений демократии
- [x] `/config`
- [x] README и `.env.example`

---

## Структура проекта

```text
ErundaBot/
├── bot/
│   ├── bot.py
│   ├── cogs/
│   │   ├── config.py
│   │   ├── birthdays.py
│   │   └── statistics.py
│   ├── database/
│   │   ├── database.py
│   │   └── models.py
│   ├── services/
│   │   ├── config_service.py
│   │   ├── birthday_service.py
│   │   └── statistics_service.py
│   ├── views/
│   │   ├── config_views.py
│   │   ├── birthday_views.py
│   │   └── top_views.py
│   ├── tasks/background.py
│   └── utils/
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

`cogs/views` → `services` → `database`; `BackgroundTasks` для loops.

Background:

- `birthday_loop` (1 мин)
- voice recovery при `on_ready` (один раз, guard)

---

## База данных

Таблицы: `guilds`, `users`, `birthdays`, `birthday_notifications`, `message_statistics`, `voice_sessions`, `reaction_statistics`, `events`, `event_participants`, `quotes`, `custom_roles`, `proposals`, `proposal_votes`.

---

## Discord-команды

```text
/config
/birthday set|remove|list|next
/profile [user]
/top
```

---

## Реализованные функции

### Дни рождения — реализовано

### Статистика / профиль / топы — реализовано

- сообщения: guild/user/channel/date (timezone гильдии)
- voice: сессии, AFK не считается; recovery после restart
- реакции: полученные (не self, не боты); add/remove
- флаг `statistics_enabled` в `/config`
- `/profile` — сообщения, voice, реакции, время на сервере, ранги
- `/top` — Select категория + период (сегодня / неделя / месяц / всё время)

**Общая активность:** `messages + voice_minutes + reactions`.

---

## Current Task

Статистика/профиль/топы готовы.

Следующий шаг: система ивентов.

---

## TODO

- [ ] Система ивентов
- [ ] Система цитат (+ context menu)
- [ ] Управление ролями + `/myrole`
- [ ] RGB manager
- [ ] Демократия + optional auto-actions
- [ ] Проверка restart-recovery (events, RGB, proposals)

---

## Known Issues

- Global `tree.sync()` может задерживать slash-команды.
- `/top` View timeout 180с — после этого Select перестаёт работать (нужно вызвать `/top` снова).

---

## Technical Decisions

- Python 3.12+, discord.py 2.x, aiosqlite, python-dotenv, tzdata
- Overall score = messages + (voice_seconds // 60) + reactions
- Voice overlap clipping при подсчёте за период
- Birthday notify dedup в `birthday_notifications`
- RGB interval ≥ 10 (ещё не реализовано)
- Commit messages: English, lowercase, no trailing period

### Открытые

1. Набор `action_type` для автовыполнения предложений.
2. Дефолтная скорость `/myrole` RGB.

---

## Зависимости

`discord.py`, `aiosqlite`, `python-dotenv`, `tzdata`

---

## Правила разработки

- Секреты не в Git; не смешивать DB и UI; один набор background tasks; permissions + иерархия; обновлять PROJECT_CONTEXT; не добавлять лишние крупные функции.

---

## Recent Changes

- 2026-08-10 — статистика, `/profile`, `/top`, voice recovery
- 2026-08-10 — дни рождения
- 2026-08-10 — ядро + `/config`

---

## Правило восстановления контекста

Прочитай этот файл первым; при расхождении верь коду и обнови файл.
