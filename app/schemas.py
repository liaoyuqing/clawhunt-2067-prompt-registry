"""Pydantic v2 response models for the evaluation query API."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PromptVersionHistoryItem(BaseModel):
    """One registered version plus the outcome of its most recent evaluation."""

    model_config = ConfigDict(extra="forbid")

    version: str = Field(description="Semantic version with three numeric segments.")
    created_at: datetime
    latest_run_id: UUID | None = Field(
        default=None,
        description="Most recent evaluation run that exercised this version.",
    )
    latest_pass_rate: float | None = Field(
        default=None,
        description=(
            "passed / total for this version within latest_run_id. "
            "null when the version was never evaluated or the denominator is zero; "
            "never 0.0 as a stand-in for 'not evaluated'."
        ),
    )
    latest_run_finished_at: datetime | None = None


class PromptVersionHistory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    prompt_key: str
    org_id: UUID
    versions: list[PromptVersionHistoryItem]


class RunVersionItem(BaseModel):
    """A prompt version that the given run actually exercised."""

    model_config = ConfigDict(extra="forbid")

    tool: str
    prompt_key: str
    version: str
    content_sha256: str


class RunVersions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    tool: str
    status: str
    versions: list[RunVersionItem]


class ModelProfileSummary(BaseModel):
    """Model profile as seen by a caller: alias and parameters, never an address."""

    model_config = ConfigDict(extra="forbid")

    name: str
    endpoint_alias: str
    params: dict[str, object]


class PromptVersionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    tool: str
    prompt_key: str
    version: str
    content_sha256: str
    file_path: str


class EvalRequestContext(BaseModel):
    """Payload an evaluated service returns and an executor persists."""

    model_config = ConfigDict(extra="forbid")

    prompt_version: PromptVersionSummary
    model_profile: ModelProfileSummary
