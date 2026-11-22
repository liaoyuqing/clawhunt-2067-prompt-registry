"""prompt-register: registration, idempotence, and every rejection path.

Each test drives the real command entry point against a real SQLite database and
asserts on both the exit code and the resulting rows, because the acceptance
criteria care about "it failed" and about "it did not write".
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.frontmatter import content_sha256
from app.models import AiPromptVersion

from .conftest import ORG_A, PROMPT_BODY_V1, PROMPT_BODY_V2


def rows(database) -> list[AiPromptVersion]:
    with database.session() as session:
        return session.query(AiPromptVersion).all()


def test_first_registration_inserts_one_row_per_file(write_prompt, run_cli, database, tmp_path):
    prompts = tmp_path / "prompts"
    write_prompt(prompts, name="faq.md", prompt_key="faq_answer", version="1.0.0")
    write_prompt(prompts, name="intent.md", prompt_key="intent_router", version="1.0.0")
    write_prompt(prompts, name="summary.md", prompt_key="thread_summary", version="2.0.0")

    code, out, err = run_cli(prompts)

    assert code == 0, err
    assert "inserted 3, skipped 0" in out
    assert database.count_prompt_versions() == 3
    stored = database.prompt_version(tool="member_service", prompt_key="faq_answer", version="1.0.0")
    assert stored.content_sha256 == content_sha256(PROMPT_BODY_V1)
    assert stored.version_major == 1 and stored.version_minor == 0 and stored.version_patch == 0
    assert stored.created_by == "engineer@example.invalid"
    assert stored.changelog == "initial version"


def test_second_run_is_idempotent_and_reports_skipped(write_prompt, run_cli, database, tmp_path):
    prompts = tmp_path / "prompts"
    write_prompt(prompts, name="faq.md", version="1.0.0")
    write_prompt(prompts, name="intent.md", prompt_key="intent_router", version="1.0.0")

    first_code, first_out, _ = run_cli(prompts)
    count_after_first = database.count_prompt_versions()
    second_code, second_out, second_err = run_cli(prompts)

    assert first_code == 0 and second_code == 0, second_err
    assert "inserted 2, skipped 0" in first_out
    assert "inserted 0, skipped 2" in second_out
    assert "[skip]" in second_out
    assert database.count_prompt_versions() == count_after_first == 2


def test_changing_changelog_only_keeps_the_hash_stable(write_prompt, run_cli, database, tmp_path):
    prompts = tmp_path / "prompts"
    write_prompt(prompts, name="faq.md", version="1.0.0", changelog="first wording")
    run_cli(prompts)
    before = database.prompt_version(tool="member_service", prompt_key="faq_answer", version="1.0.0")

    write_prompt(prompts, name="faq.md", version="1.0.0", changelog="a brand new changelog")
    code, out, err = run_cli(prompts)

    assert code == 0, err
    assert "skipped 1" in out
    after = database.prompt_version(tool="member_service", prompt_key="faq_answer", version="1.0.0")
    assert after.content_sha256 == before.content_sha256
    assert database.count_prompt_versions() == 1


def test_content_change_without_version_bump_exits_1_and_writes_nothing(
    write_prompt, run_cli, database, tmp_path
):
    prompts = tmp_path / "prompts"
    write_prompt(prompts, name="faq.md", version="1.0.0", body=PROMPT_BODY_V1)
    run_cli(prompts)
    count_before = database.count_prompt_versions()

    write_prompt(prompts, name="faq.md", version="1.0.0", body=PROMPT_BODY_V2)
    code, out, err = run_cli(prompts)

    assert code == 1
    assert "code=version_content_conflict" in err
    assert database.count_prompt_versions() == count_before
    stored = database.prompt_version(tool="member_service", prompt_key="faq_answer", version="1.0.0")
    assert stored.content_sha256 == content_sha256(PROMPT_BODY_V1)


def test_version_rollback_exits_1(write_prompt, run_cli, database, tmp_path):
    prompts = tmp_path / "prompts"
    write_prompt(prompts, name="faq.md", version="2.0.0")
    assert run_cli(prompts)[0] == 0

    write_prompt(prompts, name="faq.md", version="1.9.0", body=PROMPT_BODY_V2)
    code, out, err = run_cli(prompts)

    assert code == 1
    assert "code=version_regression" in err
    assert "1.9.0" in err and "2.0.0" in err
    assert database.count_prompt_versions() == 1


def test_same_version_in_two_files_exits_1(write_prompt, run_cli, database, tmp_path):
    prompts = tmp_path / "prompts"
    write_prompt(prompts, name="faq_a.md", prompt_key="faq_answer", version="1.0.0")
    write_prompt(prompts, name="faq_b.md", prompt_key="faq_answer", version="1.0.0")

    code, out, err = run_cli(prompts)

    assert code == 1
    assert "code=duplicate_version" in err
    assert database.count_prompt_versions() == 0


def test_missing_frontmatter_field_exits_1(write_prompt, run_cli, database, tmp_path):
    prompts = tmp_path / "prompts"
    target = write_prompt(prompts, name="faq.md", version="1.0.0")
    target.write_text(
        "---\ntool: member_service\nversion: 1.0.0\n---\n\nbody\n", encoding="utf-8"
    )

    code, out, err = run_cli(prompts)

    assert code == 1
    assert "code=missing_field" in err
    assert "prompt_key" in err
    assert database.count_prompt_versions() == 0


def test_unknown_frontmatter_field_exits_1(write_prompt, run_cli, database, tmp_path):
    prompts = tmp_path / "prompts"
    write_prompt(prompts, name="faq.md", version="1.0.0", extra_front={"versoin": "1.1.0"})

    code, out, err = run_cli(prompts)

    assert code == 1
    assert "versoin" in err
    assert database.count_prompt_versions() == 0


def test_version_with_two_segments_exits_1(write_prompt, run_cli, database, tmp_path):
    prompts = tmp_path / "prompts"
    write_prompt(prompts, name="faq.md", version="1.0")

    code, out, err = run_cli(prompts)

    assert code == 1
    assert "code=invalid_version" in err
    assert database.count_prompt_versions() == 0


def test_crlf_and_lf_versions_of_the_same_body_hash_identically(
    write_prompt, run_cli, database, tmp_path
):
    prompts = tmp_path / "prompts"
    lf_file = write_prompt(prompts, name="faq.md", version="1.0.0", body=PROMPT_BODY_V1)
    lf_sha = content_sha256(lf_file.read_text(encoding="utf-8").split("---", 2)[2])
    run_cli(prompts)

    crlf_dir = tmp_path / "crlf"
    crlf_dir.mkdir()
    crlf_file = crlf_dir / "faq.md"
    text_block = lf_file.read_text(encoding="utf-8").replace("\n", "\r\n")
    crlf_file.write_bytes(text_block.encode("utf-8"))
    assert b"\r\n" in crlf_file.read_bytes()

    code, out, err = run_cli(crlf_dir)

    assert code == 0, err
    assert "skipped 1" in out
    stored = database.prompt_version(tool="member_service", prompt_key="faq_answer", version="1.0.0")
    assert stored.content_sha256 == lf_sha
    assert database.count_prompt_versions() == 1


def test_rollback_of_one_file_prevents_writing_the_whole_batch(
    write_prompt, run_cli, database, tmp_path
):
    prompts = tmp_path / "prompts"
    write_prompt(prompts, name="faq.md", prompt_key="faq_answer", version="2.0.0")
    assert run_cli(prompts)[0] == 0

    write_prompt(prompts, name="faq.md", prompt_key="faq_answer", version="1.0.0", body=PROMPT_BODY_V2)
    write_prompt(prompts, name="intent.md", prompt_key="intent_router", version="1.0.0")
    code, out, err = run_cli(prompts)

    assert code == 1
    assert database.count_prompt_versions() == 1
    with database.session() as session:
        keys = {(row.tool, row.prompt_key) for row in session.query(AiPromptVersion).all()}
    assert ("member_service", "intent_router") not in keys


def test_dry_run_reports_plan_without_writing(write_prompt, run_cli, database, tmp_path):
    prompts = tmp_path / "prompts"
    write_prompt(prompts, name="faq.md", version="1.0.0")

    code, out, err = run_cli(prompts, "--dry-run")

    assert code == 0, err
    assert "dry run, would insert 1" in out
    assert "[insert]" in out
    assert database.count_prompt_versions() == 0


def test_allow_missing_db_validates_files_without_a_database(
    write_prompt, run_cli, monkeypatch, tmp_path
):
    prompts = tmp_path / "prompts"
    write_prompt(prompts, name="faq.md", version="1.0.0", prompt_key="faq_answer")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("PROMPT_CREATED_BY", "engineer@example.invalid")

    from app.cli_prompt_register import main

    import io
    from contextlib import redirect_stdout, redirect_stderr

    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(["--prompt-dir", str(prompts), "--dry-run", "--allow-missing-db"])

    assert code == 0, stderr.getvalue()
    assert "file-level validation only" in stdout.getvalue()
    assert "insert" in stdout.getvalue()


def test_allow_missing_db_still_reports_content_errors(
    write_prompt, monkeypatch, tmp_path
):
    prompts = tmp_path / "prompts"
    write_prompt(prompts, name="faq.md", version="1.00.0")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("PROMPT_CREATED_BY", "engineer@example.invalid")

    from app.cli_prompt_register import main

    import io
    from contextlib import redirect_stdout, redirect_stderr

    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(["--prompt-dir", str(prompts), "--allow-missing-db"])

    assert code == 1
    assert "code=invalid_version" in stderr.getvalue()


def test_missing_database_configuration_exits_2(write_prompt, monkeypatch, tmp_path):
    prompts = tmp_path / "prompts"
    write_prompt(prompts, name="faq.md", version="1.0.0")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("PROMPT_CREATED_BY", "engineer@example.invalid")

    from app.cli_prompt_register import main

    import io
    from contextlib import redirect_stdout, redirect_stderr

    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(["--prompt-dir", str(prompts)])

    assert code == 2
    assert "DATABASE_URL" in stderr.getvalue()


def test_unmigrated_database_exits_2(write_prompt, run_cli, tmp_path):
    prompts = tmp_path / "prompts"
    write_prompt(prompts, name="faq.md", version="1.0.0")
    empty_db = tmp_path / "empty.db"
    sqlite3.connect(empty_db).close()

    code, out, err = run_cli(prompts, database_url=f"sqlite:///{empty_db.as_posix()}")

    assert code == 2
    assert "not migrated" in err
    assert "alembic upgrade head" in err


def test_unreachable_database_exits_2(write_prompt, run_cli, tmp_path):
    prompts = tmp_path / "prompts"
    write_prompt(prompts, name="faq.md", version="1.0.0")
    code, out, err = run_cli(
        prompts, database_url=f"sqlite:///{(tmp_path / 'nodir' / 'x.db').as_posix()}"
    )
    assert code == 2
    assert "exit-code 2" in err
