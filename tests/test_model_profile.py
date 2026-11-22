"""model-profile: only an alias is stored, never an address."""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout

import pytest
from sqlalchemy import select

from app.cli_model_profile import main as profile_main
from app.models import AiModelProfile

from .conftest import ORG_A, SITE


@pytest.fixture()
def profile_env(monkeypatch: pytest.MonkeyPatch, database):
    monkeypatch.setenv("DATABASE_URL", database.url)
    monkeypatch.setenv("PROMPT_CREATED_BY", "engineer@example.invalid")
    monkeypatch.setenv("EVAL_ORG_ID", str(ORG_A))
    monkeypatch.setenv("EVAL_SITE_ID", str(SITE))
    return database


def run_profile(*args: str) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = profile_main(list(args))
    return code, stdout.getvalue(), stderr.getvalue()


def test_model_profile_registers_alias_and_params(profile_env):
    code, out, err = run_profile(
        "--name", "default", "--endpoint-alias", "model.main",
        "--temperature", "0.2", "--max-tokens", "512",
    )

    assert code == 0, err
    assert "insert name=default alias=model.main" in out
    assert "address_from_env=MODEL_ALIAS_MODEL_MAIN" in out
    with profile_env.session() as session:
        row = session.execute(
            select(AiModelProfile).where(AiModelProfile.org_id == ORG_A)
        ).scalar_one()
    assert row.endpoint_alias == "model.main"
    assert row.params == {"temperature": 0.2, "max_tokens": 512}
    assert "http" not in row.endpoint_alias and "." not in row.endpoint_alias.replace("model.main", "")


def test_model_profile_second_run_updates_the_same_row(profile_env):
    run_profile("--name", "default", "--endpoint-alias", "model.main", "--temperature", "0.2")
    code, out, err = run_profile(
        "--name", "default", "--endpoint-alias", "model.main", "--temperature", "0.7",
        "--max-tokens", "1024",
    )

    assert code == 0, err
    assert "update name=default" in out
    with profile_env.session() as session:
        rows = session.execute(select(AiModelProfile)).scalars().all()
    assert len(rows) == 1
    assert rows[0].params["temperature"] == 0.7
    assert rows[0].params["max_tokens"] == 1024


def test_model_profile_rejects_an_address_as_alias(profile_env):
    code, out, err = run_profile(
        "--name", "default", "--endpoint-alias", "https://gateway.example.invalid/v1"
    )
    assert code == 1
    assert "plain alias" in err
    with profile_env.session() as session:
        assert session.execute(select(AiModelProfile)).scalars().all() == []


def test_model_profile_rejects_out_of_range_temperature(profile_env):
    code, out, err = run_profile(
        "--name", "default", "--endpoint-alias", "model.main", "--temperature", "9.5"
    )
    assert code == 1
    assert "temperature" in err


def test_model_profile_dry_run_writes_nothing(profile_env):
    code, out, err = run_profile(
        "--name", "default", "--endpoint-alias", "model.main", "--dry-run"
    )
    assert code == 0, err
    assert "dry run" in out
    with profile_env.session() as session:
        assert session.execute(select(AiModelProfile)).scalars().all() == []
