"""Evaluation query API.

Two read-only endpoints answer the two questions the acceptance criteria ask:

* which versions of one prompt exist, and how each of them last performed;
* which prompt versions a specific run actually used, for regression triage.

Both are org scoped. A caller never sees another organisation row: an object
that belongs to another org is reported as 404, exactly like an object that does
not exist, so the API never leaks the existence of foreign data.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Iterator
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.db import get_session_factory
from app.models import AiPromptVersion, EvalResult, EvalRun
from app.schemas import (
    PromptVersionHistory,
    PromptVersionHistoryItem,
    RunVersionItem,
    RunVersions,
)

router = APIRouter(prefix="/api/eval", tags=["eval"])

TOOL_PATTERN = r"^[A-Za-z0-9_.\-]{1,64}$"
PROMPT_KEY_PATTERN = r"^[A-Za-z0-9_.\-]{1,128}$"


def get_db_session() -> Iterator[Session]:
    """Session per request; a missing database is a 503, not a crash."""
    try:
        factory = get_session_factory()
    except config.ConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=exc.message,
        ) from exc
    session = factory()
    try:
        yield session
    finally:
        session.close()


def caller_org_id(
    x_org_id: Annotated[
        str | None,
        Header(
            alias="X-Org-Id",
            description=(
                "Organisation the caller acts for. Defaults to EVAL_ORG_ID, then "
                "to the nil UUID for single-tenant deployments."
            ),
        ),
    ] = None,
) -> UUID:
    """Resolve the caller organisation; the only source of tenancy here."""
    if x_org_id is None:
        try:
            return config.org_id()
        except config.ConfigError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=exc.message
            ) from exc
    try:
        return config.parse_uuid(x_org_id, variable="X-Org-Id")
    except config.ConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=exc.message
        ) from exc


SessionDep = Annotated[Session, Depends(get_db_session)]
OrgDep = Annotated[UUID, Depends(caller_org_id)]


def _latest_evaluation_per_version(
    session: Session, *, org_id: UUID, version_ids: list[UUID]
) -> dict[UUID, tuple[UUID, datetime | None, int, int]]:
    """For each version: (run_id, run_finished_at, passed, total) of its latest run.

    "Latest" is decided by the run timeline (finished_at, then started_at, then
    created_at, then the run id as a deterministic tie breaker), never by
    insertion order, so re-running an evaluation of an old version does not make
    that version look newer than it is.
    """
    if not version_ids:
        return {}
    rows = session.execute(
        select(
            EvalResult.prompt_version_id,
            EvalRun.id,
            EvalRun.finished_at,
            EvalRun.started_at,
            EvalRun.created_at,
            EvalResult.passed,
        )
        .join(EvalRun, EvalRun.id == EvalResult.run_id)
        .where(
            EvalResult.org_id == org_id,
            EvalResult.deleted_at.is_(None),
            EvalResult.prompt_version_id.in_(version_ids),
            EvalRun.org_id == org_id,
            EvalRun.deleted_at.is_(None),
        )
    ).all()

    per_version: dict[UUID, dict[UUID, dict[str, object]]] = {}
    for version_id, run_id, finished_at, started_at, created_at, passed in rows:
        bucket = per_version.setdefault(version_id, {})
        stats = bucket.setdefault(
            run_id,
            {
                "timeline": (finished_at or started_at or created_at, str(run_id)),
                "finished_at": finished_at,
                "passed": 0,
                "total": 0,
            },
        )
        stats["total"] = int(stats["total"]) + 1
        if passed:
            stats["passed"] = int(stats["passed"]) + 1

    latest: dict[UUID, tuple[UUID, datetime | None, int, int]] = {}
    for version_id, runs in per_version.items():
        best_run_id = None
        best: dict[str, object] | None = None
        for run_id, stats in runs.items():
            if best is None or stats["timeline"] > best["timeline"]:  # type: ignore[operator]
                best = stats
                best_run_id = run_id
        assert best is not None and best_run_id is not None
        latest[version_id] = (
            best_run_id,
            best["finished_at"],  # type: ignore[arg-type]
            int(best["passed"]),
            int(best["total"]),
        )
    return latest


@router.get(
    "/prompt-versions/{tool}/{prompt_key}/history",
    response_model=PromptVersionHistory,
    summary="Registered versions of one prompt with their latest evaluation outcome",
    responses={
        200: {"description": "One row per registered version, newest version last."},
        404: {"description": "No such prompt in this organisation (also used cross-org)."},
    },
)
def prompt_version_history(
    session: SessionDep,
    org_id: OrgDep,
    tool: Annotated[str, Path(pattern=TOOL_PATTERN)],
    prompt_key: Annotated[str, Path(pattern=PROMPT_KEY_PATTERN)],
    include_deleted: Annotated[
        bool, Query(description="Include soft-deleted versions (audit view).")
    ] = False,
) -> PromptVersionHistory:
    """Every version of a prompt, with the pass rate of its most recent run.

    `latest_pass_rate` is null, never 0.0, when a version has never been
    evaluated or when its latest run recorded no case at all: 0.0 would read as
    "evaluated and every case failed", which is a different fact.
    """
    statement = select(AiPromptVersion).where(
        AiPromptVersion.org_id == org_id,
        AiPromptVersion.tool == tool,
        AiPromptVersion.prompt_key == prompt_key,
    )
    if not include_deleted:
        statement = statement.where(AiPromptVersion.deleted_at.is_(None))
    versions = session.execute(statement).scalars().all()
    if not versions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"no registered prompt version for tool={tool!r} "
                f"prompt_key={prompt_key!r} in this organisation"
            ),
        )
    latest = _latest_evaluation_per_version(
        session, org_id=org_id, version_ids=[row.id for row in versions]
    )
    items: list[PromptVersionHistoryItem] = []
    for row in versions:
        stats = latest.get(row.id)
        if stats is None:
            items.append(
                PromptVersionHistoryItem(
                    version=row.version,
                    created_at=row.created_at,
                    latest_run_id=None,
                    latest_pass_rate=None,
                    latest_run_finished_at=None,
                )
            )
            continue
        run_id, finished_at, passed, total = stats
        rate = (passed / total) if total > 0 else None
        items.append(
            PromptVersionHistoryItem(
                version=row.version,
                created_at=row.created_at,
                latest_run_id=run_id,
                latest_pass_rate=rate,
                latest_run_finished_at=finished_at,
            )
        )
    items.sort(key=lambda item: _version_tuple(item.version))
    return PromptVersionHistory(
        tool=tool, prompt_key=prompt_key, org_id=org_id, versions=items
    )


def _version_tuple(raw: str) -> tuple[int, int, int]:
    from app.frontmatter import FrontmatterError, Semver

    try:
        return Semver.parse(raw).tuple
    except FrontmatterError:
        return (0, 0, 0)


@router.get(
    "/runs/{run_id}/versions",
    response_model=RunVersions,
    summary="Prompt versions a run actually exercised",
    responses={
        200: {"description": "Distinct versions referenced by the run's results."},
        404: {"description": "No such run in this organisation (also used cross-org)."},
    },
)
def run_versions(
    session: SessionDep,
    org_id: OrgDep,
    run_id: UUID,
) -> RunVersions:
    """The prompt versions a given run used, sorted by tool then prompt_key.

    These are the versions referenced by the run's eval_result rows, not every
    version in the database: that is what makes the endpoint useful for finding
    which version introduced a regression.
    """
    run = session.execute(
        select(EvalRun).where(
            EvalRun.id == run_id,
            EvalRun.org_id == org_id,
            EvalRun.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no evaluation run {run_id} in this organisation",
        )
    rows = session.execute(
        select(
            AiPromptVersion.tool,
            AiPromptVersion.prompt_key,
            AiPromptVersion.version,
            AiPromptVersion.content_sha256,
        )
        .join(EvalResult, EvalResult.prompt_version_id == AiPromptVersion.id)
        .where(
            EvalResult.run_id == run_id,
            EvalResult.org_id == org_id,
            EvalResult.deleted_at.is_(None),
            AiPromptVersion.org_id == org_id,
        )
        .distinct()
    ).all()
    items = [
        RunVersionItem(
            tool=tool,
            prompt_key=prompt_key,
            version=version,
            content_sha256=content_sha256,
        )
        for tool, prompt_key, version, content_sha256 in rows
    ]
    items.sort(key=lambda item: (item.tool, item.prompt_key, _version_tuple(item.version)))
    return RunVersions(
        run_id=run.id, tool=run.tool, status=run.status, versions=items
    )
