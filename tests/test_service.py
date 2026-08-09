from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

import recalllease.service as service_module
import recalllease.store as store_module
from recalllease.embeddings import DeterministicEmbeddingProvider
from recalllease.models import (
    ActionDecision,
    ActionRequest,
    MemoryCreate,
    MemoryEffect,
    MemoryKind,
    utc_now,
)
from recalllease.receipts import NullReceiptSink
from recalllease.service import RecallLeaseService
from recalllease.store import InMemoryStore, PublicSessionLimitReached, SessionError


class RacingStore(InMemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.save_attempts = 0

    def save_receipt(
        self,
        receipt,
        *,
        expected_memory_version: int,
        decision_valid_before,
    ) -> bool:
        self.save_attempts += 1
        if self.save_attempts == 1:
            active_permission = next(
                memory
                for memory in self.list_memories(tenant_id=receipt.tenant_id)
                if memory.effect == MemoryEffect.ALLOW and memory.status.value == "active"
            )
            content = (
                "The agent must not publish the weekly project status. "
                "A concurrent policy update revoked that permission."
            )
            self.add_memory(
                tenant_id=receipt.tenant_id,
                memory=MemoryCreate(
                    kind=MemoryKind.PERMISSION,
                    effect=MemoryEffect.DENY,
                    subject="Public weekly status publishing",
                    content=content,
                    source="concurrent-policy-update",
                    supersedes_id=active_permission.id,
                ),
                embedding=DeterministicEmbeddingProvider().embed(
                    "Publish the weekly status publicly. Publish a sanitized weekly project status."
                ),
            )
        return super().save_receipt(
            receipt,
            expected_memory_version=expected_memory_version,
            decision_valid_before=decision_valid_before,
        )


class AlwaysStaleStore(InMemoryStore):
    def save_receipt(
        self,
        receipt,
        *,
        expected_memory_version: int,
        decision_valid_before,
    ) -> bool:
        return False


class CountingTransitionStore(InMemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.save_attempts = 0

    def save_receipt(
        self,
        receipt,
        *,
        expected_memory_version: int,
        decision_valid_before,
    ) -> bool:
        self.save_attempts += 1
        return super().save_receipt(
            receipt,
            expected_memory_version=expected_memory_version,
            decision_valid_before=decision_valid_before,
        )


class RecordingReceiptSink:
    def __init__(self) -> None:
        self.digests: list[str] = []

    def put(self, *, payload, digest_sha256: str, created_at) -> str:
        self.digests.append(digest_sha256)
        return f"receipts/{created_at:%Y/%m/%d}/{digest_sha256}.json"


def service(*, hourly_limit: int = 10, use_limit: int = 4) -> RecallLeaseService:
    return RecallLeaseService(
        store=InMemoryStore(),
        embeddings=DeterministicEmbeddingProvider(),
        receipt_sink=NullReceiptSink(),
        hourly_session_limit=hourly_limit,
        session_use_limit=use_limit,
        session_ttl_minutes=30,
    )


def test_public_session_rate_limit_is_enforced() -> None:
    active_service = service(hourly_limit=1)
    active_service.create_demo_session()

    with pytest.raises(PublicSessionLimitReached):
        active_service.create_demo_session()


def test_use_budget_is_consumed_atomically() -> None:
    active_service = service(use_limit=4)
    session = active_service.create_demo_session()
    memory = MemoryCreate(
        kind=MemoryKind.INSTRUCTION,
        effect=MemoryEffect.CONTEXT,
        subject="Bounded demo use",
        content="A public demo session has a small, fixed action budget.",
    )

    for _ in range(4):
        active_service.add_memory(
            tenant_id=session.tenant_id,
            token=session.token,
            memory=memory,
        )

    with pytest.raises(SessionError):
        active_service.add_memory(
            tenant_id=session.tenant_id,
            token=session.token,
            memory=memory,
        )


def test_expired_memory_is_not_recalled() -> None:
    store = InMemoryStore()
    embeddings = DeterministicEmbeddingProvider()
    tenant_id = "demo-expiry"
    store.create_session(
        tenant_id=tenant_id,
        token_sha256="a" * 64,
        expires_at=utc_now() + timedelta(minutes=30),
        uses_remaining=4,
        hourly_limit=10,
    )
    content = "The agent may publish the weekly status."
    store.add_memory(
        tenant_id=tenant_id,
        memory=MemoryCreate(
            kind=MemoryKind.PERMISSION,
            effect=MemoryEffect.ALLOW,
            subject="Weekly status publishing",
            content=content,
            valid_from=utc_now() - timedelta(hours=2),
            valid_until=utc_now() - timedelta(hours=1),
        ),
        embedding=embeddings.embed(content),
    )

    recalled, _, _, _ = store.recall_active(
        tenant_id=tenant_id,
        embedding=embeddings.embed(content),
        limit=8,
    )

    assert recalled == []


def test_time_transition_forces_fresh_recall_before_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluated_at = datetime(2026, 8, 9, 14, 30, tzinfo=UTC)
    after_transition = evaluated_at + timedelta(seconds=1)
    transition_at = evaluated_at + timedelta(milliseconds=500)
    token = hashlib.sha256(b"transition-test-token").hexdigest()
    tenant_id = "demo-time-transition"
    query = "Publish the weekly status. Publish a sanitized weekly project status."
    embeddings = DeterministicEmbeddingProvider()
    store = CountingTransitionStore()
    monkeypatch.setattr(store_module, "utc_now", lambda: evaluated_at)
    store.create_session(
        tenant_id=tenant_id,
        token_sha256=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=evaluated_at + timedelta(hours=1),
        uses_remaining=4,
        hourly_limit=10,
    )
    store.add_memory(
        tenant_id=tenant_id,
        memory=MemoryCreate(
            kind=MemoryKind.PERMISSION,
            effect=MemoryEffect.ALLOW,
            subject="Public weekly status publishing",
            content="The agent may publish the weekly status.",
            valid_from=evaluated_at - timedelta(minutes=1),
        ),
        embedding=embeddings.embed(query),
    )
    store.add_memory(
        tenant_id=tenant_id,
        memory=MemoryCreate(
            kind=MemoryKind.PERMISSION,
            effect=MemoryEffect.DENY,
            subject="Scheduled publication revocation",
            content="The agent must not publish after the scheduled revocation.",
            valid_from=transition_at,
        ),
        embedding=embeddings.embed(query),
    )
    active_service = RecallLeaseService(
        store=store,
        embeddings=embeddings,
        receipt_sink=NullReceiptSink(),
        hourly_session_limit=10,
        session_use_limit=4,
        session_ttl_minutes=30,
    )
    store_clock = iter(
        (evaluated_at, evaluated_at, after_transition, after_transition, after_transition)
    )
    monkeypatch.setattr(store_module, "utc_now", lambda: next(store_clock))

    receipt = active_service.evaluate_action(
        tenant_id=tenant_id,
        token=token,
        request=ActionRequest(
            action="Publish the weekly status",
            intent="Publish a sanitized weekly project status.",
        ),
    )

    assert store.save_attempts == 2
    assert receipt.decision == ActionDecision.DENY
    assert receipt.created_at == after_transition
    assert len(store.list_receipts(tenant_id=tenant_id)) == 1


def test_store_clock_prevents_future_permission_from_becoming_active_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    app_now = store_now + timedelta(seconds=10)
    tenant_id = "demo-clock-skew"
    token = hashlib.sha256(b"clock-skew-test-token").hexdigest()
    query = "Publish the weekly status. Publish a sanitized weekly project status."
    embeddings = DeterministicEmbeddingProvider()
    store = InMemoryStore()
    monkeypatch.setattr(store_module, "utc_now", lambda: store_now)
    monkeypatch.setattr(service_module, "utc_now", lambda: app_now)
    store.create_session(
        tenant_id=tenant_id,
        token_sha256=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=store_now + timedelta(hours=1),
        uses_remaining=4,
        hourly_limit=10,
    )
    store.add_memory(
        tenant_id=tenant_id,
        memory=MemoryCreate(
            kind=MemoryKind.PERMISSION,
            effect=MemoryEffect.ALLOW,
            subject="Future publication permission",
            content="The agent may publish once this permission becomes active.",
            valid_from=store_now + timedelta(seconds=5),
        ),
        embedding=embeddings.embed(query),
    )
    active_service = RecallLeaseService(
        store=store,
        embeddings=embeddings,
        receipt_sink=NullReceiptSink(),
        hourly_session_limit=10,
        session_use_limit=4,
        session_ttl_minutes=30,
    )

    receipt = active_service.evaluate_action(
        tenant_id=tenant_id,
        token=token,
        request=ActionRequest(
            action="Publish the weekly status",
            intent="Publish a sanitized weekly project status.",
        ),
    )

    assert receipt.decision == ActionDecision.REVIEW
    assert receipt.created_at == store_now
    assert receipt.recalled_memory_ids == []


def test_expiry_during_embedding_prevents_memory_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started_at = datetime(2026, 8, 10, 13, 0, tzinfo=UTC)
    clock = {"now": started_at}
    tenant_id = "demo-expiring-write"
    token = hashlib.sha256(b"expiring-write-test-token").hexdigest()
    store = InMemoryStore()
    monkeypatch.setattr(store_module, "utc_now", lambda: clock["now"])
    store.create_session(
        tenant_id=tenant_id,
        token_sha256=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=started_at + timedelta(seconds=1),
        uses_remaining=4,
        hourly_limit=10,
    )

    class ExpiringEmbeddingProvider(DeterministicEmbeddingProvider):
        def embed(self, value: str) -> list[float]:
            clock["now"] = started_at + timedelta(seconds=2)
            return super().embed(value)

    active_service = RecallLeaseService(
        store=store,
        embeddings=ExpiringEmbeddingProvider(),
        receipt_sink=NullReceiptSink(),
        hourly_session_limit=10,
        session_use_limit=4,
        session_ttl_minutes=30,
    )

    with pytest.raises(SessionError, match="expired"):
        active_service.add_memory(
            tenant_id=tenant_id,
            token=token,
            memory=MemoryCreate(
                kind=MemoryKind.INSTRUCTION,
                effect=MemoryEffect.CONTEXT,
                subject="Late memory",
                content="This write must not commit after the demo session expires.",
            ),
        )

    assert store.list_memories(tenant_id=tenant_id) == []
    assert store._sessions[tenant_id]["memory_version"] == 0


def test_unstructured_permission_never_authorizes_an_action() -> None:
    active_service = service(use_limit=4)
    session = active_service.create_demo_session()

    matching_permission = active_service.evaluate_action(
        tenant_id=session.tenant_id,
        token=session.token,
        request=ActionRequest(
            action="Publish the weekly status publicly",
            intent="Publish a sanitized weekly project status.",
        ),
    )
    assert matching_permission.decision == ActionDecision.REVIEW

    negated_safety_condition = active_service.evaluate_action(
        tenant_id=session.tenant_id,
        token=session.token,
        request=ActionRequest(
            action="Publish the weekly status publicly",
            intent=(
                "Publish the weekly project status without removing credentials, "
                "private paths, or personal phone numbers."
            ),
        ),
    )
    assert negated_safety_condition.decision == ActionDecision.REVIEW

    active_service.relevance_floor = 1.1
    review = active_service.evaluate_action(
        tenant_id=session.tenant_id,
        token=session.token,
        request=ActionRequest(
            action="Rotate a telescope",
            intent="Point a fictional telescope at a new star.",
        ),
    )
    assert review.decision == ActionDecision.REVIEW


def test_supersedes_id_must_be_active_and_same_tenant() -> None:
    active_service = service()
    first = active_service.create_demo_session()
    second = active_service.create_demo_session()

    with pytest.raises(ValueError, match="active memory"):
        active_service.add_memory(
            tenant_id=second.tenant_id,
            token=second.token,
            memory=MemoryCreate(
                kind=MemoryKind.PERMISSION,
                effect=MemoryEffect.DENY,
                subject="Cross tenant revocation",
                content="A tenant cannot revoke another tenant's permission.",
                supersedes_id=first.initial_permission_id,
            ),
        )


def test_concurrent_revocation_forces_fresh_recall_before_receipt() -> None:
    store = RacingStore()
    sink = RecordingReceiptSink()
    active_service = RecallLeaseService(
        store=store,
        embeddings=DeterministicEmbeddingProvider(),
        receipt_sink=sink,
        hourly_session_limit=10,
        session_use_limit=4,
        session_ttl_minutes=30,
    )
    session = active_service.create_demo_session()

    receipt = active_service.evaluate_action(
        tenant_id=session.tenant_id,
        token=session.token,
        request=ActionRequest(
            action="Publish the weekly status publicly",
            intent="Publish a sanitized weekly project status.",
        ),
    )

    assert store.save_attempts == 2
    assert receipt.decision == ActionDecision.DENY
    assert receipt.s3_key is not None
    assert sink.digests == [receipt.digest_sha256]
    saved = store.list_receipts(tenant_id=session.tenant_id)
    assert len(saved) == 1
    assert saved[0].s3_key == receipt.s3_key


def test_repeated_memory_changes_fail_without_fabricated_receipt() -> None:
    store = AlwaysStaleStore()
    active_service = RecallLeaseService(
        store=store,
        embeddings=DeterministicEmbeddingProvider(),
        receipt_sink=NullReceiptSink(),
        hourly_session_limit=10,
        session_use_limit=4,
        session_ttl_minutes=30,
    )
    session = active_service.create_demo_session()

    with pytest.raises(RuntimeError, match="Memory changed repeatedly"):
        active_service.evaluate_action(
            tenant_id=session.tenant_id,
            token=session.token,
            request=ActionRequest(
                action="Publish the weekly status publicly",
                intent="Publish a sanitized weekly project status.",
            ),
        )

    assert store.list_receipts(tenant_id=session.tenant_id) == []
