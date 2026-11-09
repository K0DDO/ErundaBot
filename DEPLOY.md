# Deploy — Ерунда

Production VPS: `deploy@107.172.44.182` (hostname: `strawberry`)

## Путь на сервере

По умолчанию: `/home/deploy/erunda`

Можно перенести в `/opt/erunda` (нужен root для `chown deploy:deploy`).

## Первичная установка

```bash
ssh deploy@107.172.44.182
git clone https://github.com/K0DDO/ErundaBot.git ~/erunda
cd ~/erunda
cp .env.production.example .env.production
# заполнить DISCORD_TOKEN, OWNER_USER_ID, GROQ_API_KEY, OPENROUTER_API_KEY, TELEGRAM_BOT_TOKEN
# DATABASE_URL уже указывает на сервис postgres в compose
mkdir -p data
docker compose --env-file .env.production up -d postgres
# дождаться healthy, затем (если есть старый SQLite) миграция:
# docker compose run --rm -e DATABASE_URL=postgresql://erunda:erunda@postgres:5432/erunda erunda-bot \
#   python scripts/migrate_sqlite_to_postgres.py --sqlite /app/data/erunda.db
docker compose --env-file .env.production up -d --build
docker logs erunda-bot --tail 50
```

## Миграция SQLite → Postgres (один раз)

1. Бэкап `./data/erunda.db`
2. Поднять только Postgres: `docker compose up -d postgres`
3. Прогнать `scripts/migrate_sqlite_to_postgres.py` с `DATABASE_URL` на контейнер postgres
4. В `.env.production` оставить `DATABASE_URL=postgresql://erunda:erunda@postgres:5432/erunda` и `OWNER_USER_ID` / `HOME_GUILD_ID`
5. `docker compose up -d --build` и smoke-test

## Обычный деплой

```bash
cd ~/erunda
bash scripts/deploy.sh
```

Или автоматически при push в `main` через GitHub Actions.

## GitHub Secrets (репозиторий ErundaBot)

| Secret | Пример |
|--------|--------|
| `VPS_SSH_KEY` | приватный ключ для `deploy@107.172.44.182` |
| `VPS_HOST` | `107.172.44.182` |
| `VPS_USER` | `deploy` |
| `ERUNDA_PATH` | `/home/deploy/erunda` (опционально) |

`.env.production` **не** коммитится и **не** деплоится через Git — только на сервере.

## Важно

- Не трогать контейнеры Amnezia, Briefly, Berrio и др.
- Postgres volume: `erunda_pgdata`; бот всё ещё монтирует `./data` (TTS-кэш и бэкап sqlite)
- Не выполнять `docker compose down --remove-orphans` на всём хосте
