# Security and operations

## Assets and boundaries
Evaluation prompts, ground truth, outputs, exports, operator credentials, and provider keys are private. All evaluation data is behind operator authentication. Do not use real credentials in test prompts. Logs include request ID, HTTP method, status, and elapsed time; they exclude request/response bodies. Provider exception bodies are not retained, because they can echo prompts.

Controls include hashed session tokens, constant-time key comparison, HttpOnly/SameSite cookies, HTTPS-only cookies for HTTPS origins, CSRF protection, strict Origin checks, a restrictive CSP, no untrusted HTML rendering, ORM-bound queries, bounded request/case sizes, model allowlists, a fixed provider endpoint, and non-root Docker runtime. PostgreSQL is not exposed by Compose.

The model has no tools and cannot execute code. Malicious output is still data. Assertion-based scores can be gamed and are not evidence of general model safety. Substring checks are deliberately simple; they are not semantic grading. Real-provider calls leave the application boundary; review provider data-retention terms before uploading private examples.

## Before publishing
Use HTTPS with the correct BASE_URL, restrict access to the operator, keep database and provider secrets in a secret manager/protected environment, and set provider spending caps. Verify the complete sign-in and evaluation flow. Add a shared rate limiter if scaling API processes. Keep a single schema-migration deployment owner. This repository does not claim a penetration test or a production security certification.

## Worker failure
Inspect running-job heartbeats and worker health. After three minutes, stale jobs become interrupted when any worker runs its recovery cycle. Preserve partial results. Do not manually reset a paid run to queued: start a new explicit run after checking provider usage. Avoid treating interrupted/errored usage as zero cost.

## Credential exposure
Rotate the provider key at the provider and restart workers. Rotate ADMIN_KEY and restart the API to invalidate operator sessions. Keep secrets out of issue comments, screenshots, commits, and request logs. Review access to database copies and exported results.

## Persistence and recovery
SQLite local development uses WAL and one shared database file; back it up using SQLite's backup API, not an arbitrary copy of a live DB file without its WAL. Compose uses PostgreSQL with a named volume. Take `pg_dump` backups to a separate protected location and test restoration. Do not remove named volumes during a normal update.

## Intentional limits
No multi-user tenancy, object-level ACLs, semantic/LLM judge, streaming inference, currency-budget enforcement, GPU serving, automatic dataset labeling, immutable audit trail, distributed traces, or public hosting is claimed. Worker lease recovery favors avoiding duplicate billing over automatic availability. Keep large/expensive benchmarks on an explicitly budgeted provider account.
