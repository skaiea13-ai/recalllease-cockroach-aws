from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

from psycopg import Connection, errors
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from recalllease.embeddings import cosine_similarity
from recalllease.models import (
    ActionDecision,
    ActionReceipt,
    MemoryCreate,
    MemoryEffect,
    MemoryKind,
    MemoryRecord,
    MemoryStatus,
    utc_now,
)
from recalllease.schema import SCHEMA_VALIDATION_STATEMENTS


class SessionError(RuntimeError):
    pass


class PublicSessionLimitReached(SessionError):
    pass


class MemoryStore(Protocol):
    def initialize(self) -> None: ...

    def create_session(
        self,
        *,
        tenant_id: str,
        token_sha256: str,
        expires_at: datetime,
        uses_remaining: int,
        hourly_limit: int,
    ) -> None: ...

    def authorize_session(
        self,
        *,
        tenant_id: str,
        token_sha256: str,
        consume: bool,
    ) -> int: ...

    def add_memory(
        self,
        *,
        tenant_id: str,
        memory: MemoryCreate,
        embedding: list[float],
    ) -> MemoryRecord: ...

    def recall_active(
        self,
        *,
        tenant_id: str,
        embedding: list[float],
        limit: int,
    ) -> tuple[list[MemoryRecord], int, datetime | None, datetime]: ...

    def list_memories(self, *, tenant_id: str) -> list[MemoryRecord]: ...

    def save_receipt(
        self,
        receipt: ActionReceipt,
        *,
        expected_memory_version: int,
        decision_valid_before: datetime | None,
    ) -> bool: ...

    def attach_receipt_archive(self, *, receipt_id: UUID, s3_key: str) -> None: ...

    def list_receipts(self, *, tenant_id: str) -> list[ActionReceipt]: ...


class InMemoryStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, dict[str, object]] = {}
        self._memories: dict[str, list[MemoryRecord]] = {}
        self._receipts: dict[str, list[ActionReceipt]] = {}
        self._hourly_counts: dict[datetime, int] = {}

    def initialize(self) -> None:
        return None

    def create_session(
        self,
        *,
        tenant_id: str,
        token_sha256: str,
        expires_at: datetime,
        uses_remaining: int,
        hourly_limit: int,
    ) -> None:
        with self._lock:
            window = utc_now().replace(minute=0, second=0, microsecond=0)
            count = self._hourly_counts.get(window, 0) + 1
            self._hourly_counts[window] = count
            if count > hourly_limit:
                raise PublicSessionLimitReached("Public demo session limit reached")
            self._sessions[tenant_id] = {
                "token_sha256": token_sha256,
                "expires_at": expires_at,
                "uses_remaining": uses_remaining,
                "memory_version": 0,
            }
            self._memories[tenant_id] = []
            self._receipts[tenant_id] = []

    def authorize_session(
        self,
        *,
        tenant_id: str,
        token_sha256: str,
        consume: bool,
    ) -> int:
        with self._lock:
            session = self._sessions.get(tenant_id)
            if session is None or session["token_sha256"] != token_sha256:
                raise SessionError("Unknown demo session")
            if session["expires_at"] <= utc_now():
                raise SessionError("Demo session expired")
            remaining = int(session["uses_remaining"])
            if consume:
                if remaining <= 0:
                    raise SessionError("Demo session use limit reached")
                remaining -= 1
                session["uses_remaining"] = remaining
            return remaining

    def add_memory(
        self,
        *,
        tenant_id: str,
        memory: MemoryCreate,
        embedding: list[float],
    ) -> MemoryRecord:
        with self._lock:
            session = self._sessions.get(tenant_id)
            if session is None or session["expires_at"] <= utc_now():
                raise SessionError("Unknown or expired demo session")
            records = self._memories[tenant_id]
            if memory.supersedes_id is not None:
                replaced = False
                updated: list[MemoryRecord] = []
                for existing in records:
                    if (
                        existing.id == memory.supersedes_id
                        and existing.status == MemoryStatus.ACTIVE
                    ):
                        existing = existing.model_copy(update={"status": MemoryStatus.SUPERSEDED})
                        replaced = True
                    updated.append(existing)
                if not replaced:
                    raise ValueError("supersedes_id must identify an active memory in this session")
                records[:] = updated
            record = _new_memory_record(tenant_id=tenant_id, memory=memory, embedding=embedding)
            records.append(record)
            session["memory_version"] = int(session["memory_version"]) + 1
            return record

    def recall_active(
        self,
        *,
        tenant_id: str,
        embedding: list[float],
        limit: int,
    ) -> tuple[list[MemoryRecord], int, datetime | None, datetime]:
        with self._lock:
            session = self._sessions.get(tenant_id)
            if session is None:
                raise SessionError("Unknown demo session")
            evaluated_at = utc_now()
            tenant_records = self._memories.get(tenant_id, [])
            records = [
                record
                for record in tenant_records
                if record.status == MemoryStatus.ACTIVE
                and record.valid_from <= evaluated_at
                and (record.valid_until is None or record.valid_until > evaluated_at)
            ]
            transitions = [
                transition
                for record in tenant_records
                if record.status == MemoryStatus.ACTIVE
                for transition in (record.valid_from, record.valid_until)
                if transition is not None and transition > evaluated_at
            ]
            ranked = sorted(
                (
                    record.model_copy(
                        update={"relevance": cosine_similarity(record.embedding, embedding)}
                    )
                    for record in records
                ),
                key=lambda record: (record.relevance or 0.0, record.created_at),
                reverse=True,
            )
            return (
                ranked[:limit],
                int(session["memory_version"]),
                min(transitions, default=None),
                evaluated_at,
            )

    def list_memories(self, *, tenant_id: str) -> list[MemoryRecord]:
        with self._lock:
            return sorted(self._memories.get(tenant_id, []), key=lambda item: item.created_at)

    def save_receipt(
        self,
        receipt: ActionReceipt,
        *,
        expected_memory_version: int,
        decision_valid_before: datetime | None,
    ) -> bool:
        with self._lock:
            session = self._sessions.get(receipt.tenant_id)
            now = utc_now()
            if (
                session is None
                or int(session["memory_version"]) != expected_memory_version
                or session["expires_at"] <= now
                or (decision_valid_before is not None and decision_valid_before <= now)
            ):
                return False
            self._receipts.setdefault(receipt.tenant_id, []).append(receipt)
            return True

    def attach_receipt_archive(self, *, receipt_id: UUID, s3_key: str) -> None:
        with self._lock:
            for tenant_id, receipts in self._receipts.items():
                self._receipts[tenant_id] = [
                    receipt.model_copy(update={"s3_key": s3_key})
                    if receipt.id == receipt_id
                    else receipt
                    for receipt in receipts
                ]

    def list_receipts(self, *, tenant_id: str) -> list[ActionReceipt]:
        with self._lock:
            return sorted(
                self._receipts.get(tenant_id, []),
                key=lambda item: item.created_at,
                reverse=True,
            )


