#!/usr/bin/env bash
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
(cd "$ROOT/api" && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload) &
API_PID=$!
(cd "$ROOT/web" && npm run dev -- --host 0.0.0.0 --port 5173) &
WEB_PID=$!
trap 'kill $API_PID $WEB_PID 2>/dev/null || true' EXIT INT TERM
wait
