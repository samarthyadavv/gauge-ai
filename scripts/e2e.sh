#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export ADMIN_KEY=test-only-e2e-access-key-at-least-32-characters
export E2E_ADMIN_KEY="$ADMIN_KEY"
export DATABASE_URL=${E2E_DATABASE_URL:-sqlite:////tmp/gauge_e2e_test.db}
export BASE_URL=http://127.0.0.1:9201
[[ "$DATABASE_URL" == *"_test"* ]] || { echo 'E2E database must contain _test'; exit 1; }
cd backend
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 9201 --no-access-log >/tmp/gauge-test-web.log 2>&1 & web_pid=$!
uv run python -m app.worker >/tmp/gauge-test-worker.log 2>&1 & worker_pid=$!
trap 'kill "$web_pid" "$worker_pid" 2>/dev/null || true' EXIT
uv run python - <<'PY'
import time,urllib.request
for i in range(40):
    try:urllib.request.urlopen('http://127.0.0.1:9201/ready');break
    except Exception:time.sleep(.25)
else:raise SystemExit('Test API failed to start')
PY
cd ../frontend
npx playwright test
