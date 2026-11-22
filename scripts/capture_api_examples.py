#!/usr/bin/env python
"""Produce the real HTTP exchanges quoted in the README.

The script builds a throwaway SQLite database, walks one prompt through the
1.0.0 -> 1.1.0 -> 1.2.0 lifecycle against prompts/ (plus a copy in a temp
directory), records two evaluation runs, then calls the query endpoints through
FastAPI's TestClient and prints the verbatim request line, status and JSON body.

    python scripts/capture_api_examples.py

Nothing here is hand written: the README quotes this output.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ORG = UUID("11111111-1111-1111-1111-111111111111")
OTHER_ORG = UUID("22222222-2222-2222-2222-222222222222")
SITE = UUID("33333333-3333-3333-3333-333333333333")
OPERATOR = "platform-ops@example.invalid"

FAQ_BODIES = {
    "1.0.0": """你是会员空间的在线答疑助手。

## 约束
- 只使用【知识库片段】中的事实作答。
- 无法回答时，输出：该问题需要人工确认，已为您转接。

## 知识库片段
{{context}}

## 会员问题
{{question}}
""",
    "1.1.0": """你是会员空间的在线答疑助手。

## 目标
用不超过 120 字回答会员的问题，语气克制、礼貌。

## 约束
- 只使用【知识库片段】中的事实，禁止补充片段之外的政策、价格或时效。
- 价格与金额一律以「分」为单位给出原始数值，不要自行换算或四舍五入。
- 无法回答时，输出：该问题需要人工确认，已为您转接。
- 不透露内部系统名称、接口地址、模型名称。

## 知识库片段
{{context}}

## 会员问题
{{question}}
""",
    "1.2.0": """你是会员空间的在线答疑助手。

## 目标
用不超过 120 字回答会员的问题，语气克制、礼貌；先给结论再给依据。

## 约束
- 只使用【知识库片段】中的事实，禁止补充片段之外的政策、价格或时效。
- 价格与金额一律以「分」为单位给出原始数值，不要自行换算或四舍五入。
- 每个结论后面用【片段编号】标注来源。
- 无法回答时，输出：该问题需要人工确认，已为您转接。
- 不透露内部系统名称、接口地址、模型名称。

## 知识库片段
{{context}}

