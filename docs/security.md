# Operator demo security

## Cloud front door

- The Lambda Function URL uses `AWS_IAM`; there is no anonymous hosted API.
- Only an explicitly authorized, SigV4-signed operator invocation reaches the
  Lambda runtime.
- The planned continuous browser recording uses a loopback frontend with the
  CockroachDB and S3 adapters, so no public proxy is needed.
- The Lambda template creates embeddings in process and has no Bedrock
  permission, preventing model-inference charges.

## Abuse controls

- Session creation is capped atomically at 20 per UTC hour in CockroachDB.
- Each random session token has a two-hour TTL and an eight-use API budget.
- Every tenant endpoint consumes that budget, including reads.
- Lambda reserved concurrency limits simultaneous work to two executions.
- Request models cap all user-controlled text; unneeded arbitrary payloads are
  not accepted.

## Secret handling

- The Lambda environment contains only a Parameter Store name. The dedicated
  role reads one Standard `SecureString` at cold start; the database URL is
  neither committed nor stored in Lambda configuration.
- `SecureString` uses AWS KMS. Because KMS, Lambda, S3, and CockroachDB usage can
  incur charges, the cloud profile is not deployed under the project's strict
  zero-cost gate.
- Database bootstrap requires `sslmode=verify-full` and uses a separate
  administrator URL. Production accepts only the dedicated `recalllease_app`
  login to the `recalllease` database with password authentication and
  `sslmode=verify-full`; that role has no DDL, user-management, or database-
  ownership grants.
- Production fails closed when the private receipt bucket is missing.
- Raw session tokens and loopback capabilities are not logged, persisted, or
  placed in the DOM.
- AWS credentials are provided by the Lambda execution role.
- S3 permits no public access and uses server-side encryption.

## Decision consistency

Vector similarity selects evidence but never grants positive authority. A
relevant active denial can return `deny`; every other result is
`needs_review`. This prevents a negated or adversarially similar sentence from
turning a free-text memory into an authorization.

Every memory mutation rechecks session expiry and increments a tenant-scoped
version in the same serializable transaction. Retrieval also returns the
earliest future validity transition. The store's own transaction clock supplies
one `evaluated_at` timestamp for retrieval and the canonical receipt, so an
application-host clock cannot activate a future permission. Receipt insertion
is conditional on the memory version remaining
unchanged, the session remaining valid, and the next policy transition not yet
having arrived. A revocation, expiry, or scheduled denial that races with an
action therefore invalidates the stale decision and forces a new retrieval
before any receipt can commit.

## Browser controls

The app rejects cross-site state-changing requests using `Origin` and Fetch
Metadata. A cloud-backed loopback additionally requires a random capability;
the UI reads it from a URL fragment, removes that fragment immediately, and
keeps the value only in memory. Session creation also requires the fixed
first-party browser marker. The app sets a restrictive Content Security Policy,
denies framing, disables referrer forwarding and MIME sniffing, and marks all
API responses `no-store`. It uses same-origin static assets and no analytics,
trackers, third-party fonts, or remote scripts.

## Public replay boundary

The supplemental GitHub Pages build is a browser-only fixture, not a cloud
endpoint. It visibly says that it makes no cloud calls, generates its hashes
with Web Crypto, and loads no production token or adapter. Its meta CSP sets
`connect-src 'none'`, so the page cannot call CockroachDB, AWS, or any other
network API. The build wrapper copies only the four allowlisted static assets;
the SAM wrapper separately copies an exact 15-file runtime manifest and excludes
the fixture adapter. A continuous submission video must still prove the real
CockroachDB and S3 path before those cloud claims are submitted.

## Reporting

Please do not test the demo with private or personal content. Report a
security issue privately to the repository owner through the hosting platform's
security contact rather than opening a public issue with exploit details.
