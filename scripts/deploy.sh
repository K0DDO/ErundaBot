#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${ERUNDA_PATH:-$HOME/erunda}"
COMPOSE=(docker compose --env-file .env.production)

if [[ ! -d "${APP_DIR}" ]]; then
  echo "Erunda directory not found: ${APP_DIR}" >&2
  exit 1
fi

cd "${APP_DIR}"

if [[ ! -f .env.production ]]; then
  echo "Missing ${APP_DIR}/.env.production" >&2
  exit 1
fi

echo "==> Using ${APP_DIR}"
PREV_SHA="$(git rev-parse HEAD)"

rollback() {
  echo "Deploy failed — rolling back to ${PREV_SHA}" >&2
  git reset --hard "${PREV_SHA}"
  "${COMPOSE[@]}" up -d --build --force-recreate
  exit 1
}

echo "==> git pull"
git fetch origin main
git checkout main
git pull --ff-only origin main
echo "Host SHA $(git rev-parse --short HEAD)"

echo "==> Build and restart"
if ! "${COMPOSE[@]}" up -d --build; then
  rollback
fi

echo "==> Status"
"${COMPOSE[@]}" ps

sleep 3
ok=0
for _ in $(seq 1 20); do
  if docker logs erunda-bot --tail 40 2>&1 | grep -q "Logged in as"; then
    ok=1
    break
  fi
  sleep 2
done
if [[ "${ok}" -ne 1 ]]; then
  echo "Bot did not log in — check logs:" >&2
  docker logs erunda-bot --tail 80 >&2 || true
  rollback
fi

echo "Deploy OK $(git rev-parse --short HEAD) @ ${APP_DIR}"
