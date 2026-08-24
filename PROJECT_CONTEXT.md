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
- Сообщения коммитов: **одна строка**, строчные буквы, без точки в конце, **без body/описания**.
- Не коммитить `.env`, `.env.production`, токены.
- Windows PowerShell: команды через `;`, не `&&`. Только `git commit -m "message"` (один `-m`).
- Не деструктивные git-команды и не force push в `main`, пока явно не попросили.

## Стек

- Python 3.12+, `discord.py>=2.6`, aiosqlite, Docker + GitHub Actions deploy (`DEPLOY.md`)
- Intents: members, message content, `emojis_and_stickers`
- Env: `DISCORD_TOKEN`, `DATABASE_PATH`, `DEFAULT_TIMEZONE`, `GROQ_API_KEY`, `GROQ_MODEL` (дефолт `openai/gpt-oss-20b`; `llama-3.1-8b-instant` снимают 16.08.2026). Groq: `User-Agent: ErundaBot/1.0` (без него Cloudflare 1010/403), для gpt-oss — `max_completion_tokens` + `reasoning_effort=low`, таймаут поздравления 120с.
- Slash-группы в Discord визуально не группируются в пикере; группы уже есть (`quote`, `birthday`, `myrole`, `event`, `proposal`, `fest`, `tgk`)

## Команды (актуально)

```text
/config
/birthday set|remove
/profile [user]          # ephemeral
/top
/event create|list|cancel|ping
/fest add|remove|new|edit|delete|export|winner|ping
/tgk add|remove|debug-add
/quote add|edit|delete|cleanup|random|user
Apps → Add quote
/myrole edit|delete
/proposal create|list|info|cancel
```

Удалены и не возвращать без запроса: `/birthday list|next|preview|test-announce` и debug `test-reminder`/`test-rgb-*`; `/quote list|import` и context «Import quote»; `/event join|info|leave` и кнопки участия; `/fest list|block|preview|test-ping|role`.

## Модули

### Ивенты

Slash: `create` (с ролью для пинга) / `list` / `cancel` / `ping`. Без списка участников и без кнопок «Участвовать». На карточке: описание курсивом, дата/время, роль пинга. `/event cancel` показывает карточку и спрашивает «Удалить этот ивент?». После отмены в оригинале жирно **Ивент отменён**, запись удаляется, номера сдвигаются. `/event list` — ephemeral, все scheduled (включая уже начавшиеся), ссылка на оригинал. `/event ping номер` — пингует роль ивента: если уже начался — «идёт», иначе сколько осталось. Автонапоминаний нет. При отмене/завершении и на ready бот сносит свои пинг-сообщения (и старые автонапоминания) в канале ивентов. Через 2 часа после старта карточка завершается и запись удаляется.

### Кинофестиваль

Канал в `/config`. Пока канала нет — `/fest new` и `/fest ping` пишут туда, откуда вызвали. Одно сообщение = `#N`, нумерация с **#29**. Как доска ДР: сеанс **МСК** + серая строка «местное время» (`<t:unix:f>` у каждого своё). После выбора победителя и старта сеанса: «Сеанс: **идёт**» + длина с TMDB (если нет — 120 мин) и «до конца» (`<t:end:R>`); когда время вышло — «Сеанс: **прошёл**». Кнопки оценки 1–10 на карточке, средняя рядом с названием. В списке заявок рядом с названием возраст (`12+`, `🔞 NSFW`). Маркеры `nsfw` / `нсфв` / `18+` в названии снимаются и помечают заявку; иначе Groq нормализует кривое название (опечатки/порядок слов), поиск на TMDB и бейдж certification (`G` → `0+`, `PG` → `6+`, `PG-13` → `12+`, `R`/`NC-17` → `18+`); если бейджа нет — Викиданные. При add/replace возраст пересчитывается заново. Между сеансом, списком и победителем — пустые промежутки (`Separator`). После `/fest winner` пингуется только роль из `/config` → Роли, снизу большой постер **только с TMDB** (если TMDB не нашёл — без картинки), название с TMDB если нашлось, иначе как ввёл человек. Победивший фильм больше нельзя предлагать ни в каком фестивале (скобки, запятые и регистр не важны: `(1993)` и `1993` — одно и то же). Победитель прошлого фестиваля не предлагает в следующем, со через один — снова можно. Один фильм с человека. Все: `add`/`remove` и кнопки на карточке «Предложить фильм» / «Убрать фильм». Роль «Кино» **или админ** (Administrator / Manage Server): `new`, `edit`, `delete`, `winner`, `export`, `ping`. `/fest delete` без номера — текущий открытый, с номером — любой старый; подтверждение показывает карточку, оставшиеся перенумеровываются с #29, блок победившего фильма снимается если он больше нигде не выигрывал. `/fest ping` — если сеанс уже начался, «мы уже смотрим фильм». При старте и конце сеанса бот обновляет карточки.

### ТГК

Несколько каналов на человека: ссылка `https://t.me/...`, `@channel` или invite `https://t.me/+...`. Название и картинка с t.me; **раз в день в 04:00** (timezone гильдии) бот перезапрашивает t.me и обновляет доску, если что-то изменилось. Под ссылкой серым `(приватка)` или `(открытый)`. Каналы **сгруппированы по владельцу** (все ТГК одного человека под одним разделителем, номера 1, 2, 3… подряд). Разделитель `--- {emoji} username ---`; превью канала — маленький thumbnail справа. ~8 каналов с превью на сообщение; если больше — несколько сообщений подряд («часть N из M»). На первой странице кнопки «Добавить ТГК» / «Убрать ТГК» (как у кинофестиваля). `/tgk debug-add` — добавить канал участнику по нику (роль из `/config` → Роли или админ).

