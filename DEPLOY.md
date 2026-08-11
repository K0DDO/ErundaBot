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
# заполнить DISCORD_TOKEN в .env.production
mkdir -p data
docker compose --env-file .env.production up -d --build
docker logs erunda-bot --tail 50
```

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
- SQLite база: `./data/erunda.db` (volume)
- Не выполнять `docker compose down --remove-orphans` на всём хосте
