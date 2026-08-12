#!/bin/sh
# Состояние службы одной командой: ./status.sh
# Код возврата: 0 — работает, 1 — сбой последнего такта, 2 — данные устарели.
set -e
cd "$(dirname "$0")"
if ! docker compose ps --status running --services 2>/dev/null | grep -qx sync; then
    echo "Служба sync не запущена. Запустить: docker compose up -d"
    exit 2
fi
exec docker compose exec sync python status.py
