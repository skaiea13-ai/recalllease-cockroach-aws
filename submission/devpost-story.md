# RecallLease

Memory that expires safely.

## Inspiration

Most agent memory demos focus on recall. I was more interested in what happens
after a permission is revoked. If a restart can bring an old grant back,
persistence has become a security bug.

RecallLease started with one deliberately small scenario. An agent may publish
a sanitized weekly status, then a later policy revokes that permission. The
test is whether a fresh agent can avoid acting on the old grant.

## What it does

RecallLease treats memory as a lease with a source, validity window, status,
content digest, and optional supersession link. The demo writes an initial
permission, records a newer denial, and starts a fresh agent instance. In the
cloud profile, the new agent retrieves policy memory from CockroachDB instead
of trusting process-local state.

The result is `BLOCKED`. The older permission stays in the ledger for audit
purposes, but its status is `superseded`, so it cannot authorize an action. A
content-addressed receipt records the agent instance, retrieval-query hash,
memory-set digest, evidence IDs, decision, and decision time.

Semantic similarity is used to find evidence, never to grant authority. A
matching denial can block automatically; anything else returns `needs_review`
until a separate structured authorization mechanism is added.

## How I built it

The CockroachDB adapter is the system of record for sessions, versioned policy
memory, 1,024-dimensional vectors, and action receipts. One serializable
transaction supersedes the old policy and inserts the new one. Distributed
Vector Indexing retrieves the closest active memories without splitting vector
state from the transactional ledger.

I used `ccloud` CLI to create and inspect a resource-capped Basic cluster. This
is the second CockroachDB tool in the project. The runtime profile packages a
FastAPI application for AWS Lambda with AWS SAM. Lambda computes deterministic
query vectors in process, so the template grants no paid model-inference
permission. The cloud profile writes private, content-addressed receipts to
Amazon S3 with a seven-day lifecycle.

The template gives the hosted Function URL AWS IAM authentication. The browser
walkthrough runs on loopback with the CockroachDB and S3 adapters. A random
capability protects the loopback page and disappears from the address bar
before recording. Credentials and account screens stay out of the video, and
the hosted endpoint never becomes anonymous.

A separate public replay runs the same fixed policy story entirely in the
browser. It is labeled as a fixture, makes no cloud claim, and uses a CSP that
forbids network API calls. The continuous video is the evidence for the real
CockroachDB and AWS path.

## The consistency problem

The first version retrieved memory and then saved a decision. That leaves a
small but real race: a revocation or validity boundary can arrive between those
two operations.

RecallLease now asks the store for four values together: the active memories,
the tenant's memory version, the next scheduled policy transition, and a
store-authoritative evaluation time. The receipt insert succeeds only if the
version still matches, the session is still live, and the scheduled transition
has not arrived. Otherwise, the service discards the stale decision and
retrieves again. Memory writes also recheck session expiry inside the commit
transaction.

This matters for clock skew too. A Lambda host with a clock a few seconds ahead
cannot activate a future permission because CockroachDB supplies the decision
time used by both retrieval and the receipt.

## Challenges I ran into

The hard part was keeping the demo honest without making it hard to follow.
The interface has to show the superseded grant for audit context, even though
only active memory may affect the decision. It also has to distinguish the
offline replay from the cloud path without implying that local data came from
CockroachDB.

Cost controls changed the architecture. The Lambda URL is IAM-only,
concurrency is capped at two, sessions have a fixed use budget, S3 objects have
a short lifecycle, and the default embedding path makes no remote model call.
The SAM build copies an exact 15-file runtime manifest, which keeps untracked
notes, local caches, tests, design files, and environment files out of the
deployment artifact. The Lambda environment holds only a Parameter Store name,
not the password-bearing database URL.

## Verification

The current suite has 72 tests and 90.50 percent branch-aware coverage. It
includes regressions for cross-site session creation, application and database
clock skew, expiry during a memory write, concurrent policy changes, tenant
isolation, exhausted session budgets, unstructured permission matches,
cloud-loopback authentication, exact Lambda packaging, and encrypted parameter
retrieval. The cloud gate also rejects a non-Free AWS account, the wrong account
identity, expired credits, an oversized CockroachDB cluster, a nonzero draft
invoice, or missing trial-credit coverage. Ruff, CloudFormation linting, the
frozen dependency export, gitleaks, and a local AWS SAM build are separate
release gates.

The browser replay was checked at desktop size and at 390 by 844 pixels. Both
paths finish with two policy records, a populated receipt, and a visible
`BLOCKED` decision without horizontal overflow.

## What I learned

I came away thinking about agent memory as a time and consistency problem. A
correct vector match is not enough. The system also needs to know whether the
record is active at the store's time and whether that answer stayed true until
the decision was committed.

## What's next

The same lease model can cover tool permissions, delegated credentials, and
time-limited approvals. A production version would add organization-managed
policy namespaces and cross-region revocation drills while keeping the same
deny-first decision and receipt contract.

## Built with

- CockroachDB Cloud
- CockroachDB Distributed Vector Indexing
- `ccloud` CLI
- AWS Lambda
- Amazon S3
- AWS SAM and CloudFormation
- FastAPI, Psycopg, Pydantic, and Mangum

## AI use disclosure

OpenAI Codex and image generation were used during implementation and interface
concept development. I reviewed the executable behavior, security controls,
tests, and submission artifacts. The demo narration uses a locally generated
Qwen3-TTS Aiden voice in a calm tone.
