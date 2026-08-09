from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class MemoryKind(StrEnum):
    FACT = "fact"
    INSTRUCTION = "instruction"
    PERMISSION = "permission"
    DECISION = "decision"


class MemoryEffect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    CONTEXT = "context"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REVOKED = "revoked"


class ActionDecision(StrEnum):
    DENY = "deny"
    REVIEW = "needs_review"


class MemoryCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    kind: MemoryKind
    effect: MemoryEffect
    subject: str = Field(min_length=3, max_length=120)
    content: str = Field(min_length=3, max_length=2_000)
    source: str = Field(default="user", min_length=2, max_length=200)
    valid_from: datetime = Field(default_factory=utc_now)
    valid_until: datetime | None = None
    supersedes_id: UUID | None = None

    @model_validator(mode="after")
    def validate_time_window(self) -> MemoryCreate:
        if self.valid_from.tzinfo is None:
            raise ValueError("valid_from must include a timezone")
        if self.valid_until is not None:
            if self.valid_until.tzinfo is None:
                raise ValueError("valid_until must include a timezone")
            if self.valid_until <= self.valid_from:
                raise ValueError("valid_until must be later than valid_from")
        return self


class MemoryRecord(MemoryCreate):
    id: UUID
    tenant_id: str
    status: MemoryStatus
    embedding: list[float] = Field(exclude=True)
    relevance: float | None = Field(default=None, exclude=True)
    content_sha256: str
    created_at: datetime


class ActionRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    action: str = Field(min_length=3, max_length=160)
    intent: str = Field(min_length=3, max_length=1_000)


class ActionReceipt(BaseModel):
    id: UUID
    tenant_id: str
    action: str
    intent: str
    decision: ActionDecision
    reason: str
    recalled_memory_ids: list[UUID]
    agent_instance_id: str
    retrieval_query_sha256: str
    memory_set_digest_sha256: str
    created_at: datetime
    digest_sha256: str
    s3_key: str | None = None


class DemoSession(BaseModel):
    tenant_id: str
    token: str
    expires_at: datetime
    uses_remaining: int
    initial_permission_id: UUID


class DemoState(BaseModel):
    tenant_id: str
    uses_remaining: int
    memories: list[MemoryRecord]
    receipts: list[ActionReceipt]
