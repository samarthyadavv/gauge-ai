# Gauge — AI evaluation service

A standalone backend portfolio project for evaluating model behavior, inspecting failures, and comparing releases. FastAPI + SQLAlchemy + a durable database-backed worker, with a minimal React/TypeScript dashboard.

**Working scope:** immutable datasets, exact/contains/JSON assertions, forbidden-term checks, asynchronous runs, per-case outputs, measured latency, token usage, optional cost estimates, JSON exports, and baseline/candidate comparisons. The UI always identifies mock results. No synthetic run history or invented metrics is seeded.

## Quick start — no Docker or paid AI API needed

Requires Python 3.12+, uv, and Node 22+.

```bash
python scripts/setup.py
cd backend
uv sync --frozen
cd ../frontend
npm ci
npm run build
cd ..
uv run --project backend python scripts/dev.py
```

Open **http://localhost:9200**. Copy `ADMIN_KEY` from your local `.env` into the sign-in form; do not share it or paste it into chat. Setup generates random secrets and protects the file with mode 0600. Login exchanges the key for an HttpOnly server-side session; the browser does not store the key in localStorage.

1. Open Datasets → New dataset → Load starter examples.
2. Create the six-case synthetic dataset.
3. Open Runs → New evaluation and choose the deterministic mock provider.
4. Inspect case outputs, latency, and the deliberately unsupported general-knowledge case.
5. Run the same dataset again and use Compare to inspect differences.

The mock is a tiny ruleset (uppercase, lowercase, addition, reverse, and one JSON instruction), **not an AI model**. The starter dataset has five supported cases and one unsupported case, so its mock score is 5/6. This tests the evaluation workflow, not model quality.

## Docker / PostgreSQL

```bash
python scripts/setup.py  # only if .env does not exist
# Compose reads .env and overrides DATABASE_URL to the private PostgreSQL service.
docker compose build web
docker compose up -d --wait
```

Web is bound to localhost:9200. PostgreSQL has a persistent named volume and no published port. Migration completes before web and worker start. Web and worker share one non-root image. Do not use `down -v` unless you intend to delete the database.

For public deployment, put the app behind HTTPS, set `ENVIRONMENT=production` and an exact HTTPS `BASE_URL`, keep PostgreSQL private, and use a generated operator key. This is a **single-operator service**, not a multi-tenant SaaS. It is not deployed automatically.

For a hosted backend connected to a separate frontend, see [Deploy the backend to Render](docs/render.md).

## Real AI provider

Set `OPENAI_API_KEY` in the server environment / protected `.env` and restart web and worker. `OPENAI_MODELS` is a comma-separated allowlist (default `gpt-4.1-mini`). The provider then appears in the evaluation form. Callers cannot select arbitrary upstream URLs or unconfigured model names.

Each run sends one request per case. There are at most 100 cases, 4,000 prompt characters each, a 2,000-character system instruction, and 1,000 output tokens per case. Ground-truth answers and grading criteria are **not sent to the model**. The mock ignores system instructions by design.

Token counts come from provider-reported usage. Optional USD/million-token prices are entered by the operator and snapshotted with the run; cost is an **estimate**, not an invoice. Missing usage/pricing stays unknown. Failed or interrupted requests can still have been billed. Use provider-side spending caps; this service does not enforce a currency budget.

## Assertions and metrics

Dataset creation accepts:

```json
{
  "name": "Support contract checks",
  "cases": [
    {"prompt":"Add: 2 and 2","expected":"4","match":"exact"},
    {"prompt":"Return JSON with status equal to ok","expected":"{\"status\":\"ok\"}","match":"json"},
    {"prompt":"Lowercase: APPROVED","expected":"approved","match":"contains","forbidden":["denied"]}
  ]
}
```

- `exact`: case-sensitive equality after trimming surrounding whitespace.
- `contains`: case-insensitive Unicode substring comparison.
- `json`: parsed JSON equality, independent of object key order; not JSON Schema validation.
- `forbidden`: case-insensitive substring assertions. A match fails the case even when the expected-answer check passes. This is not a comprehensive safety evaluator.
- Pass rate: passed cases divided by **all dataset cases**; partial runs are labeled as partial and errors do not disappear from the denominator.
- P50/P95: linearly interpolated latency percentiles over successful requests. They measure full non-streaming completion time, not time-to-first-token. A failed assertion can still be a successful provider request.
- Compare: only completed runs with identical dataset fingerprints; reports percentage-point change and case-level regressions/improvements. No statistical significance is claimed.

Datasets are immutable through the API. Runs also retain their own copy plus SHA-256 fingerprint, model, instruction, token limit, and pricing. Provider model aliases can change upstream, so a snapshot improves reproducibility but cannot guarantee bit-identical output.

## API

OpenAPI JSON: `/api/openapi.json`. Liveness: `/health`; database readiness: `/ready`.

All dataset, run, comparison, and export data require authentication. API clients can use `Authorization: Bearer $ADMIN_KEY`. Browser mutations also require `X-CSRF-Token`, retrieved through the authenticated `/api/me` endpoint. New runs require an `Idempotency-Key`: repeating the same payload returns the same run; changing payload under that key returns 409.

```bash
curl -H "Authorization: Bearer $ADMIN_KEY" "$GAUGE_URL/api/datasets"
curl -H "Authorization: Bearer $ADMIN_KEY" -H 'Content-Type: application/json' \
  -H "Idempotency-Key: $REQUEST_ID" \
  -d '{"dataset_id":"DATASET_ID","name":"Candidate","provider":"mock","model":"deterministic-v1"}' \
  "$GAUGE_URL/api/runs"
```

A run detail response is also its export payload: `GET /api/runs/{id}`. Case outputs and expected answers are sensitive application data; export them only to approved destinations.

## Verification

```bash
cd backend
uv run pytest -q
# PostgreSQL verification requires a dedicated database containing _test:
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@localhost/gauge_test uv run pytest -q
cd ../frontend
npm ci
npm run build
npx playwright install chromium
cd ..
bash scripts/e2e.sh
```

Tests drop/recreate tables in the specified test database. Never point them at live data. The browser runner uses a separate SQLite test database and loopback-only server on 9201. Read [verification](docs/verification.md), [architecture](docs/architecture.md), and [security and operations](docs/security.md).
