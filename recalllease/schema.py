APP_DATABASE = "recalllease"
APP_USER = "recalllease_app"


SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS demo_sessions (
        tenant_id STRING PRIMARY KEY,
        token_sha256 STRING NOT NULL UNIQUE,
        expires_at TIMESTAMPTZ NOT NULL,
        uses_remaining INT8 NOT NULL CHECK (uses_remaining >= 0),
        memory_version INT8 NOT NULL DEFAULT 0 CHECK (memory_version >= 0),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    ) WITH (
        ttl_expiration_expression = 'expires_at',
        ttl_job_cron = '0 */4 * * *'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS session_rate_windows (
        window_start TIMESTAMPTZ PRIMARY KEY,
        created_count INT8 NOT NULL CHECK (created_count >= 0)
    ) WITH (
        ttl_expiration_expression = $$(window_start + INTERVAL '2 days')$$,
        ttl_job_cron = '0 */4 * * *'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memories (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id STRING NOT NULL REFERENCES demo_sessions (tenant_id) ON DELETE CASCADE,
        kind STRING NOT NULL CHECK (kind IN ('fact', 'instruction', 'permission', 'decision')),
        effect STRING NOT NULL CHECK (effect IN ('allow', 'deny', 'context')),
        subject STRING NOT NULL,
        content STRING NOT NULL,
        content_sha256 STRING NOT NULL,
        source STRING NOT NULL,
        valid_from TIMESTAMPTZ NOT NULL,
        valid_until TIMESTAMPTZ NULL,
        status STRING NOT NULL CHECK (status IN ('active', 'superseded', 'revoked')),
        supersedes_id UUID NULL REFERENCES memories (id),
        embedding VECTOR(1024) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        INDEX memories_tenant_created_idx (tenant_id, created_at DESC),
        INDEX memories_tenant_status_idx (tenant_id, status, valid_from, valid_until)
    )
    """,
    """
    CREATE VECTOR INDEX IF NOT EXISTS memories_tenant_embedding_idx
    ON memories (tenant_id, embedding vector_cosine_ops)
    """,
    """
    CREATE TABLE IF NOT EXISTS action_receipts (
        id UUID PRIMARY KEY,
        tenant_id STRING NOT NULL REFERENCES demo_sessions (tenant_id) ON DELETE CASCADE,
        action STRING NOT NULL,
        intent STRING NOT NULL,
        decision STRING NOT NULL CHECK (decision IN ('deny', 'needs_review')),
        reason STRING NOT NULL,
        recalled_memory_ids UUID[] NOT NULL,
        agent_instance_id STRING NOT NULL,
        retrieval_query_sha256 STRING NOT NULL,
        memory_set_digest_sha256 STRING NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        digest_sha256 STRING NOT NULL UNIQUE,
        s3_key STRING NULL,
        INDEX receipts_tenant_created_idx (tenant_id, created_at DESC)
    )
    """,
)

SCHEMA_VALIDATION_STATEMENTS = (
    "SELECT tenant_id FROM demo_sessions LIMIT 0",
    "SELECT window_start FROM session_rate_windows LIMIT 0",
    "SELECT id FROM memories LIMIT 0",
    "SELECT id FROM action_receipts LIMIT 0",
)

LEAST_PRIVILEGE_STATEMENTS = (
    "REVOKE CREATE ON SCHEMA public FROM PUBLIC",
    "REVOKE ALL ON TABLE demo_sessions FROM PUBLIC",
    "REVOKE ALL ON TABLE session_rate_windows FROM PUBLIC",
    "REVOKE ALL ON TABLE memories FROM PUBLIC",
    "REVOKE ALL ON TABLE action_receipts FROM PUBLIC",
    "GRANT USAGE ON SCHEMA public TO recalllease_app",
    "GRANT SELECT, INSERT, UPDATE ON TABLE demo_sessions TO recalllease_app",
    "GRANT SELECT, INSERT, UPDATE ON TABLE session_rate_windows TO recalllease_app",
    "GRANT SELECT, INSERT, UPDATE ON TABLE memories TO recalllease_app",
    "GRANT SELECT, INSERT, UPDATE ON TABLE action_receipts TO recalllease_app",
)
