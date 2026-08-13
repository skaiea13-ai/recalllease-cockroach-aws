# Submission readiness: AWS account verification pending

Verified locally and against the bounded CockroachDB cluster on 2026-08-13 KST.

| Requirement | Evidence | State |
| --- | --- | --- |
| Agentic application with persistent memory | FastAPI policy service, CockroachDB store, vector retrieval, guarded receipts | Ready locally |
| Two CockroachDB tools | `ccloud` authenticated and inspected the live `recalllease` Basic/AWS/us-east-1 cluster; the live schema includes Distributed Vector Indexing | Cluster and least-privilege schema ready |
| At least one AWS service | IAM-only Lambda Function URL and private lifecycle-managed S3 receipt bucket in the validated SAM template | AWS Free account activation and live deployment pending |
| Public open-source repository | [GitHub repository](https://github.com/skaiea13-ai/recalllease-cockroach-aws), MIT license, complete source, and pinned Pages workflow | Public with privacy-safe release history |
| Functional demo URL | [Browser-only replay](https://skaiea13-ai.github.io/recalllease-cockroach-aws/) deployed by the exact `dist/` workflow | Public fixture; not cloud evidence |
| Under-three-minute public video | Continuous 1920×1080 cloud capture plan and reviewed 103.7-second calm Aiden narration bed | Record only after the AWS Free-plan verifier passes and the bounded demo is live |
| Demo shows CockroachDB memory layer | Script requires real CockroachDB and S3 adapters, live revocation, fresh retrieval, `BLOCKED`, and populated receipt | CockroachDB cluster and schema ready; integrated capture pending |
| AI-use disclosure | README and Devpost story identify Codex, image generation, and local synthetic Aiden narration | Ready |

Current local quality gate: 72 tests with 90.50 percent branch-aware coverage,
Ruff, Node syntax, CloudFormation lint, SAM build/validation, workflow YAML, the
hash-pinned runtime export, app-owned gitleaks scans, and the rebuilt replay all
pass. The SAM dependency directory also triggers 110 generic-key patterns, all
confined to pinned botocore example and paginator data rather than RecallLease
source. The public replay is visibly labeled as a fixture and has
`connect-src 'none'`; it is not presented as cloud evidence.

The public repository and Pages site exist, and the privacy-safe release history
is published at commit `8a8dab0e6a1660e8411837d7378e4f7bbee59182`. The live CockroachDB cluster is
`BASIC`, `AWS`, `us-east-1`, `CREATED`, and capped at 1,000,000 RUs plus 1 GiB
storage per month; `scripts.verify_zero_cost_cloud --cockroach-only` passes
against that state and the current USD 0 draft invoice with applied free-trial
credit. The dedicated schema is live; `recalllease_app` has no
`admin` membership or role options, has exactly 12 `SELECT`/`INSERT`/`UPDATE`
grants across four tables, and is denied DDL. No payment method is attached to
the CockroachDB trial. SQL remains network-reachable from all IPs because a
zero-cost Lambda path has no stable egress CIDR; DB Console access is disabled,
TLS `verify-full` and password authentication are mandatory, and the local-only
bootstrap credential is not deployed. AWS activation, the integrated cloud
demo, Devpost project, and public video are still unverified, so the entry must
not be submitted yet.
