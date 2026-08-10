# Submission readiness: blocked by the zero-cost gate

Verified locally on 2026-08-10 KST.

| Requirement | Evidence | State |
| --- | --- | --- |
| Agentic application with persistent memory | FastAPI policy service, CockroachDB store, vector retrieval, guarded receipts | Ready locally |
| Two CockroachDB tools | Distributed Vector Indexing in the schema/store; pinned `ccloud` 0.8.23 installed for bounded Basic-cluster provisioning | Not executed; cloud cost cannot be guaranteed at zero |
| At least one AWS service | IAM-only Lambda Function URL and private lifecycle-managed S3 receipt bucket in the validated SAM template | Do not deploy under the strict zero-cost rule |
| Public open-source repository | [GitHub repository](https://github.com/skaiea13-ai/recalllease-cockroach-aws), MIT license, complete source, and pinned Pages workflow | Public with privacy-safe release history |
| Functional demo URL | [Browser-only replay](https://skaiea13-ai.github.io/recalllease-cockroach-aws/) deployed by the exact `dist/` workflow | Public fixture; not cloud evidence |
| Under-three-minute public video | Continuous 1920×1080 cloud capture plan and reviewed 103.7-second calm Aiden narration bed | Do not record or upload without eligible zero-cost cloud proof |
| Demo shows CockroachDB memory layer | Script requires real CockroachDB and S3 adapters, live revocation, fresh retrieval, `BLOCKED`, and populated receipt | Capture gate not satisfied |
| AI-use disclosure | README and Devpost story identify Codex, image generation, and local synthetic Aiden narration | Ready |

Current local quality gate: 45 tests with 90.50 percent branch-aware coverage,
Ruff, Node syntax, CloudFormation lint, SAM build/validation, workflow YAML, the
hash-pinned runtime export, app-owned gitleaks scans, and the rebuilt replay all
pass. The SAM dependency directory also triggers 110 generic-key patterns, all
confined to pinned botocore example and paginator data rather than RecallLease
source. The public replay is visibly labeled as a fixture and has
`connect-src 'none'`; it is not presented as cloud evidence.

The public repository and Pages site exist, and the privacy-safe release history
is published at commit `8a8dab0e6a1660e8411837d7378e4f7bbee59182`. No cloud execution proof,
Devpost project, or public video has
been verified for this repository, so none is claimed here. Standard Parameter
Store has no additional charge, but `SecureString` uses metered AWS KMS requests;
the wider Lambda, S3, and CockroachDB profile also lacks an enforceable zero-cost
cap. RecallLease must not be submitted as a cloud entry under the current rule.