class CockroachStore:
    def __init__(self, dsn: str) -> None:
        self._pool = ConnectionPool(
            conninfo=dsn,
            min_size=0,
            max_size=2,
            timeout=5,
            max_idle=300,
            max_lifetime=1_500,
            kwargs={"row_factory": dict_row, "application_name": "recalllease"},
            open=True,
        )

    def initialize(self) -> None:
        with self._pool.connection() as connection:
            for statement in SCHEMA_VALIDATION_STATEMENTS:
                connection.execute(statement)

    def create_session(
        self,
        *,
        tenant_id: str,
        token_sha256: str,
        expires_at: datetime,
        uses_remaining: int,
        hourly_limit: int,
    ) -> None:
        def create(connection: Connection) -> bool:
            row = connection.execute(
                """
                INSERT INTO session_rate_windows (window_start, created_count)
                VALUES (date_trunc('hour', now()), 1)
                ON CONFLICT (window_start) DO UPDATE
                SET created_count = session_rate_windows.created_count + 1
                WHERE session_rate_windows.created_count < %s
                RETURNING created_count
                """,
                (hourly_limit,),
            ).fetchone()
            exceeded = row is None
            if not exceeded:
                connection.execute(
                    """
                    INSERT INTO demo_sessions
                        (tenant_id, token_sha256, expires_at, uses_remaining)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (tenant_id, token_sha256, expires_at, uses_remaining),
                )
            return exceeded

        exceeded = self._run_serializable(create)
        if exceeded:
            raise PublicSessionLimitReached("Public demo session limit reached")

    def authorize_session(
        self,
        *,
        tenant_id: str,
        token_sha256: str,
        consume: bool,
    ) -> int:
        with self._pool.connection() as connection:
            if consume:
                row = connection.execute(
                    """
                    UPDATE demo_sessions
                    SET uses_remaining = uses_remaining - 1
                    WHERE tenant_id = %s
                      AND token_sha256 = %s
                      AND expires_at > now()
                      AND uses_remaining > 0
                    RETURNING uses_remaining
                    """,
                    (tenant_id, token_sha256),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT uses_remaining
                    FROM demo_sessions
                    WHERE tenant_id = %s
                      AND token_sha256 = %s
                      AND expires_at > now()
                    """,
                    (tenant_id, token_sha256),
                ).fetchone()
            if row is None:
                raise SessionError("Unknown, expired, or exhausted demo session")
            return int(row["uses_remaining"])

    def add_memory(
        self,
        *,
        tenant_id: str,
        memory: MemoryCreate,
        embedding: list[float],
    ) -> MemoryRecord:
        def insert(connection: Connection) -> MemoryRecord:
            if memory.supersedes_id is not None:
                replaced = connection.execute(
                    """
                    UPDATE memories
                    SET status = 'superseded'
                    WHERE id = %s AND tenant_id = %s AND status = 'active'
                    RETURNING id
                    """,
                    (memory.supersedes_id, tenant_id),
                ).fetchone()
                if replaced is None:
                    raise ValueError("supersedes_id must identify an active memory in this session")
            row = connection.execute(
                """
                INSERT INTO memories (
                    tenant_id, kind, effect, subject, content, content_sha256,
                    source, valid_from, valid_until, status, supersedes_id, embedding
                ) VALUES (%s, %s, %s, %s, %s, %s,
                          %s, %s, %s, 'active', %s, %s::VECTOR)
                RETURNING id, tenant_id, kind, effect, subject, content,
                          content_sha256, source, valid_from, valid_until, status,
                          supersedes_id, created_at, embedding::STRING AS embedding_text
                """,
                (
                    tenant_id,
                    memory.kind.value,
                    memory.effect.value,
                    memory.subject,
                    memory.content,
                    hashlib.sha256(memory.content.encode("utf-8")).hexdigest(),
                    memory.source,
                    memory.valid_from,
                    memory.valid_until,
                    memory.supersedes_id,
                    _vector_text(embedding),
                ),
            ).fetchone()
            version_row = connection.execute(
                """
                UPDATE demo_sessions
                SET memory_version = memory_version + 1
                WHERE tenant_id = %s AND expires_at > now()
                RETURNING memory_version
                """,
                (tenant_id,),
            ).fetchone()
            if version_row is None:
                raise SessionError("Unknown or expired demo session")
            return _memory_from_row(row)

        return self._run_serializable(insert)

    def recall_active(
        self,
        *,
        tenant_id: str,
        embedding: list[float],
        limit: int,
    ) -> tuple[list[MemoryRecord], int, datetime | None, datetime]:
        def recall(
            connection: Connection,
        ) -> tuple[list[MemoryRecord], int, datetime | None, datetime]:
            version_row = connection.execute(
                """
                SELECT memory_version, now() AS evaluated_at
                FROM demo_sessions
                WHERE tenant_id = %s
                """,
                (tenant_id,),
            ).fetchone()
            if version_row is None:
                raise SessionError("Unknown demo session")
            evaluated_at = version_row["evaluated_at"]
            rows = connection.execute(
                """
                SELECT id, tenant_id, kind, effect, subject, content,
                       content_sha256, source, valid_from, valid_until, status,
                       supersedes_id, created_at, embedding::STRING AS embedding_text,
                       1 - (embedding <=> %s::VECTOR) AS relevance
                FROM memories
                WHERE tenant_id = %s
                  AND status = 'active'
                  AND valid_from <= %s
                  AND (valid_until IS NULL OR valid_until > %s)
                ORDER BY embedding <=> %s::VECTOR, created_at DESC
                LIMIT %s
                """,
                (
                    _vector_text(embedding),
                    tenant_id,
                    evaluated_at,
                    evaluated_at,
                    _vector_text(embedding),
                    limit,
                ),
            ).fetchall()
            transition_row = connection.execute(
                """
                SELECT min(transition_at) AS next_transition_at
                FROM (
                    SELECT valid_from AS transition_at
                    FROM memories
                    WHERE tenant_id = %s
                      AND status = 'active'
                      AND valid_from > %s
                    UNION ALL
                    SELECT valid_until AS transition_at
                    FROM memories
                    WHERE tenant_id = %s
                      AND status = 'active'
                      AND valid_until > %s
                ) AS policy_transitions
                """,
                (tenant_id, evaluated_at, tenant_id, evaluated_at),
            ).fetchone()
            return (
                [_memory_from_row(row) for row in rows],
                int(version_row["memory_version"]),
                transition_row["next_transition_at"],
                evaluated_at,
            )

        return self._run_serializable(recall)

    def list_memories(self, *, tenant_id: str) -> list[MemoryRecord]:
        with self._pool.connection() as connection:
            rows = connection.execute(
                """
                SELECT id, tenant_id, kind, effect, subject, content,
                       content_sha256, source, valid_from, valid_until, status,
                       supersedes_id, created_at, embedding::STRING AS embedding_text
                FROM memories
                WHERE tenant_id = %s
                ORDER BY created_at
                """,
                (tenant_id,),
            ).fetchall()
            return [_memory_from_row(row) for row in rows]

    def save_receipt(
        self,
        receipt: ActionReceipt,
        *,
        expected_memory_version: int,
        decision_valid_before: datetime | None,
    ) -> bool:
        with self._pool.connection() as connection:
            row = connection.execute(
                """
                INSERT INTO action_receipts (
                    id, tenant_id, action, intent, decision, reason,
                    recalled_memory_ids, agent_instance_id, retrieval_query_sha256,
                    memory_set_digest_sha256, created_at, digest_sha256, s3_key
                )
                SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                FROM demo_sessions
                WHERE tenant_id = %s
                  AND memory_version = %s
                  AND expires_at > now()
                  AND (
                    %s::TIMESTAMPTZ IS NULL
                    OR now() < %s::TIMESTAMPTZ
                  )
                RETURNING id
                """,
                (
                    receipt.id,
                    receipt.tenant_id,
                    receipt.action,
                    receipt.intent,
                    receipt.decision.value,
                    receipt.reason,
                    receipt.recalled_memory_ids,
                    receipt.agent_instance_id,
                    receipt.retrieval_query_sha256,
                    receipt.memory_set_digest_sha256,
                    receipt.created_at,
                    receipt.digest_sha256,
                    receipt.s3_key,
                    receipt.tenant_id,
                    expected_memory_version,
                    decision_valid_before,
                    decision_valid_before,
                ),
            ).fetchone()
            return row is not None

    def attach_receipt_archive(self, *, receipt_id: UUID, s3_key: str) -> None:
        with self._pool.connection() as connection:
            connection.execute(
                """
                UPDATE action_receipts
                SET s3_key = %s
                WHERE id = %s AND s3_key IS NULL
                """,
                (s3_key, receipt_id),
            )

    def list_receipts(self, *, tenant_id: str) -> list[ActionReceipt]:
        with self._pool.connection() as connection:
            rows = connection.execute(
                """
                SELECT id, tenant_id, action, intent, decision, reason,
                       recalled_memory_ids, agent_instance_id, retrieval_query_sha256,
                       memory_set_digest_sha256, created_at, digest_sha256, s3_key
                FROM action_receipts
                WHERE tenant_id = %s
                ORDER BY created_at DESC
                """,
                (tenant_id,),
            ).fetchall()
            return [_receipt_from_row(row) for row in rows]

    def _run_serializable[T](
        self,
        operation: Callable[[Connection], T],
        *,
        attempts: int = 4,
    ) -> T:
        for attempt in range(attempts):
            try:
                with self._pool.connection() as connection, connection.transaction():
                    return operation(connection)
            except errors.SerializationFailure:
                if attempt == attempts - 1:
                    raise
                delay = (0.05 * (2**attempt)) + (secrets.randbelow(25) / 1_000)
                time.sleep(delay)
        raise AssertionError("serialization retry loop exhausted unexpectedly")


