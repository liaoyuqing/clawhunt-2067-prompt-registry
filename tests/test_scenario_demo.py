"""End-to-end scenario: one prompt registered at v1.0.0, then raised to v1.1.0.

This is the shape the acceptance criteria describe, exercised through the real
command and the real API in one flow, with the API reading the same rows.
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from app.eval_binding import CaseOutcome, record_eval_run
from app.models import AiPromptVersion

from .conftest import ORG_A, PROMPT_BODY_V1, PROMPT_BODY_V2, SITE


def test_register_v1_then_promote_to_v1_1_and_query_history(
    write_prompt, database, monkeypatch, tmp_path
):
    prompts = tmp_path / "prompts"
    monkeypatch.setenv("DATABASE_URL", database.url)
    monkeypatch.setenv("PROMPT_CREATED_BY", "engineer@example.invalid")
    monkeypatch.setenv("EVAL_ORG_ID", str(ORG_A))
    monkeypatch.setenv("EVAL_SITE_ID", str(SITE))

    from app.cli_prompt_register import main

    def register() -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--prompt-dir", str(prompts)])
        return code, stdout.getvalue(), stderr.getvalue()

    # 1. v1.0.0 registered.
    write_prompt(prompts, name="faq.md", prompt_key="faq_answer", version="1.0.0", body=PROMPT_BODY_V1)
    code, out, err = register()
    assert code == 0, err
    assert database.count_prompt_versions() == 1
    v1 = database.prompt_version(tool="member_service", prompt_key="faq_answer", version="1.0.0")

    # 2. Editing the body in place is refused: this is the rule that protects history.
    write_prompt(prompts, name="faq.md", prompt_key="faq_answer", version="1.0.0", body=PROMPT_BODY_V2)
    code, out, err = register()
    assert code == 1
    assert "code=version_content_conflict" in err
    assert database.count_prompt_versions() == 1

    # 3. Raising the version registers a second immutable row.
    write_prompt(prompts, name="faq.md", prompt_key="faq_answer", version="1.1.0", body=PROMPT_BODY_V2)
    code, out, err = register()
    assert code == 0, err
    assert database.count_prompt_versions() == 2
    v2 = database.prompt_version(tool="member_service", prompt_key="faq_answer", version="1.1.0")
    assert v1.content_sha256 != v2.content_sha256

    # 4. An evaluation of each version is recorded by the executor.
    with database.session() as session:
        run_v1 = record_eval_run(
            session, org_id=ORG_A, site_id=SITE, created_by="executor@example.invalid",
            tool="member_service", status="finished", started_at=None, finished_at=None,
            outcomes=[CaseOutcome("c1", v1.id, True), CaseOutcome("c2", v1.id, False)],
            model_profile_name="default",
        )
        run_v2 = record_eval_run(
            session, org_id=ORG_A, site_id=SITE, created_by="executor@example.invalid",
            tool="member_service", status="finished", started_at=None, finished_at=None,
            outcomes=[CaseOutcome("c1", v2.id, True), CaseOutcome("c2", v2.id, True)],
            model_profile_name="default",
        )
        session.commit()
        run_v1_id, run_v2_id = run_v1.id, run_v2.id

    # 5. The API reports both versions with their own pass rate.
    from fastapi.testclient import TestClient
    from app.api_eval import get_db_session
    from app.main import create_app

    application = create_app()

    def override():
        session = database.session_factory()
        try:
            yield session
        finally:
            session.close()

    application.dependency_overrides[get_db_session] = override
    with TestClient(application) as client:
        history = client.get("/api/eval/prompt-versions/member_service/faq_answer/history")
        assert history.status_code == 200, history.text
        payload = history.json()
        rates = {item["version"]: item["latest_pass_rate"] for item in payload["versions"]}
        assert rates == {"1.0.0": 0.5, "1.1.0": 1.0}

        versions_of_run = client.get(f"/api/eval/runs/{run_v2_id}/versions")
        assert versions_of_run.status_code == 200
        assert [item["version"] for item in versions_of_run.json()["versions"]] == ["1.1.0"]
