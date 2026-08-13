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
- `SecureString` uses AWS KMS. The deployment procedure requires the live AWS
  account to be `FREE` and `ACTIVE`, have positive credits and at least seven
  days remaining, and match the active STS identity.
- Database bootstrap requires `sslmode=verify-full` and uses a separate
  administrator URL. Production accepts only the dedicated `recalllease_app`
  login to the `recalllease` database with password authentication and
  `sslmode=verify-full`; bootstrap explicitly revokes its default `admin`
  membership and grants no DDL, user-management, or database-ownership rights.
- Production fails closed when the private receipt bucket is missing.
- Raw session tokens and loopback capabilities are not logged, persisted, or
  placed in the DOM.
- AWS credentials are provided by the Lambda execution role.
- S3 permits no public access and uses server-side encryption.

## Database network boundary

- The live Basic cluster's DB Console allowlist is disabled. SQL is reachable
  from `0.0.0.0/0` because an ordinary Lambda Function URL has no stable egress
  address and adding VPC/NAT infrastructure would violate the zero-cost gate.
- Public reachability does not grant a database session. Production requires
  TLS `verify-full`, the Cockroach Cloud CA chain, and the random
  `recalllease_app` password.
- The runtime role has no `admin` membership or role options, cannot create a
  table, and holds exactly `SELECT`, `INSERT`, and `UPDATE` on the four required
  tables. The separate bootstrap administrator remains local and is never
  deployed.
- If a future zero-cost hosting path supplies a stable egress CIDR, replace the
  all-address SQL allowlist with that CIDR before redeploying.

## Cost boundary

- `scripts.verify_zero_cost_cloud` fails closed on missing, malformed, expired,
  Paid, or wrong-account AWS plan state.
- The exact CockroachDB cluster must be `BASIC`, `CREATED`, hosted on AWS, and
  capped at 1,000,000 RUs plus 1 GiB storage per month. Non-finite and missing
  limits are rejected.
- The current CockroachDB draft invoice must total exactly USD 0, contain a
  negative `Free trial credits` adjustment, and cover at least seven more days.
  The verifier does not treat the monthly Basic credit as available because it
  requires pay-as-you-go billing.
- The bounded CockroachDB trial has no payment method. Never add one; losing
  trial eligibility must stop deployment rather than transition to paid use.
- Never upgrade the AWS account, join AWS Organizations, or enable a feature that
  converts the account to Paid. A failed preflight means no deploy and no video.

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