def _new_memory_record(
    *, tenant_id: str, memory: MemoryCreate, embedding: list[float]
) -> MemoryRecord:
    return MemoryRecord(
        **memory.model_dump(),
        id=uuid4(),
        tenant_id=tenant_id,
        status=MemoryStatus.ACTIVE,
        embedding=embedding,
        content_sha256=hashlib.sha256(memory.content.encode("utf-8")).hexdigest(),
        created_at=utc_now(),
    )


def _vector_text(embedding: Sequence[float]) -> str:
    return json.dumps([round(float(value), 9) for value in embedding], separators=(",", ":"))


def _memory_from_row(row: dict[str, object]) -> MemoryRecord:
    return MemoryRecord(
        id=UUID(str(row["id"])),
        tenant_id=str(row["tenant_id"]),
        kind=MemoryKind(str(row["kind"])),
        effect=MemoryEffect(str(row["effect"])),
        subject=str(row["subject"]),
        content=str(row["content"]),
        content_sha256=str(row["content_sha256"]),
        source=str(row["source"]),
        valid_from=row["valid_from"],
        valid_until=row["valid_until"],
        status=MemoryStatus(str(row["status"])),
        supersedes_id=UUID(str(row["supersedes_id"])) if row["supersedes_id"] else None,
        embedding=json.loads(str(row["embedding_text"])),
        relevance=float(row["relevance"]) if row.get("relevance") is not None else None,
        created_at=row["created_at"],
    )


def _receipt_from_row(row: dict[str, object]) -> ActionReceipt:
    return ActionReceipt(
        id=UUID(str(row["id"])),
        tenant_id=str(row["tenant_id"]),
        action=str(row["action"]),
        intent=str(row["intent"]),
        decision=ActionDecision(str(row["decision"])),
        reason=str(row["reason"]),
        recalled_memory_ids=[UUID(str(value)) for value in row["recalled_memory_ids"]],
        agent_instance_id=str(row["agent_instance_id"]),
        retrieval_query_sha256=str(row["retrieval_query_sha256"]),
        memory_set_digest_sha256=str(row["memory_set_digest_sha256"]),
        created_at=row["created_at"],
        digest_sha256=str(row["digest_sha256"]),
        s3_key=str(row["s3_key"]) if row["s3_key"] else None,
    )