## 会员问题
{{question}}
""",
}


class Tee:
    """Mirror everything printed to a file, so the README can quote a real run."""

    def __init__(self, stream, handle) -> None:
        self.stream = stream
        self.handle = handle

    def write(self, data: str) -> int:
        self.stream.write(data)
        self.handle.write(data)
        return len(data)

    def flush(self) -> None:
        self.stream.flush()
        self.handle.flush()


def divider(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def write_prompt(directory: Path, version: str) -> None:
    """Write prompts/answers/faq_answer.md at the requested version."""
    path = directory / "answers" / "faq_answer.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    header = [
        "---",
        "tool: member_service",
        "prompt_key: faq_answer",
        f"version: {version}",
        'changelog: "{}: 演示版本演进"'.format(version),
        "---",
        "",
    ]
    path.write_text("\n".join(header) + FAQ_BODIES[version], encoding="utf-8", newline="\n")


def run(command: list[str], environment: dict[str, str]) -> int:
    print("$ " + " ".join(command[1:]))
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, **environment},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    print(completed.stdout.rstrip())
    print(f"[exit code: {completed.returncode}]")
    return completed.returncode


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="prompt-register-api-"))
    database_url = f"sqlite:///{(tmp / 'registry.db').as_posix()}"
    environment = {
        "DATABASE_URL": database_url,
        "PROMPT_CREATED_BY": OPERATOR,
        "EVAL_ORG_ID": str(ORG),
        "EVAL_SITE_ID": str(SITE),
    }
    os.environ.update(environment)

    prompts = tmp / "prompts"
    shutil.copytree(ROOT / "prompts", prompts)

    divider("1. schema, then three successive versions of member_service/faq_answer")
    if run([sys.executable, "-m", "app.dbcheck", "init"], environment) != 0:
        return 1

    # 1.0.0 is the version that was in production before the change.
    write_prompt(prompts, "1.0.0")
    if run([sys.executable, "-m", "app.cli_prompt_register", "--prompt-dir", prompts.as_posix()], environment) != 0:
        return 1

    # The body changed, so the version was raised: 1.0.0 -> 1.1.0.
    write_prompt(prompts, "1.1.0")
    if run([sys.executable, "-m", "app.cli_prompt_register", "--prompt-dir", prompts.as_posix()], environment) != 0:
        return 1

    # A further refinement that has not been evaluated yet: 1.1.0 -> 1.2.0.
    write_prompt(prompts, "1.2.0")
    if run([sys.executable, "-m", "app.cli_prompt_register", "--prompt-dir", prompts.as_posix()], environment) != 0:
        return 1

    if run(
        [
            sys.executable, "-m", "app.cli_model_profile",
            "--name", "default", "--endpoint-alias", "model.main",
            "--temperature", "0.2", "--max-tokens", "512",
        ],
        environment,
    ) != 0:
        return 1

    divider("2. the evaluated service echoes its binding; the executor stores two runs")
    from app.db import get_session_factory, init_engine
    from app.eval_binding import (
        CaseOutcome,
        build_eval_context,
        find_prompt_version,
        record_eval_run,
    )

    init_engine(database_url)
    session = get_session_factory()()
    try:
        binding = build_eval_context(
            session, org_id=ORG, tool="member_service", prompt_key="faq_answer",
            version="1.1.0", model_profile="default",
        )
        print("evaluation-mode payload returned by the service under test:")
        print(json.dumps(binding.as_payload(), indent=2, sort_keys=True))
        print()
        print("the alias resolves to a real address only here, from the environment:")
        print("  MODEL_ALIAS_MODEL_MAIN ->", os.environ.get("MODEL_ALIAS_MODEL_MAIN", "<unset>"))

        row_100 = find_prompt_version(
            session, org_id=ORG, tool="member_service", prompt_key="faq_answer", version="1.0.0"
        )
        row_110 = find_prompt_version(
            session, org_id=ORG, tool="member_service", prompt_key="faq_answer", version="1.1.0"
        )

        run_a = record_eval_run(
            session, org_id=ORG, site_id=SITE, created_by="executor@example.invalid",
            tool="member_service", status="finished", started_at=None, finished_at=None,
            outcomes=[
                CaseOutcome("case-001", row_100.id, True),
                CaseOutcome("case-002", row_100.id, True),
                CaseOutcome("case-003", row_100.id, False),
                CaseOutcome("case-004", row_100.id, False),
            ],
            model_profile_name="default",
        )
        run_b = record_eval_run(
            session, org_id=ORG, site_id=SITE, created_by="executor@example.invalid",
            tool="member_service", status="finished", started_at=None, finished_at=None,
            outcomes=[
                CaseOutcome("case-001", row_110.id, True),
                CaseOutcome("case-002", row_110.id, True),
                CaseOutcome("case-003", row_110.id, True),
                CaseOutcome("case-004", row_110.id, True),
            ],
            model_profile_name="default",
        )
        session.commit()
        run_a_id, run_b_id = run_a.id, run_b.id
        session.close()
    except Exception as exc:  # pragma: no cover - demo diagnostics
        print(f"could not record the demo runs: {type(exc).__name__}: {exc}")
        return 1

    divider("3. HTTP responses (FastAPI TestClient, same code paths as uvicorn)")
    from fastapi.testclient import TestClient
    from app.api_eval import get_db_session
    from app.main import create_app

    application = create_app()
    factory = get_session_factory()

    def override():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    application.dependency_overrides[get_db_session] = override
    with TestClient(application) as client:
        print("$ curl -s -H 'X-Org-Id: {}' \\".format(ORG))
        print("      http://127.0.0.1:8000/api/eval/prompt-versions/member_service/faq_answer/history")
        response = client.get(
            "/api/eval/prompt-versions/member_service/faq_answer/history",
            headers={"X-Org-Id": str(ORG)},
        )
        print(f"HTTP {response.status_code}")
        print(json.dumps(response.json(), indent=2, sort_keys=True, ensure_ascii=False))

        print()
        print("$ curl -s -H 'X-Org-Id: {}' \\".format(ORG))
        print(f"      http://127.0.0.1:8000/api/eval/runs/{run_b_id}/versions")
        response = client.get(
            f"/api/eval/runs/{run_b_id}/versions", headers={"X-Org-Id": str(ORG)}
        )
        print(f"HTTP {response.status_code}")
        print(json.dumps(response.json(), indent=2, sort_keys=True, ensure_ascii=False))

        print()
        print("$ curl -s -H 'X-Org-Id: {}' \\".format(OTHER_ORG))
        print(f"      http://127.0.0.1:8000/api/eval/runs/{run_b_id}/versions")
        response = client.get(
            f"/api/eval/runs/{run_b_id}/versions", headers={"X-Org-Id": str(OTHER_ORG)}
        )
        print(f"HTTP {response.status_code}  (another organisation sees 404, never 403)")
        print(json.dumps(response.json(), indent=2, sort_keys=True, ensure_ascii=False))

        print()
        print("$ curl -s http://127.0.0.1:8000/healthz")
        response = client.get("/healthz")
        print(f"HTTP {response.status_code}")
        print(json.dumps(response.json(), indent=2, sort_keys=True, ensure_ascii=False))

    divider("4. identifiers used above")
    print(f"org_id                     {ORG}")
    print(f"run A (faq_answer 1.0.0)   {run_a_id}")
    print(f"run B (faq_answer 1.1.0)   {run_b_id}")
    print(f"database                   {database_url}")
    return 0


if __name__ == "__main__":
    output = ROOT / "docs" / "checks" / "api-examples.txt"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        original = sys.stdout
        sys.stdout = Tee(original, handle)
        try:
            code = main()
        finally:
            sys.stdout = original
    print(f"wrote docs/checks/api-examples.txt (exit {code})")
    raise SystemExit(code)