"""Registration core: validate a prompt directory, decide what to write, write it.

The command line is a thin shell around this module. The rule set is:

* a prompt is identified by (org_id, tool, prompt_key, version);
* the content hash covers the body only, so editing a changelog keeps the hash;
* re-registering an identical version is a no-op (idempotent);
* re-registering an existing version with different content is a content error;
* a version lower than the highest registered version is a rollback error;
* two files claiming the same version is a duplicate error;
* nothing is written unless every file passes (one transaction for the batch).

Errors carry a code so the CLI can print a machine-readable line while keeping
exit code 1 for content problems and exit code 2 for operational problems.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.frontmatter import (
    FrontmatterError,
    PromptFile,
    Semver,
    discover_prompt_files,
    load_prompt_file,
)
from app.models import AiPromptVersion


class RegistryError(Exception):
    """A content problem that must stop registration with exit code 1."""

    def __init__(self, code: str, message: str, *, path: str = "", version: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.path = path
        self.version = version

    def render(self) -> str:
        location = f" file={self.path}" if self.path else ""
        version = f" version={self.version}" if self.version else ""
        return f"code={self.code}{location}{version} {self.message}"


@dataclass(frozen=True)
class PlannedAction:
    """One decision for one prompt file."""

    action: str  # insert | skip | error
    prompt: PromptFile | None
    tool: str
    prompt_key: str
    version: str
    content_sha256: str
    file_path: str
    reason: str = ""
    existing_path: str = ""

    @property
    def sort_key(self) -> tuple[str, str, tuple[int, int, int], str]:
        semver = Semver.parse(self.version) if self.version else Semver(0, 0, 0)
        return (self.tool, self.prompt_key, semver.tuple, self.file_path)


@dataclass
class RegistrationPlan:
    """Result of the analysis phase: per-file actions plus blocking errors."""

    prompt_dir: Path
    actions: list[PlannedAction] = field(default_factory=list)
    errors: list[RegistryError] = field(default_factory=list)
    database_checked: bool = False

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def inserts(self) -> list[PlannedAction]:
        return [action for action in self.actions if action.action == "insert"]

    @property
    def skips(self) -> list[PlannedAction]:
        return [action for action in self.actions if action.action == "skip"]

    @property
    def inserted_count(self) -> int:
        return len(self.inserts)

    @property
    def skipped_count(self) -> int:
        return len(self.skips)


def load_prompt_files(prompt_dir: Path) -> tuple[list[PromptFile], list[RegistryError]]:
    """Parse every prompt file; malformed files become blocking errors."""
    prompts: list[PromptFile] = []
    errors: list[RegistryError] = []
    for path in discover_prompt_files(prompt_dir):
        try:
            prompts.append(load_prompt_file(path, root=prompt_dir))
        except FrontmatterError as exc:
            errors.append(RegistryError(exc.code, exc.message, path=exc.path))
    return prompts, errors


def duplicate_errors(prompts: Sequence[PromptFile]) -> list[RegistryError]:
    """Two files may not claim the same (tool, prompt_key, version)."""
    seen: dict[tuple[str, str, str], PromptFile] = {}
    duplicates: dict[tuple[str, str, str], list[PromptFile]] = {}
    for prompt in prompts:
        key = (prompt.tool, prompt.prompt_key, prompt.version)
        if key in seen:
            duplicates.setdefault(key, [seen[key]]).append(prompt)
        else:
            seen[key] = prompt
    errors: list[RegistryError] = []
    for key, files in sorted(duplicates.items()):
        paths = ", ".join(item.rel_path for item in files)
        errors.append(
            RegistryError(
                "duplicate_version",
                (
                    f"version {key[2]} of {key[0]}/{key[1]} is claimed by "
                    f"{len(files)} files ({paths}); each version must appear once"
                ),
                path=files[-1].rel_path,
                version=key[2],
            )
        )
    return errors


def load_registered(
    session: Session, *, org_id: UUID
) -> dict[tuple[str, str, str], AiPromptVersion]:
    """All live rows for this org, keyed by (tool, prompt_key, version)."""
    rows = (
        session.execute(
            select(AiPromptVersion).where(
                AiPromptVersion.org_id == org_id,
                AiPromptVersion.deleted_at.is_(None),
            )
        )
        .scalars()
        .all()
    )
    return {(row.tool, row.prompt_key, row.version): row for row in rows}


def highest_versions(registered: Iterable[AiPromptVersion]) -> dict[tuple[str, str], Semver]:
    """Highest registered version per (tool, prompt_key), compared numerically."""
    highest: dict[tuple[str, str], Semver] = {}
    for row in registered:
        try:
            semver = Semver.parse(row.version)
        except FrontmatterError:
            continue
        key = (row.tool, row.prompt_key)
        current = highest.get(key)
        if current is None or semver > current:
            highest[key] = semver
    return highest


def plan_actions(
    prompts: Sequence[PromptFile],
    registered: dict[tuple[str, str, str], AiPromptVersion],
) -> tuple[list[PlannedAction], list[RegistryError]]:
    """Decide insert / skip / error for each file against the registered state."""
    actions: list[PlannedAction] = []
    errors: list[RegistryError] = []
    highest = highest_versions(registered.values())
    ordered = sorted(
        prompts, key=lambda item: (item.tool, item.prompt_key, item.semver.tuple, item.rel_path)
    )
    for prompt in ordered:
        key = (prompt.tool, prompt.prompt_key, prompt.version)
        existing = registered.get(key)
        if existing is not None:
            if existing.content_sha256 == prompt.content_sha256:
                actions.append(
                    PlannedAction(
                        action="skip",
                        prompt=prompt,
                        tool=prompt.tool,
                        prompt_key=prompt.prompt_key,
                        version=prompt.version,
                        content_sha256=prompt.content_sha256,
                        file_path=prompt.rel_path,
                        reason="already registered with identical content",
                        existing_path=existing.file_path,
                    )
                )
                continue
            errors.append(
                RegistryError(
                    "version_content_conflict",
                    (
                        f"version {prompt.version} of {prompt.tool}/{prompt.prompt_key} "
                        f"is already registered from {existing.file_path} with a different "
                        f"content hash ({existing.content_sha256[:12]}... vs "
                        f"{prompt.content_sha256[:12]}...); raise the version number "
                        "instead of editing a registered version in place"
                    ),
                    path=prompt.rel_path,
                    version=prompt.version,
                )
            )
            continue
        ceiling = highest.get((prompt.tool, prompt.prompt_key))
        if ceiling is not None and prompt.semver < ceiling:
            errors.append(
                RegistryError(
                    "version_regression",
                    (
                        f"version {prompt.version} of {prompt.tool}/{prompt.prompt_key} "
                        f"is lower than the highest registered version {ceiling}; "
                        "versions must increase monotonically"
                    ),
                    path=prompt.rel_path,
                    version=prompt.version,
                )
            )
            continue
        actions.append(
            PlannedAction(
                action="insert",
                prompt=prompt,
                tool=prompt.tool,
                prompt_key=prompt.prompt_key,
                version=prompt.version,
                content_sha256=prompt.content_sha256,
                file_path=prompt.rel_path,
                reason="new version",
            )
        )
    return actions, errors


def _demote_inserts(actions: Sequence[PlannedAction]) -> list[PlannedAction]:
    """Nothing is written when the batch has errors: report inserts as errors."""
    demoted: list[PlannedAction] = []
    for action in actions:
        if action.action != "insert":
            demoted.append(action)
            continue
        demoted.append(
            PlannedAction(
                action="error",
                prompt=action.prompt,
                tool=action.tool,
                prompt_key=action.prompt_key,
                version=action.version,
                content_sha256=action.content_sha256,
                file_path=action.file_path,
                reason="not written: the batch has errors",
                existing_path=action.existing_path,
            )
        )
    return demoted


def plan_registration(
    prompt_dir: Path,
    *,
    org_id: UUID,
    session: Session | None = None,
) -> RegistrationPlan:
    """Validate files and, when a session is given, compare against the database."""
    plan = RegistrationPlan(prompt_dir=prompt_dir)
    try:
        prompts, file_errors = load_prompt_files(prompt_dir)
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise RegistryError("prompt_dir_missing", str(exc)) from exc
    plan.errors.extend(file_errors)
    plan.errors.extend(duplicate_errors(prompts))
    if plan.errors:
        actions, _ = plan_actions(prompts, {})
        plan.actions = _demote_inserts(actions)
        return plan
    registered: dict[tuple[str, str, str], AiPromptVersion] = {}
    if session is not None:
        registered = load_registered(session, org_id=org_id)
        plan.database_checked = True
    actions, errors = plan_actions(prompts, registered)
    plan.actions = actions
    plan.errors.extend(errors)
    if plan.errors:
        plan.actions = _demote_inserts(actions)
    return plan


def apply_registration(
    session: Session,
    plan: RegistrationPlan,
    *,
    org_id: UUID,
    site_id: UUID,
    created_by: str,
) -> int:
    """Insert every planned row inside the caller transaction.

    The caller owns the transaction: either all rows are committed together or
    none of them are. Returns the number of inserted rows.
    """
    if not plan.ok:
        raise RegistryError(
            "batch_invalid",
            "refusing to write: the registration plan has errors",
        )
    inserted = 0
    for action in plan.inserts:
        prompt = action.prompt
        assert prompt is not None
        session.add(
            AiPromptVersion(
                org_id=org_id,
                site_id=site_id,
                created_by=created_by,
                tool=prompt.tool,
                prompt_key=prompt.prompt_key,
                version=prompt.version,
                version_major=prompt.semver.major,
                version_minor=prompt.semver.minor,
                version_patch=prompt.semver.patch,
                content_sha256=prompt.content_sha256,
                file_path=prompt.rel_path,
                changelog=(prompt.changelog or None),
            )
        )
        inserted += 1
    if inserted:
        session.flush()
    return inserted


def format_plan(plan: RegistrationPlan, *, prompt_dir: Path) -> str:
    """Human readable, greppable plan used by CLI output and CI logs."""
    lines = [
        f"prompt-register: prompt directory {prompt_dir} ({len(plan.actions)} file(s))"
    ]
    for action in sorted(plan.actions, key=lambda item: item.sort_key):
        label = f"[{action.action}]".ljust(9)
        origin = f" path={action.file_path}"
        if action.action == "skip" and action.existing_path:
            origin += f" registered_path={action.existing_path}"
        line = (
            f"{label} tool={action.tool} prompt_key={action.prompt_key} "
            f"version={action.version} sha256={action.content_sha256[:12]}{origin}"
        )
        if action.reason:
            line += f" ({action.reason})"
        lines.append(line)
    for error in plan.errors:
        lines.append(f"[error]   {error.render()}")
    return "\n".join(lines)
