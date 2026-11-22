"""ORM models. Business tables share id / org_id / site_id / audit columns."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    JSON,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


JSONType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class OrgScoped:
    """Columns every business table in this platform carries."""

    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    site_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AiPromptVersion(Base, OrgScoped):
    """One registered prompt revision. Immutable: a change is a new row."""

    __tablename__ = "ai_prompt_version"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tool: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt_key: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    version_major: Mapped[int] = mapped_column(Integer, nullable=False)
    version_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    version_patch: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    changelog: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "org_id", "tool", "prompt_key", "version", name="uq_ai_prompt_version_scope"
        ),
        Index("ix_ai_prompt_version_lookup", "org_id", "tool", "prompt_key"),
    )


class AiModelProfile(Base, OrgScoped):
    """A named sampling profile. Only an alias is stored, never an address."""

    __tablename__ = "ai_model_profile"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    endpoint_alias: Mapped[str] = mapped_column(String(64), nullable=False)
    params: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_ai_model_profile_name"),
    )


class EvalRun(Base, OrgScoped):
    """One evaluation execution over a case set."""

    __tablename__ = "eval_run"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tool: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="finished")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Denormalised counters kept in step with the eval_result rows by the executor;
    # the API always recomputes from eval_result and never reads these columns.
    total_cases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    passed_cases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class EvalResult(Base, OrgScoped):
    """One case result, pinned to the prompt version that produced it."""

    __tablename__ = "eval_result"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("eval_run.id", ondelete="CASCADE"), nullable=False, index=True
    )
    prompt_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("ai_prompt_version.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    model_profile_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    case_id: Mapped[str] = mapped_column(String(64), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("ix_eval_result_run_version", "run_id", "prompt_version_id"),
    )
