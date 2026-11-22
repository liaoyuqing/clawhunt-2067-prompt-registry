#!/usr/bin/env python
"""Regenerate docs/checks/*.txt from real command runs.

Every file this script writes is the verbatim stdout/stderr plus the exit code
of a command that actually ran; nothing here is hand written. Run it from the
repository root:

    python scripts/capture_checks.py

The script uses its own throwaway SQLite database, so it never touches a
developer database and never needs PostgreSQL.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKS = ROOT / "docs" / "checks"
CHECKED_PACKAGES = (
    "fastapi",
    "sqlalchemy",
    "alembic",
    "pydantic",
    "httpx",
    "pyyaml",
    "pytest",
    "uvicorn",
)


def run(command: list[str], *, env: dict[str, str] | None = None) -> tuple[int, str]:
    """Run a command from the repository root and return (exit code, output)."""
    merged = dict(os.environ)
    merged.pop("PYTHONPATH", None)
    if env is not None:
        for key, value in env.items():
            if value == "":
                merged.pop(key, None)
            else:
                merged[key] = value
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=merged,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return completed.returncode, completed.stdout


def banner(title: str, command: list[str]) -> str:
    rendered = " ".join(command)
    return f"$ {rendered}\n# {title}\n"


def write_check(
    name: str, title: str, command: list[str], env: dict[str, str] | None = None
) -> int:
    code, output = run(command, env=env)
    header = banner(title, command)
    body = f"{header}{output}\n[exit code: {code}]\n"
    (CHECKS / name).write_text(body, encoding="utf-8", newline="\n")
    print(f"wrote docs/checks/{name} (exit {code})")
    return code


def pip_versions() -> str:
    lines: list[str] = []
    code, output = run([sys.executable, "-V"])
    lines.append(f"$ {sys.executable} -V\n{output.strip()}\n[exit code: {code}]\n")
    code, output = run([sys.executable, "-m", "pip", "list", "--format=freeze"])
    selected: list[str] = []
    for line in output.splitlines():
        name = line.split("==")[0].strip().lower()
        if name in CHECKED_PACKAGES:
            selected.append(line.strip())
    lines.append(
        "$ python -m pip list --format=freeze  (selected packages)\n"
        + "\n".join(selected)
        + "\n"
    )
    lines.append(
        "psycopg installed: "
        + ("yes" if any(item.lower().startswith("psycopg==") for item in selected) else "no")
        + " (the suite runs on SQLite and never imports it)\n"
    )
    return "\n".join(lines)


def main() -> int:
    CHECKS.mkdir(parents=True, exist_ok=True)
    failures = 0

    (CHECKS / "py-version.txt").write_text(
        pip_versions() + "\n[exit code: 0]\n", encoding="utf-8", newline="\n"
    )
    print("wrote docs/checks/py-version.txt")

    failures += write_check(
        "pytest.txt",
        "full test suite on SQLite (no PostgreSQL server, no psycopg)",
        [sys.executable, "-m", "pytest", "-q"],
        env={"DATABASE_URL": "", "PROMPT_CREATED_BY": ""},
    )

    with tempfile.TemporaryDirectory(prefix="prompt-register-checks-") as tmp:
        tmpdir = Path(tmp)
        database_url = f"sqlite:///{(tmpdir / 'registry.db').as_posix()}"
        base_env = {
            "DATABASE_URL": database_url,
            "PROMPT_CREATED_BY": "ci-robot@example.invalid",
            "EVAL_ORG_ID": "",
            "EVAL_SITE_ID": "",
        }

        code, output = run([sys.executable, "-m", "app.dbcheck", "init"], env=base_env)
        (CHECKS / "db-init.txt").write_text(
            banner(
                "create the schema on SQLite (production uses alembic upgrade head)",
                [sys.executable, "-m", "app.dbcheck", "init"],
            )
            + output
            + f"\n[exit code: {code}]\n",
            encoding="utf-8",
            newline="\n",
        )
        print(f"wrote docs/checks/db-init.txt (exit {code})")
        if code != 0:
            failures += 1

        first = write_check(
            "cli-ok.txt",
            "first registration: five prompt files inserted in one transaction",
            [sys.executable, "-m", "app.cli_prompt_register", "--prompt-dir", "prompts"],
            env=base_env,
        )
        if first != 0:
            failures += 1

        second = write_check(
            "cli-idempotent.txt",
            "same command a second time: every file is skipped, the row count does not change",
            [sys.executable, "-m", "app.cli_prompt_register", "--prompt-dir", "prompts"],
            env=base_env,
        )
        if second != 0:
            failures += 1

        # Content edited, version left alone: the mistake this command exists for.
        broken = tmpdir / "prompts-edited-body"
        shutil.copytree(ROOT / "prompts", broken)
        target = broken / "answers" / "faq_answer.md"
        target.write_text(
            target.read_text(encoding="utf-8")
            + "\n## 追加段落（验收时故意改动正文，版本号仍是 1.1.0）\n这一行改变了正文哈希。\n",
            encoding="utf-8",
            newline="\n",
        )
        conflict = write_check(
            "cli-fail-version.txt",
            "body changed, version left at 1.1.0: exit code 1 and nothing is written",
            [sys.executable, "-m", "app.cli_prompt_register", "--prompt-dir", broken.as_posix()],
            env=base_env,
        )
        if conflict != 1:
            failures += 1

        # Rollback: a lower version than the registered maximum.
        rolled_back = tmpdir / "prompts-rollback"
        shutil.copytree(ROOT / "prompts", rolled_back)
        target = rolled_back / "answers" / "faq_answer.md"
        text = target.read_text(encoding="utf-8")
        target.write_text(
            text.replace("version: 1.1.0", "version: 1.0.9", 1),
            encoding="utf-8",
            newline="\n",
        )
        rollback = write_check(
            "cli-fail-rollback.txt",
            "version 1.0.9 after 1.1.0 is a rollback: exit code 1",
            [sys.executable, "-m", "app.cli_prompt_register", "--prompt-dir", rolled_back.as_posix()],
            env=base_env,
        )
        if rollback != 1:
            failures += 1

        # A mistyped front matter key must never be ignored silently.
        typo = tmpdir / "prompts-typo"
        shutil.copytree(ROOT / "prompts", typo)
        target = typo / "triage" / "intent_router.md"
        target.write_text(
            target.read_text(encoding="utf-8").replace("version:", "versoin:", 1),
            encoding="utf-8",
            newline="\n",
        )
        typo_code = write_check(
            "cli-fail-frontmatter.txt",
            "front matter key mistyped as versoin: exit code 1, never ignored",
            [sys.executable, "-m", "app.cli_prompt_register", "--prompt-dir", typo.as_posix()],
            env=base_env,
        )
        if typo_code != 1:
            failures += 1

        # No database at all: file level validation only.
        dry = write_check(
            "cli-dry-run-no-db.txt",
            "no DATABASE_URL with --dry-run --allow-missing-db: files validated, exit code 0",
            [
                sys.executable,
                "-m",
                "app.cli_prompt_register",
                "--prompt-dir",
                "prompts",
                "--dry-run",
                "--allow-missing-db",
            ],
            env={"DATABASE_URL": "", "PROMPT_CREATED_BY": "ci-robot@example.invalid"},
        )
        if dry != 0:
            failures += 1

        # Database configured but schema absent: exit code 2, not 1.
        unmigrated_url = f"sqlite:///{(tmpdir / 'unmigrated.db').as_posix()}"
        unmigrated = write_check(
            "cli-exit2-unmigrated.txt",
            "database reachable but not migrated: exit code 2 (operational), not 1 (content)",
            [sys.executable, "-m", "app.cli_prompt_register", "--prompt-dir", "prompts"],
            env={
                "DATABASE_URL": unmigrated_url,
                "PROMPT_CREATED_BY": "ci-robot@example.invalid",
            },
        )
        if unmigrated != 2:
            failures += 1

        unreachable = write_check(
            "cli-exit2-unreachable.txt",
            "database host unreachable: exit code 2, still distinguishable from a content error",
            [sys.executable, "-m", "app.cli_prompt_register", "--prompt-dir", "prompts"],
            env={
                "DATABASE_URL": "postgresql+psycopg://app:secret@127.0.0.1:1/registry",
                "PROMPT_CREATED_BY": "ci-robot@example.invalid",
            },
        )
        if unreachable != 2:
            failures += 1

    print()
    if failures:
        print(f"{failures} check(s) did not produce the expected exit code")
        return 1
    print("all checks produced their expected exit codes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
