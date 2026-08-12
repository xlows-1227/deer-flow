#!/usr/bin/env sh
#
# DeerFlow frontend live entrypoint — production serve with bind-mounted source.
# Runs `pnpm build` then `pnpm start` so pages are fast (unlike `next dev`).
# Logs go to /app/logs/frontend.log (same pattern as gateway dev-entrypoint).

set -e

exec >/app/logs/frontend.log 2>&1

cd /app/frontend

mkdir -p .next
chmod 777 .next 2>/dev/null || true

echo "[frontend] clearing stale .next output..."
if [ -d .next ]; then
    find .next -mindepth 1 -delete 2>/dev/null || true
fi

echo "[frontend] building (SKIP_ENV_VALIDATION=1 pnpm build)..."
# NODE_ENV=production is set in compose; build still needs a writable .next volume.
SKIP_ENV_VALIDATION=1 pnpm build

if [ ! -f .next/BUILD_ID ]; then
    echo "[frontend] ERROR: build finished but .next/BUILD_ID is missing"
    exit 1
fi

echo "[frontend] starting (pnpm start)..."
exec pnpm start
