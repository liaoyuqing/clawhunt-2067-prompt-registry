"""Shared fixtures: a real SQLite database, a real FastAPI app.

No PostgreSQL driver is required: the models use portable SQLAlchemy types, so
the whole suite runs against SQLite. Production uses PostgreSQL 16 through
Alembic; nothing in these fixtures hides a PostgreSQL-only dependency.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import config
from app.api_eval import get_db_session
from app.main import create_app
from app.models import AiModelProfile, AiPromptVersion, Base, EvalResult, EvalRun

ORG_A = UUID("11111111-1111-1111-1111-111111111111")
ORG_B = UUID("22222222-2222-2222-2222-222222222222")
SITE = UUID("33333333-3333-3333-3333-333333333333")

PROMPT_BODY_V1 = """你是会员服务的答疑助手。

## 约束
- 只使用知识库片段作答。
- 无法回答时转人工。

## 问题
{{question}}
"""

PROMPT_BODY_V2 = """你是会员服务的答疑助手。

## 约束
- 只使用知识库片段作答，并给出来源编号。
- 无法回答时转人工，并说明原因。

## 问题
{{question}}
"""


@dataclass
class Database:
    """A live SQLite database plus the helpers tests need."""

    url: str
    engine: object
    session_factory: sessionmaker

    def session(self) -> Session:
        return self.session_factory()

    def count_prompt_versions(self, org_id: UUID | None = None) -> int:
        with self.session() as session:
            statement = select(AiPromptVersion)
            if org_id is not None:
                statement = statement.where(AiPromptVersion.org_id == org_id)
            return len(session.execute(statement).scalars().all())

    def prompt_version(self, *, tool: str, prompt_key: str, version: str, org_id: UUID = ORG_A):
        with self.session() as session:
            return session.execute(
                select(AiPromptVersion).where(
                    AiPromptVersion.org_id == org_id,
                    AiPromptVersion.tool == tool,
                    AiPromptVersion.prompt_key == prompt_key,
                    AiPromptVersion.version == version,
                )
            ).scalar_one()


@pytest.fixture()
def database(tmp_path: Path) -> Database:
    """A file-backed SQLite database with the full schema."""
    url = f"sqlite:///{(tmp_path / 'registry.db').as_posix()}"
    engine = create_engine(
        url, future=True, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    yield Database(url=url, engine=engine, session_factory=factory)
    engine.dispose()


@pytest.fixture()
def memory_database() -> Database:
    """In-memory SQLite with a StaticPool, so every session sees the schema."""
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    yield Database(url="sqlite://", engine=engine, session_factory=factory)
    engine.dispose()


@pytest.fixture()
def write_prompt() -> Callable[..., Path]:
    """Write one prompt file with a valid front matter and return its path."""

    def _write(
        directory: Path,
        *,
        name: str = "faq_answer.md",
        tool: str = "member_service",
        prompt_key: str = "faq_answer",
        version: str = "1.0.0",
        body: str = PROMPT_BODY_V1,
        changelog: str | None = "initial version",
        extra_front: dict[str, object] | None = None,
        encoding: str = "utf-8",
    ) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        lines = ["---", f"tool: {tool}", f"prompt_key: {prompt_key}", f"version: {version}"]
        if changelog is not None:
            lines.append(f"changelog: {changelog}")
        for key, value in (extra_front or {}).items():
            lines.append(f"{key}: {value}")
        lines.append("---")
        text_block = "\n".join(lines) + "\n\n" + body
        target = directory / name
        target.write_text(text_block, encoding=encoding, newline="")
        return target

    return _write


@pytest.fixture()
def run_cli(monkeypatch: pytest.MonkeyPatch, database: Database):
    """Run the prompt-register command in-process and capture its output."""

    def _run(prompt_dir: Path, *extra: str, database_url: str | None = None):
        from app.cli_prompt_register import main

        monkeypatch.setenv("DATABASE_URL", database_url or database.url)
        monkeypatch.setenv("PROMPT_CREATED_BY", "engineer@example.invalid")
        monkeypatch.setenv("EVAL_ORG_ID", str(ORG_A))
        monkeypatch.setenv("EVAL_SITE_ID", str(SITE))
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = ["--prompt-dir", str(prompt_dir), *extra]
        code = 0
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                code = main(argv)
            except SystemExit as exc:  # pragma: no cover - defensive
                code = int(exc.code or 0)
        return code, stdout.getvalue(), stderr.getvalue()

    return _run


@pytest.fixture()
def api_client(memory_database: Database, monkeypatch: pytest.MonkeyPatch):
    """TestClient wired to the in-memory database, with org isolation intact.

    EVAL_ORG_ID reproduces a real single-tenant deployment: the header is
    optional and this environment variable decides the default organisation.
    """
    monkeypatch.setenv("EVAL_ORG_ID", str(ORG_A))
    monkeypatch.setenv("EVAL_SITE_ID", str(SITE))
    application = create_app()

    def override_session():
        session = memory_database.session_factory()
        try:
            yield session
        finally:
            session.close()

    application.dependency_overrides[get_db_session] = override_session
    with TestClient(application) as client:
        yield client


@pytest.fixture()
def seed(memory_database: Database):
    """Insert prompt versions, a model profile, runs and results."""

    def _seed_prompt(
        *,
        tool: str = "member_service",
        prompt_key: str = "faq_answer",
        version: str = "1.0.0",
        sha: str = "a" * 64,
        org_id: UUID = ORG_A,
        file_path: str | None = None,
    ) -> AiPromptVersion:
        major, minor, patch = (int(part) for part in version.split("."))
        with memory_database.session() as session:
            row = AiPromptVersion(
                org_id=org_id,
                site_id=SITE,
                created_by="seed@example.invalid",
                tool=tool,
                prompt_key=prompt_key,
                version=version,
                version_major=major,
                version_minor=minor,
                version_patch=patch,
                content_sha256=sha,
                file_path=file_path or f"{prompt_key}.md",
            )
            session.add(row)
            session.commit()
            return row

    def _seed_profile(
        *, name: str = "default", alias: str = "model.main", org_id: UUID = ORG_A
    ) -> AiModelProfile:
        with memory_database.session() as session:
            row = AiModelProfile(
                org_id=org_id,
                site_id=SITE,
                created_by="seed@example.invalid",
                name=name,
                endpoint_alias=alias,
                params={"temperature": 0.2, "max_tokens": 512},
            )
            session.add(row)
            session.commit()
            return row

    def _seed_run(
        *,
        tool: str = "member_service",
        org_id: UUID = ORG_A,
        status: str = "finished",
        results: list[tuple[UUID, bool]] | None = None,
        finished_at=None,
        created_at=None,
    ) -> EvalRun:
        with memory_database.session() as session:
            run = EvalRun(
                org_id=org_id,
                site_id=SITE,
                created_by="executor@example.invalid",
                tool=tool,
                status=status,
                started_at=finished_at,
                finished_at=finished_at,
                total_cases=len(results or []),
                passed_cases=sum(1 for _, passed in (results or []) if passed),
            )
            if created_at is not None:
                run.created_at = created_at
            session.add(run)
            session.flush()
            for index, (prompt_version_id, passed) in enumerate(results or []):
                session.add(
                    EvalResult(
                        org_id=org_id,
                        site_id=SITE,
                        created_by="executor@example.invalid",
                        run_id=run.id,
                        prompt_version_id=prompt_version_id,
                        model_profile_name="default",
                        case_id=f"case-{index:03d}",
                        passed=passed,
                    )
                )
            session.commit()
            session.refresh(run)
            return run

    class Seeder:
        prompt = staticmethod(_seed_prompt)
        profile = staticmethod(_seed_profile)
        run = staticmethod(_seed_run)

        @staticmethod
        def db() -> Database:
            return memory_database

    return Seeder