### Цитаты (заморожено)

Карточки Components V2 (`QuoteCardView`): мелкий курсив `#N`, крупная цитата в «ёлочках», автор + русская дата, thumbnail справа у блока автора. На карточке обязателен display name; `@` авторы (до 5) только для `/quote user`, в `author_ids` JSON. Нумерация — колонка `number` по гильдии; cleanup/delete перенумеровывают и синкают сообщения. `/quote cleanup` без admin-check. Эмодзи сервера: `format_text_with_guild_emojis` / `expand_guild_shortcodes` в `bot/utils/birthday_emojis.py` — **не** `escape_markdown` на shortcodes (ломает `:dead_inside:`). На старте rewrite + migrate legacy cards.

### Дни рождения (заморожено)

Команды: `set`, `remove`. Доска в канале ДР, sync при set/remove/config и на ready. RGB-роль «Именинник», Groq-поздравления (голос из чата, случайный тон, можно зацепить роль/цитаты/кино/войс; ждёт ответ до 2 мин). Пинг в день ДР — отдельной строкой сообщения (не в embed: на мобиле иначе сырой id). После полуночи следующего дня удаляются оба сообщения: напоминание и поздравление. При старте и в цикле бот ещё проходит канал ДР и сносит старые карточки «День рождения» / «Скоро день рождения» (до того как id начали сохранять). Серверные эмодзи через `guild.fetch_emojis()`, в БД `<:name:id>`.

### Роли (заморожено)

Только персональные `/myrole edit|delete` (флаг в `/config`). Отдельные RGB-роли пользователей не пилить заново без запроса; RGB именинника — часть ДР.

### Остальное

- `/profile` ephemeral. Статистика: messages + voice_minutes + reactions. Реакция на карточку цитаты бота идёт в статистику `@`-авторам из `author_ids`, если они указаны. `/top` через 180с удаляет сообщение, а не оставляет мёртвые кнопки.
- Демократия: 👍/👎, кворум/% из config, auto-actions если `auto_execute_proposals`.
- `/config`: каналы, роли (доступ к `/config` и пинг кинофестиваля), timezone, флаги, времена уведомлений, правила голосований. Пока роль доступа не выбрана — `/config` открыт всем; после выбора — эта роль **или** админ / Manage Server.

## Структура

```text
bot/cogs/        config, birthdays, statistics, events, festival, tgk, quotes, roles, democracy
bot/database/    database.py (миграции в _migrate), models.py
bot/services/    * + ai_service.py (Groq), birthday_star_service.py, festival_service.py, tgk_service.py
bot/views/       * + quote_views.py, festival_views.py, tgk_views.py
bot/tasks/background.py
bot/utils/       embeds, formatting, birthday_emojis, colors, permissions, timezones
```

## БД (миграции до v21)

Таблицы: … `festivals` (номера с 29), `festival_films` (`image_url` постер, `age_rating` вроде `12+` / `NSFW`, `runtime_minutes`), `festival_ratings` (оценка 1–10), `festival_blocked_films` (прошлые победители и ручной блок), `tg_channels`. `birthday_notifications` хранит `channel_id`/`message_id` напоминания и поздравления.

Важное у гильдии: `birthday_board_message_id`, `birthday_star_role_id`, `fest_channel_id`, `tgk_channel_id`, `fest_staff_role_id`, `fest_ping_role_id`, `config_role_id`, `tgk_list_role_id`, `fest_reminder_minutes`, `tgk_board_message_id`, `tgk_board_message_ids`. У цитат: `author_display`, `posted_*`, `number`, `author_ids`. У ивентов: `number` (гильдия, только живые scheduled), `ping_role_id` (роль для `/event ping`).

`Database.close()` должен оставаться отдельным методом — не вшивать его в `_migrate` (уже ломалось).

## Background

- `birthday_loop` — поздравления и напоминания
- `event_loop` — auto-complete через 2ч после старта
- `fest_loop` — напоминание о сеансе по `fest_reminder_minutes`
- `proposal_loop` — закрытие голосований, auto-actions

## Known issues

- Global `tree.sync()` может задерживать появление slash-команд

## Recent changes (2026-08)

- Кинофестиваль и ТГК: сбор фильмов, победитель по имени, доска каналов
- Ивенты: роль пинга на карточке, `/event ping`, без участников/кнопок/автонапоминаний
- Ивенты: гильдийные номера, удаление после завершения
- Карточка ивента: курсивное описание, роль вместо списка людей
- Убраны `/event join|info|leave` — раньше были кнопки, теперь только пинг по роли
- Groq: дефолт `openai/gpt-oss-20b` вместо снятой `llama-3.1-8b-instant`
- Цитаты: карточки V2, номера, author_ids, эмодзи сервера, confirm delete, без list/import
- ДР: AI Groq, RGB именинник, без list/next/preview/test-announce/debug
- `/profile` ephemeral
- `discord.py>=2.6`, `intents.emojis_and_stickers`
