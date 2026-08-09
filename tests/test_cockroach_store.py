from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from psycopg import errors

import recalllease.store as store_module
from recalllease.models import (
    ActionDecision,
    MemoryCreate,
    MemoryEffect,
    MemoryKind,
    MemoryStatus,
)
from recalllease.store import CockroachStore, PublicSessionLimitReached, SessionError


class FakeConnection:
    def transaction(self):
        return nullcontext()


class FakePool:
    def __init__(self) -> None:
        self.connection_value = FakeConnection()

    def connection(self):
        return nullcontext(self.connection_value)


class EmptyResult:
    def fetchone(self):
        return None


class StubResult:
    def __init__(self, *, one=None, all_rows=None) -> None:
        self.one = one
        self.all_rows = all_rows or []

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.all_rows


class RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...] | None]] = []

    def execute(self, statement: str, params: tuple[object, ...] | None = None) -> EmptyResult:
        self.calls.append((" ".join(statement.split()), params))
        return EmptyResult()


class ScriptedConnection(RecordingConnection):
    def __init__(self, results: list[StubResult]) -> None:
        super().__init__()
        self.results = iter(results)

    def execute(self, statement: str, params: tuple[object, ...] | None = None) -> StubResult:
        self.calls.append((" ".join(statement.split()), params))
        return next(self.results)


def test_serializable_operation_retries_with_bounded_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = object.__new__(CockroachStore)
    store._pool = FakePool()
    calls = 0
    sleeps: list[float] = []

    def operation(connection: FakeConnection) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise errors.SerializationFailure("retry transaction")
        return "committed"

    monkeypatch.setattr(store_module.secrets, "randbelow", lambda _: 5)
    monkeypatch.setattr(store_module.time, "sleep", sleeps.append)

    assert store._run_serializable(operation) == "committed"
    assert calls == 2
    assert sleeps == [0.055]


def test_exhausted_session_window_stops_mutating_the_counter() -> None:
    connection = RecordingConnection()
    store = object.__new__(CockroachStore)
    store._run_serializable = lambda operation: operation(connection)

    with pytest.raises(PublicSessionLimitReached):
        store.create_session(
            tenant_id="demo-limit",
            token_sha256="0" * 64,
            expires_at=datetime(2026, 8, 9, 15, 0, tzinfo=UTC),
            uses_remaining=8,
            hourly_limit=20,
        )

    assert len(connection.calls) == 1
    statement, params = connection.calls[0]
    assert "WHERE session_rate_windows.created_count < %s" in statement
    assert params == (20,)


def test_recall_uses_database_time_for_filtering_and_receipt_time() -> None:
    evaluated_at = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    transition_at = datetime(2026, 8, 10, 13, 0, tzinfo=UTC)
    connection = ScriptedConnection(
        [
            StubResult(one={"memory_version": 7, "evaluated_at": evaluated_at}),
            StubResult(all_rows=[]),
            StubResult(one={"next_transition_at": transition_at}),
        ]
    )
    store = object.__new__(CockroachStore)
    store._run_serializable = lambda operation: operation(connection)

    recalled, version, transition, returned_time = store.recall_active(
        tenant_id="demo-database-clock",
        embedding=[0.25, 0.75],
        limit=8,
    )

    assert recalled == []
    assert version == 7
    assert transition == transition_at
    assert returned_time == evaluated_at
    first_statement, _ = connection.calls[0]
    assert "SELECT memory_version, now() AS evaluated_at" in first_statement
    _, recall_params = connection.calls[1]
    assert recall_params == (
        "[0.25,0.75]",
        "demo-database-clock",
        evaluated_at,
        evaluated_at,
        "[0.25,0.75]",
        8,
    )
    _, transition_params = connection.calls[2]
    assert transition_params == (
        "demo-database-clock",
        evaluated_at,
        "demo-database-clock",
        evaluated_at,
    )


def test_memory_write_rechecks_session_expiry_before_commit() -> None:
    connection = ScriptedConnection(
        [
            StubResult(one={"id": uuid4()}),
            StubResult(one=None),
        ]
    )
    store = object.__new__(CockroachStore)
    store._run_serializable = lambda operation: operation(connection)

    with pytest.raises(SessionError, match="expired"):
        store.add_memory(
            tenant_id="demo-expired-write",
            memory=MemoryCreate(
                kind=MemoryKind.INSTRUCTION,
                effect=MemoryEffect.CONTEXT,
                subject="Late memory",
                content="This write must not commit after the demo session expires.",
            ),
            embedding=[0.25, 0.75],
        )

    assert len(connection.calls) == 2
    statement, params = connection.calls[1]
    assert "expires_at > now()" in statement
    assert "RETURNING memory_version" in statement
    assert params == ("demo-expired-write",)


def test_cloud_rows_restore_typed_memory_and_receipt_models() -> None:
    created_at = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    memory_id = uuid4()
    memory = store_module._memory_from_row(
        {
            "id": memory_id,
            "tenant_id": "demo-cloud",
            "kind": "permission",
            "effect": "deny",
            "subject": "Public weekly status publishing",
            "content": "The public publish permission was revoked.",
            "content_sha256": "a" * 64,
            "source": "policy-update",
            "valid_from": created_at,
            "valid_until": None,
            "status": "active",
            "supersedes_id": None,
            "embedding_text": "[0.25,0.75]",
            "relevance": 0.98,
            "created_at": created_at,
        }
    )
    receipt_id = uuid4()
    receipt = store_module._receipt_from_row(
        {
            "id": receipt_id,
            "tenant_id": "demo-cloud",
            "action": "Publish status",
            "intent": "Publish a sanitized status",
            "decision": "deny",
            "reason": "Blocked by active memory.",
            "recalled_memory_ids": [memory_id],
            "agent_instance_id": "agent-cloud",
            "retrieval_query_sha256": "b" * 64,
            "memory_set_digest_sha256": "c" * 64,
            "created_at": created_at,
            "digest_sha256": "d" * 64,
            "s3_key": "receipts/2026/08/09/receipt.json",
        }
    )

    assert memory.effect == MemoryEffect.DENY
    assert memory.status == MemoryStatus.ACTIVE
    assert memory.embedding == [0.25, 0.75]
    assert memory.relevance == 0.98
    assert receipt.id == receipt_id
    assert receipt.decision == ActionDecision.DENY
    assert receipt.recalled_memory_ids == [memory_id]
    assert receipt.s3_key is not None
    assert store_module._vector_text([0.1234567894, 1.0]) == "[0.123456789,1.0]"
