# PROJECT_CONTEXT — системный промпт Ерунды

Discord-бот **Ерунда** для сервера «Ерундульки». Репозиторий: `K0DDO/ErundaBot`, ветка `main`.

**Когда читать этот файл:** только после сжатия контекстного окна (conversation summary) или если пропала память о проекте. Не перечитывать каждый ход. Если файл расходится с кодом — верь коду и обнови файл.

## Заморозки (пока пользователь не попросит иначе)

Не трогать без явного запроса:

- дни рождения (`bot/cogs/birthdays.py`, `birthday_service`, `birthday_star_service`, `birthday_views`, RGB-роль именинника)
- роли (`bot/cogs/roles.py`, `role_service`, `role_views`, `/myrole`)
- цитаты (`bot/cogs/quotes.py`, `quote_service`, `quote_views`, emoji helpers)

## Git и агент

- После рабочих правок: commit + **push** (пользователь так просил).
- Сообщения коммитов: **строчные буквы, без точки в конце**.
- Не коммитить `.env`, `.env.production`, токены.
- Windows PowerShell: команды через `;`, не `&&`. HEREDOC/`cat` для commit ненадёжен — `git commit -m "message"`.
- Не деструктивные git-команды и не force push в `main`, пока явно не попросили.

## Стек

- Python 3.12+, `discord.py>=2.6`, aiosqlite, Docker + GitHub Actions deploy (`DEPLOY.md`)
- Intents: members, message content, `emojis_and_stickers`
- Env: `DISCORD_TOKEN`, `DATABASE_PATH`, `DEFAULT_TIMEZONE`, `GROQ_API_KEY`, `GROQ_MODEL` (дефолт `openai/gpt-oss-20b`; `llama-3.1-8b-instant` снимают 16.08.2026). Groq: `User-Agent: ErundaBot/1.0` (без него Cloudflare 1010/403), для gpt-oss — `max_completion_tokens` + `reasoning_effort=low`.
- Slash-группы в Discord визуально не группируются в пикере; группы уже есть (`quote`, `birthday`, `myrole`, `event`, `proposal`, `fest`, `tgk`)

## Команды (актуально)

```text
/config
/birthday set|remove|test-announce
/profile [user]          # ephemeral
/top
/event create|list|cancel
/fest add|remove|role|new|edit|delete|export|winner|ping|preview|test-ping|block
/tgk add|remove|list
/quote add|edit|delete|cleanup|random|user
Apps → Add quote
/myrole edit|delete
/proposal create|list|info|cancel
```

Удалены и не возвращать без запроса: `/birthday list|next|preview` и debug `test-reminder`/`test-rgb-*`; `/quote list|import` и context «Import quote»; `/event join|info|leave`; `/fest list`. Вернули по запросу: `/birthday test-announce` (ephemeral ИИ-поздравление).

## Модули

### Ивенты

Slash только create / list / cancel. Кнопки на карточке: «Участвовать» и «Не участвовать». Описание курсивом, создатель первый в «Участники». `/event cancel` показывает карточку и спрашивает «Удалить этот ивент?» (как цитаты). После отмены в оригинале жирно **Ивент отменён**, кнопки снимаются, запись удаляется, номера сдвигаются. `/event list` — только ещё не начавшиеся, ссылка на оригинал. Через 2 часа после старта карточка завершается и запись удаляется. `PUBLIC_CONFIG_ENABLED = True` — `/config` временно для всех.

### Кинофестиваль

Канал в `/config`. Пока канала нет — `/fest new` и `/fest ping` пишут туда, откуда вызвали. Одно сообщение = `#N`, как доска ДР: сеанс **МСК** + серая строка «местное время» (`<t:unix:f>` у каждого своё). В списке заявок рядом с названием возраст (`12+`, `🔞 NSFW`). Маркеры `nsfw` / `нсфв` / `18+` в названии снимаются и помечают заявку; иначе рейтинг с iTunes, Викиданных или TMDB. При add/replace возраст пересчитывается заново. Между сеансом, списком и победителем — пустые промежутки (`Separator`). Закрытый фестиваль: «Сеанс: закончился». После `/fest winner` пингуется только роль из `/config` → Роли, снизу большой постер (MediaGallery), название крупно со случайным эмодзи сервера. Победивший фильм больше нельзя предлагать ни в каком фестивале (скобки, запятые и регистр не важны: `(1993)` и `1993` — одно и то же). Победитель прошлого фестиваля не предлагает в следующем, со через один — снова можно. `/fest block название` — ручной блок (роль «Кино»), снимает заявку с текущего если она есть. Один фильм с человека. Все: `add`/`remove`. Роль «Кино»: `new`, `edit`, `delete`, `winner`, `export`, `ping`, `block`. `/fest delete` без номера — текущий открытый, с номером — любой старый (`#1`, `#2`…); подтверждение показывает карточку, оставшиеся перенумеровываются, блок победившего фильма снимается если он больше нигде не выигрывал. `/fest ping` — если сеанс уже начался, «мы уже смотрим фильм». При старте бот обновляет карточки.

