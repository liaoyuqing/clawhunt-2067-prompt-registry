"""Evaluation-mode binding and the executor's writeback."""

from __future__ import annotations

import pytest

from app import config
from app.eval_binding import (
    BindingError,
    CaseOutcome,
    build_eval_context,
    record_eval_run,
)

from .conftest import ORG_A, ORG_B, SITE


def test_build_eval_context_returns_version_and_alias_only(memory_database, seed):
    version = seed.prompt(tool="member_service", prompt_key="faq_answer", version="1.1.0", sha="c" * 64)
    seed.profile(name="default", alias="model.main")

    with memory_database.session() as session:
        binding = build_eval_context(
            session,
            org_id=ORG_A,
            tool="member_service",
            prompt_key="faq_answer",
            version="1.1.0",
            model_profile="default",
        )

    payload = binding.as_payload()
    assert payload["prompt_version"]["version"] == "1.1.0"
    assert payload["prompt_version"]["content_sha256"] == "c" * 64
    assert payload["prompt_version"]["tool"] == "member_service"
    assert payload["model_profile"]["name"] == "default"
    assert payload["model_profile"]["endpoint_alias"] == "model.main"
    assert payload["model_profile"]["params"] == {"temperature": 0.2, "max_tokens": 512}
    serialised = str(payload)
    assert "http://" not in serialised and "https://" not in serialised
    assert "endpoint" not in payload["model_profile"] or payload["model_profile"]["endpoint_alias"]


def test_alias_resolves_only_through_the_environment(memory_database, seed, monkeypatch):
    seed.prompt(prompt_key="faq_answer", version="1.0.0")
    seed.profile(name="default", alias="model.main")
    with memory_database.session() as session:
        binding = build_eval_context(
            session,
            org_id=ORG_A,
            tool="member_service",
            prompt_key="faq_answer",
            version="1.0.0",
            model_profile="default",
        )

    monkeypatch.delenv("MODEL_ALIAS_MODEL_MAIN", raising=False)
    with pytest.raises(config.ConfigError) as excinfo:
        binding.resolve_endpoint()
    assert excinfo.value.variable == "MODEL_ALIAS_MODEL_MAIN"

    monkeypatch.setenv("MODEL_ALIAS_MODEL_MAIN", "local-gateway.example.invalid")
    assert binding.resolve_endpoint() == "local-gateway.example.invalid"


def test_unknown_prompt_version_raises(memory_database, seed):
    seed.profile(name="default")
    with memory_database.session() as session:
        with pytest.raises(BindingError) as excinfo:
            build_eval_context(
                session,
                org_id=ORG_A,
                tool="member_service",
                prompt_key="faq_answer",
                version="9.9.9",
                model_profile="default",
            )
    assert excinfo.value.code == "prompt_version_not_found"


def test_other_org_cannot_read_the_binding(memory_database, seed):
    seed.prompt(prompt_key="faq_answer", version="1.0.0", org_id=ORG_B)
    seed.profile(name="default", org_id=ORG_B)
    with memory_database.session() as session:
        with pytest.raises(BindingError):
            build_eval_context(
                session,
                org_id=ORG_A,
                tool="member_service",
                prompt_key="faq_answer",
                version="1.0.0",
                model_profile="default",
            )


def test_record_eval_run_writes_run_and_results(memory_database, seed):
    version = seed.prompt(prompt_key="faq_answer", version="1.0.0")
    with memory_database.session() as session:
        run = record_eval_run(
            session,
            org_id=ORG_A,
            site_id=SITE,
            created_by="executor@example.invalid",
            tool="member_service",
            status="finished",
            started_at=None,
            finished_at=None,
            outcomes=[
                CaseOutcome(case_id="c1", prompt_version_id=version.id, passed=True),
                CaseOutcome(case_id="c2", prompt_version_id=version.id, passed=False),
            ],
            model_profile_name="default",
        )
        session.commit()
        run_id = run.id
        assert run.total_cases == 2 and run.passed_cases == 1

    with memory_database.session() as session:
        from app.models import EvalResult

        rows = session.query(EvalResult).filter(EvalResult.run_id == run_id).all()
    assert len(rows) == 2
    assert {row.case_id for row in rows} == {"c1", "c2"}
    assert sum(1 for row in rows if row.passed) == 1
