# RecallLease architecture and trust boundaries

## Decision path

1. The browser requests a short-lived demo session. The raw random token is
   returned once; only its SHA-256 digest is stored.
2. The initial permission is embedded and written to CockroachDB with source,
   validity, status, and content digest.
3. Lambda creates a deterministic 1,024-dimensional vector for the revocation.
   In one CockroachDB transaction, the earlier permission becomes `superseded`
   and the denial becomes `active`.
4. The action request creates a new agent-instance identifier and a vector
   retrieval query. CockroachDB supplies one fixed `evaluated_at` from its own
   transaction clock and returns only
   active, currently valid records, the tenant's current `memory_version`, and
   the earliest upcoming validity transition.
5. Any relevant active denial wins. Free-text vector retrieval is evidence, not
   a positive authorization mechanism, so every non-denied action becomes
   `needs_review`.
6. The canonical decision is SHA-256 hashed and inserted only when the tenant's
   `memory_version` still matches the retrieved version, the session has not
   expired, and no time-driven policy transition has arrived. A concurrent
   change or validity boundary discards the stale decision and retries retrieval
   instead of recording an obsolete result.
7. After the guarded CockroachDB insert commits, the receipt is written to a
   private S3 object keyed by its digest and the object key is attached to the
   database record.

## Trust boundaries

| Boundary | Untrusted input | Enforcement |
| --- | --- | --- |
| Operator → hosted Lambda | Network request | AWS IAM Function URL authentication and SigV4 |
| Browser → API | Paths, JSON, headers | Random cloud-loopback capability or IAM boundary, first-party client marker, Origin/Fetch Metadata checks, Pydantic bounds, exact methods, CSP/CORS, no-store |
| Demo token → tenant | Token and tenant ID | SHA-256 comparison, TTL, atomic use budget |
| Memory write → policy ledger | Content, validity, supersedes ID | Length/time validation and same-tenant active-row update |
| Retrieval → decision | Semantic matches and concurrent writes | Status/validity filter, deny-only automatic decision, guarded memory version |
| Lambda embedding boundary | Bounded memory/action text | In-process deterministic vectorization; no remote model permission or call |
| Lambda → S3 | Canonical receipt only | Private bucket, AES-256, content-addressed key, seven-day expiry |

## Failure behavior

- Production refuses an in-memory backend.
- Missing Parameter Store, CockroachDB, or receipt-bucket configuration stops
  production startup.
- Production rejects any runtime DSN that is not the dedicated database/login
  with password authentication and `sslmode=verify-full`.
- An unknown, expired, cross-tenant, or exhausted token returns `401`.
- A missing active superseded memory or a session that expires during a memory
  write commits no mutation.
- A concurrent memory change, session expiry, or scheduled validity transition
  forces a fresh retrieval; three consecutive conflicts fail without
  fabricating a receipt.
- Free-text retrieval never returns `allow`; no matching denial becomes
  `needs_review`.
- An unexpected vector or database failure produces no fabricated decision.

## Data minimization

The scenario contains fictional policy text. It does not ask for or
persist a name, email, password, phone number, location, private path, or source
file. Demo sessions expire after two hours. S3 receipts expire after seven days.
The browser keeps the raw token and optional loopback capability only in
JavaScript memory and never writes either to cookies or persistent browser
storage. It removes the capability fragment from the address bar immediately.

## Database privilege boundary

`scripts/bootstrap_database.py` is the only path that accepts an administrator
connection. It creates the dedicated database, schema, vector index, and runtime
login. The deployed app connects as `recalllease_app`, which receives only the
table reads and writes needed for sessions, memories, and receipts. Runtime
startup validates existing tables and does not execute DDL.