### ТГК

Несколько каналов на человека: название + `https://t.me/...`. Картинка с og:image публичного канала, иначе без неё. Доска в канале из `/config`, пока канала нет — `/tgk list` шлёт список туда, откуда вызвали. `/tgk add`, `/tgk remove номер`, `/tgk list`.

### Цитаты (заморожено)

Карточки Components V2 (`QuoteCardView`): мелкий курсив `#N`, крупная цитата в «ёлочках», автор + русская дата, thumbnail справа у блока автора. На карточке обязателен display name; `@` авторы (до 5) только для `/quote user`, в `author_ids` JSON. Нумерация — колонка `number` по гильдии; cleanup/delete перенумеровывают и синкают сообщения. `/quote cleanup` без admin-check. Эмодзи сервера: `format_text_with_guild_emojis` / `expand_guild_shortcodes` в `bot/utils/birthday_emojis.py` — **не** `escape_markdown` на shortcodes (ломает `:dead_inside:`). На старте rewrite + migrate legacy cards.

### Дни рождения (заморожено)

Команды: `set`, `remove`, дебаг `/birthday test-announce` (ephemeral, любой участник; если даты нет — генерит как будто сегодня). Доска в канале ДР, sync при set/remove/config и на ready. RGB-роль «Именинник», Groq-поздравления (только русский текст), пинг только в день ДР. Серверные эмодзи через `guild.fetch_emojis()`, в БД `<:name:id>`.

### Роли (заморожено)

Только персональные `/myrole edit|delete` (флаг в `/config`). Отдельные RGB-роли пользователей не пилить заново без запроса; RGB именинника — часть ДР.

### Остальное

- `/profile` ephemeral. Статистика: messages + voice_minutes + reactions.
- Демократия: 👍/👎, кворум/% из config, auto-actions если `auto_execute_proposals`.
- `/config`: каналы, роли (пинг кинофестиваля), timezone, флаги, времена уведомлений, правила голосований.

## Структура

```text
bot/cogs/        config, birthdays, statistics, events, festival, tgk, quotes, roles, democracy
bot/database/    database.py (миграции в _migrate), models.py
bot/services/    * + ai_service.py (Groq), birthday_star_service.py, festival_service.py, tgk_service.py
bot/views/       * + quote_views.py, festival_views.py, tgk_views.py
bot/tasks/background.py
bot/utils/       embeds, formatting, birthday_emojis, colors, permissions, timezones
```

## БД (миграции до v14)

Таблицы: … `festivals`, `festival_films` (`image_url` постер, `age_rating` вроде `12+` / `NSFW`), `festival_blocked_films` (прошлые победители и ручной блок), `tg_channels`.

Важное у гильдии: `birthday_board_message_id`, `birthday_star_role_id`, `fest_channel_id`, `tgk_channel_id`, `fest_staff_role_id`, `fest_ping_role_id`, `fest_reminder_minutes`, `tgk_board_message_id`. У цитат: `author_display`, `posted_*`, `number`, `author_ids`. У ивентов: `number` (гильдия, только живые scheduled).

`Database.close()` должен оставаться отдельным методом — не вшивать его в `_migrate` (уже ломалось).

## Background

- `birthday_loop` — поздравления и напоминания
- `event_loop` — напоминания, старт, auto-complete
- `fest_loop` — напоминание о сеансе по `fest_reminder_minutes`
- `proposal_loop` — закрытие голосований, auto-actions

## Known issues

- Global `tree.sync()` может задерживать появление slash-команд
- `/top` view timeout 180с

## Recent changes (2026-08)

- Кинофестиваль и ТГК: сбор фильмов, победитель по имени, доска каналов
- Ивенты: гильдийные номера, удаление после завершения, `/config` временно для всех
- Карточка ивента: курсивное описание, участники списком, создатель первый
- Groq: дефолт `openai/gpt-oss-20b` вместо снятой `llama-3.1-8b-instant`
- Убраны `/event join|info|leave` — функционал кнопок на карточке
- Цитаты: карточки V2, номера, author_ids, эмодзи сервера, confirm delete, без list/import
- ДР: AI Groq, RGB именинник, без list/next/preview/debug
- `/profile` ephemeral
- `discord.py>=2.6`, `intents.emojis_and_stickers`
