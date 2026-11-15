# Verification record

Verified locally on 16 November 2026 (Asia/Kolkata).

| Check | Result |
|---|---|
| Backend tests, SQLite | 17 passed |
| Same backend suite, PostgreSQL 16 | 17 passed |
| Chromium browser workflow | 1 passed |
| TypeScript and Vite production build | Passed |
| Fresh PostgreSQL Alembic migration | Passed |
| Alembic schema comparison | No upgrade operations detected |
| SQLite migration through E2E runner | Passed |
| Docker Compose configuration | Valid |
| Python lock consistency and frontend dependency installation | Passed |
| Desktop/mobile screenshots | Inspected; browser tests assert no horizontal overflow at 390px |
| Real paid provider calls | Not made; HTTP adapter exercised with mocked provider responses |
| Docker image build | Not verified; host has insufficient free disk for a multi-stage build |
| Public deployment | Not performed |

The backend suite covers authentication, session logout/CSRF, Origin checks, request limits, dataset validation, immutable snapshots, concurrent idempotent submissions, concurrent worker claims, queued/running cancellation, stale-job recovery without replay, private provider-error handling, compatible comparisons, model allowlists, actual regression detection, provider token/cost accounting, and strict JSON grading including boolean/number separation, numeric precision, non-finite constants, duplicate keys, and excessive nesting.

The browser test signs in, creates a six-case synthetic dataset, runs two real background evaluations using the deterministic mock provider, inspects the failed case, exports/parses JSON, compares runs, checks desktop/mobile layout, and signs out. The mock's 5/6 result is derived from the toy assertions; it is not an AI quality benchmark.

One non-failing Starlette/httpx compatibility deprecation warning was emitted. All dependencies are locked. No throughput, paid-provider quality, security-audit, or production-availability claim is made.
