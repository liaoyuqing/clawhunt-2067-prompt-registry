"""Evaluation-mode binding: what an evaluated service returns, and how the
executor records it.

The contract has two halves.

1. The service under test, when it runs in evaluation mode, echoes the exact
   prompt version and model profile it used. `build_eval_context` is the only
   place that decides what those two payloads look like: the prompt version is
   pinned by (tool, prompt_key, version) and the model profile exposes only an
   alias and sampling parameters, never an address.

2. The executor writes one eval_run row plus one eval_result row per case, and
   each eval_result row points at the prompt_version_id reported by the service.
   `record_eval_run` does that write inside the caller transaction.

Address resolution is deliberately not part of either payload: an alias becomes
a real address only through `config.resolve_model_endpoint`, at the moment the
client actually calls the model, outside the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.models import AiModelProfile, AiPromptVersion, EvalResult, EvalRun


class BindingError(Exception):
    """The requested prompt version or model profile does not exist."""

    def __init__(self, message: str, *, code: str = "binding_not_found") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass(frozen=True)
class PromptVersionBinding:
    """The immutable identity of one prompt version."""

    id: UUID
    tool: str
    prompt_key: str
    version: str
    content_sha256: str
    file_path: str

    def as_payload(self) -> dict[str, str]:
        return {
            "id": str(self.id),
            "tool": self.tool,
            "prompt_key": self.prompt_key,
            "version": self.version,
            "content_sha256": self.content_sha256,
            "file_path": self.file_path,
        }


@dataclass(frozen=True)
class ModelProfileBinding:
    """Alias plus sampling parameters. No address, by construction."""

    name: str
    endpoint_alias: str
    params: dict

    def as_payload(self) -> dict:
        return {
            "name": self.name,
            "endpoint_alias": self.endpoint_alias,
            "params": dict(self.params),
        }


@dataclass(frozen=True)
class EvalBinding:
    """Everything an evaluation-mode response must echo."""

    prompt_version: PromptVersionBinding
    model_profile: ModelProfileBinding

    def as_payload(self) -> dict:
        return {
            "prompt_version": self.prompt_version.as_payload(),
            "model_profile": self.model_profile.as_payload(),
        }

    def resolve_endpoint(self) -> str:
        """Resolve the alias to a real address via the environment only."""
        return config.resolve_model_endpoint(self.model_profile.endpoint_alias)


def find_prompt_version(
    session: Session, *, org_id: UUID, tool: str, prompt_key: str, version: str
) -> AiPromptVersion:
    """Fetch one live prompt version; raise BindingError when absent."""
    row = session.execute(
        select(AiPromptVersion).where(
            AiPromptVersion.org_id == org_id,
            AiPromptVersion.tool == tool,
            AiPromptVersion.prompt_key == prompt_key,
            AiPromptVersion.version == version,
            AiPromptVersion.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if row is None:
        raise BindingError(
            f"prompt version {tool}/{prompt_key}@{version} is not registered",
            code="prompt_version_not_found",
        )
    return row


def find_model_profile(session: Session, *, org_id: UUID, name: str) -> AiModelProfile:
    """Fetch one live model profile; raise BindingError when absent."""
    row = session.execute(
        select(AiModelProfile).where(
            AiModelProfile.org_id == org_id,
            AiModelProfile.name == name,
            AiModelProfile.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if row is None:
        raise BindingError(
            f"model profile {name!r} is not registered",
            code="model_profile_not_found",
        )
    return row


def build_eval_context(
    session: Session,
    *,
    org_id: UUID,
    tool: str,
    prompt_key: str,
    version: str,
    model_profile: str,
) -> EvalBinding:
    """Build the payload an evaluation-mode response returns."""
    prompt_row = find_prompt_version(
        session, org_id=org_id, tool=tool, prompt_key=prompt_key, version=version
    )
    profile_row = find_model_profile(session, org_id=org_id, name=model_profile)
    binding = EvalBinding(
        prompt_version=PromptVersionBinding(
            id=prompt_row.id,
            tool=prompt_row.tool,
            prompt_key=prompt_row.prompt_key,
            version=prompt_row.version,
            content_sha256=prompt_row.content_sha256,
            file_path=prompt_row.file_path,
        ),
        model_profile=ModelProfileBinding(
            name=profile_row.name,
            endpoint_alias=profile_row.endpoint_alias,
            params=dict(profile_row.params or {}),
        ),
    )
    return binding


@dataclass(frozen=True)
class CaseOutcome:
    """One case the service reported: which version produced it, passed or not."""

    case_id: str
    prompt_version_id: UUID
    passed: bool


def record_eval_run(
    session: Session,
    *,
    org_id: UUID,
    site_id: UUID,
    created_by: str,
    tool: str,
    status: str,
    started_at: datetime | None,
    finished_at: datetime | None,
    outcomes: Sequence[CaseOutcome],
    model_profile_name: str | None = None,
) -> EvalRun:
    """Insert one eval_run and its eval_result rows in the caller transaction.

    Counters are derived from `outcomes` so a run can never claim a total that
    disagrees with its own result rows.
    """
    if status not in {"pending", "running", "finished", "failed"}:
        raise BindingError(
            f"status {status!r} is not one of pending/running/finished/failed",
            code="invalid_status",
        )
    run = EvalRun(
        org_id=org_id,
        site_id=site_id,
        created_by=created_by,
        tool=tool,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        total_cases=len(outcomes),
        passed_cases=sum(1 for outcome in outcomes if outcome.passed),
    )
    session.add(run)
    session.flush()
    for outcome in outcomes:
        session.add(
            EvalResult(
                org_id=org_id,
                site_id=site_id,
                created_by=created_by,
                run_id=run.id,
                prompt_version_id=outcome.prompt_version_id,
                model_profile_name=model_profile_name,
                case_id=outcome.case_id,
                passed=outcome.passed,
            )
        )
    session.flush()
    return run


def model_profile_aliases(session: Session, *, org_id: UUID) -> Mapping[str, str]:
    """Every live alias of this org, for callers that only need the mapping."""
    rows = session.execute(
        select(AiModelProfile).where(
            AiModelProfile.org_id == org_id,
            AiModelProfile.deleted_at.is_(None),
        )
    ).scalars()
    return {row.name: row.endpoint_alias for row in rows}
