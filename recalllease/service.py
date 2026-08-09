from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta
from uuid import uuid4

from recalllease.embeddings import EmbeddingProvider
from recalllease.models import (
    ActionDecision,
    ActionReceipt,
    ActionRequest,
    DemoSession,
    DemoState,
    MemoryCreate,
    MemoryEffect,
    MemoryKind,
    MemoryRecord,
    utc_now,
)
from recalllease.receipts import ReceiptSink
from recalllease.store import MemoryStore


class RecallLeaseService:
    relevance_floor = 0.08

    def __init__(
        self,
        *,
        store: MemoryStore,
        embeddings: EmbeddingProvider,
        receipt_sink: ReceiptSink,
        hourly_session_limit: int,
        session_use_limit: int,
        session_ttl_minutes: int,
    ) -> None:
        self._store = store
        self._embeddings = embeddings
        self._receipt_sink = receipt_sink
        self._hourly_session_limit = hourly_session_limit
        self._session_use_limit = session_use_limit
        self._session_ttl_minutes = session_ttl_minutes

    def create_demo_session(self) -> DemoSession:
        token = secrets.token_urlsafe(32)
        tenant_id = f"demo-{uuid4().hex[:16]}"
        expires_at = utc_now() + timedelta(minutes=self._session_ttl_minutes)
        self._store.create_session(
            tenant_id=tenant_id,
            token_sha256=_sha256(token),
            expires_at=expires_at,
            uses_remaining=self._session_use_limit,
            hourly_limit=self._hourly_session_limit,
        )
        permission = self._record_memory(
            tenant_id=tenant_id,
            memory=MemoryCreate(
                kind=MemoryKind.PERMISSION,
                effect=MemoryEffect.ALLOW,
                subject="Public weekly status publishing",
                content=(
                    "The agent may publish the weekly project status after it removes "
                    "credentials, private paths, and personal phone numbers."
                ),
                source="demo-user",
                valid_until=expires_at,
            ),
        )
        self._record_memory(
            tenant_id=tenant_id,
            memory=MemoryCreate(
                kind=MemoryKind.FACT,
                effect=MemoryEffect.CONTEXT,
                subject="Status report audience",
                content="The weekly status is intended for a public project page.",
                source="demo-user",
            ),
        )
        return DemoSession(
            tenant_id=tenant_id,
            token=token,
            expires_at=expires_at,
            uses_remaining=self._session_use_limit,
            initial_permission_id=permission.id,
        )

    def get_state(self, *, tenant_id: str, token: str) -> DemoState:
        remaining = self._authorize(tenant_id=tenant_id, token=token, consume=True)
        return DemoState(
            tenant_id=tenant_id,
            uses_remaining=remaining,
            memories=self._store.list_memories(tenant_id=tenant_id),
            receipts=self._store.list_receipts(tenant_id=tenant_id),
        )

    def add_memory(
        self,
        *,
        tenant_id: str,
        token: str,
        memory: MemoryCreate,
    ) -> MemoryRecord:
        self._authorize(tenant_id=tenant_id, token=token, consume=True)
        return self._record_memory(tenant_id=tenant_id, memory=memory)

    def evaluate_action(
        self,
        *,
        tenant_id: str,
        token: str,
        request: ActionRequest,
    ) -> ActionReceipt:
        self._authorize(tenant_id=tenant_id, token=token, consume=True)
        query = f"{request.action}. {request.intent}"
        embedding = self._embeddings.embed(query)
        for _ in range(3):
            recalled, memory_version, decision_valid_before, evaluated_at = (
                self._store.recall_active(
                    tenant_id=tenant_id,
                    embedding=embedding,
                    limit=8,
                )
            )
            relevant = [
                memory
                for memory in recalled
                if memory.relevance is None or memory.relevance >= self.relevance_floor
            ]
            receipt, canonical = _build_receipt(
                tenant_id=tenant_id,
                request=request,
                relevant=relevant,
                query=query,
                evaluated_at=evaluated_at,
            )
            saved = self._store.save_receipt(
                receipt,
                expected_memory_version=memory_version,
                decision_valid_before=decision_valid_before,
            )
            if not saved:
                continue
            s3_key = self._receipt_sink.put(
                payload={**canonical, "digest_sha256": receipt.digest_sha256},
                digest_sha256=receipt.digest_sha256,
                created_at=receipt.created_at,
            )
            if s3_key is not None:
                self._store.attach_receipt_archive(receipt_id=receipt.id, s3_key=s3_key)
                receipt = receipt.model_copy(update={"s3_key": s3_key})
            return receipt
        raise RuntimeError("Memory changed repeatedly while the decision was being recorded")

    def _record_memory(self, *, tenant_id: str, memory: MemoryCreate) -> MemoryRecord:
        embedding = self._embeddings.embed(f"{memory.subject}. {memory.content}")
        return self._store.add_memory(
            tenant_id=tenant_id,
            memory=memory,
            embedding=embedding,
        )

    def _authorize(self, *, tenant_id: str, token: str, consume: bool) -> int:
        return self._store.authorize_session(
            tenant_id=tenant_id,
            token_sha256=_sha256(token),
            consume=consume,
        )


def _decide(recalled: list[MemoryRecord]) -> tuple[ActionDecision, str]:
    denials = [memory for memory in recalled if memory.effect == MemoryEffect.DENY]
    if denials:
        denial = max(denials, key=lambda memory: memory.created_at)
        return ActionDecision.DENY, f"Blocked by active memory: {denial.subject}."
    return (
        ActionDecision.REVIEW,
        "Free-text memory can supply evidence but cannot grant authority; "
        "human review is required.",
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _memory_set_digest(memories: list[MemoryRecord]) -> str:
    canonical = [
        {
            "id": str(memory.id),
            "content_sha256": memory.content_sha256,
            "effect": memory.effect.value,
            "status": memory.status.value,
        }
        for memory in sorted(memories, key=lambda item: str(item.id))
    ]
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _build_receipt(
    *,
    tenant_id: str,
    request: ActionRequest,
    relevant: list[MemoryRecord],
    query: str,
    evaluated_at: datetime,
) -> tuple[ActionReceipt, dict[str, object]]:
    decision, reason = _decide(relevant)
    receipt_id = uuid4()
    agent_instance_id = f"agent-{uuid4().hex[:12]}"
    retrieval_query_sha256 = _sha256(query)
    memory_set_digest_sha256 = _memory_set_digest(relevant)
    canonical: dict[str, object] = {
        "id": str(receipt_id),
        "tenant_id": tenant_id,
        "action": request.action,
        "intent": request.intent,
        "decision": decision.value,
        "reason": reason,
        "recalled_memory_ids": [str(memory.id) for memory in relevant],
        "agent_instance_id": agent_instance_id,
        "retrieval_query_sha256": retrieval_query_sha256,
        "memory_set_digest_sha256": memory_set_digest_sha256,
        "created_at": evaluated_at.isoformat(),
    }
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return (
        ActionReceipt(
            id=receipt_id,
            tenant_id=tenant_id,
            action=request.action,
            intent=request.intent,
            decision=decision,
            reason=reason,
            recalled_memory_ids=[memory.id for memory in relevant],
            agent_instance_id=agent_instance_id,
            retrieval_query_sha256=retrieval_query_sha256,
            memory_set_digest_sha256=memory_set_digest_sha256,
            created_at=evaluated_at,
            digest_sha256=digest,
        ),
        canonical,
    )
