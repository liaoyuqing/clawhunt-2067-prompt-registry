#!/usr/bin/env python
"""Regenerate the supplementary evidence in docs/checks/v2-*.txt.

The first delivery round left three review lenses unclear (feasibility,
acceptance criteria, reproducible evidence).  This script produces, from a bare
checkout, every artifact those three lenses ask for:

  v2-environment.txt        interpreter, pinned versions, what is NOT required
  v2-install.txt            requirements files, pip install, alembic paths
  v2-pytest.txt             python -m pytest -q
  v2-test-inventory.txt     test files and case counts
  v2-ac2-idempotent.txt     schema creation, first registration, the identical second run
  v2-ac1-conflict.txt       body edited, version NOT bumped -> exit 1, no rows
  v2-ac1-bumped-ok.txt      same edit WITH a version bump -> accepted
  v2-ac4-alias-only.txt     ai_model_profile stores an alias, never an address
  v2-ac3-history-runs.txt   three successive runs of one version
  v2-ac3-api-responses.txt  the two GET endpoints, including the 404s

Every file starts with the command that produced it and ends with the real
exit code.  Nothing here is hand-written output.

Usage:
    python scripts/capture_checks_v2.py

Exit code 0 means every captured command matched its expectation; 1 means at
least one did not (the mismatching file is still written, so it can be read).
"""

from __future__ import annotations

import io
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKS = ROOT / "docs" / "checks"
BUILD = ROOT / "build" / "v2"
EXPECT_FAILURES: list[str] = []
# Set by main(): every captured command that touches a database uses this URL,
# so a developer's own DATABASE_URL can never leak into the evidence.
DB_URL = ""


# --------------------------------------------------------------------------- #
# plumbing
# --------------------------------------------------------------------------- #
UNSET = object()  # marker: this command must run with DATABASE_URL absent


def env_for(database_url=None) -> dict:
    """Environment for one captured command.

    None     -> the capture database (DB_URL), so nothing leaks from the caller.
    UNSET    -> DATABASE_URL removed, to prove the tool refuses to guess.
    A string -> exactly that URL.
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    if database_url is UNSET:
        target = None
    else:
        target = DB_URL if database_url is None else database_url
    if target:
        env["DATABASE_URL"] = target
    else:
        env.pop("DATABASE_URL", None)
    env["PROMPT_CREATED_BY"] = "platform-ops@example.invalid"
    return env


def run(cmd: str, env: dict | None = None) -> tuple[int, str]:
    completed = subprocess.run(
        cmd,
        shell=True,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env or env_for(),
    )
    body = (completed.stdout or "") + (completed.stderr or "")
    body = body.replace("\r\n", "\n")
    # Tracebacks would otherwise carry this machine's absolute checkout path; strip only
    # that prefix so the captured text stays repo-relative and reproducible elsewhere.
    for prefix in (str(ROOT) + os.sep, str(ROOT) + "/", str(ROOT) + "\\"):
        body = body.replace(prefix, "")
    return completed.returncode, body.rstrip()


def write(name: str, blocks: list[str]) -> Path:
    CHECKS.mkdir(parents=True, exist_ok=True)
    path = CHECKS / name
    text = "\n".join(blocks).rstrip() + "\n"
    with io.open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    print("wrote docs/checks/%s" % name)
    return path


def expect(label: str, actual, wanted) -> None:
    if actual != wanted:
        EXPECT_FAILURES.append("%s: got %r, expected %r" % (label, actual, wanted))


def rows(db: Path, table: str) -> int:
    connection = sqlite3.connect(str(db))
    try:
        return connection.execute("select count(*) from %s" % table).fetchone()[0]
    finally:
        connection.close()


def query(db: Path, statement: str) -> list:
    connection = sqlite3.connect(str(db))
    try:
        return connection.execute(statement).fetchall()
    finally:
        connection.close()


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


# --------------------------------------------------------------------------- #
# 1. environment: what is needed, and just as importantly what is not
# --------------------------------------------------------------------------- #
def capture_environment(db_url: str) -> None:
    pinned = {"alembic", "fastapi", "httpx", "pydantic", "pytest", "pyyaml", "sqlalchemy", "uvicorn"}
    blocks = ["# interpreter and the exact package versions every number below was produced with"]

    code, out = run("python -V")
    blocks += ["$ python -V", out, "[exit code: %d]" % code, ""]
    expect("python -V", code, 0)

    code, out = run("python -m pip list --format=freeze")
    kept = [line for line in out.split("\n") if line.split("==")[0].lower() in pinned]
    blocks += ["$ python -m pip list --format=freeze        (filtered to the runtime + test stack)"]
    blocks += kept
    blocks += ["[exit code: %d]" % code, ""]
    expect("pip list", code, 0)

    blocks += [
        "# A PostgreSQL driver is deliberately absent: every measurement below runs on SQLite.",
        "# The production path (alembic upgrade head against PostgreSQL 16) is covered separately",
        "# in v2-install.txt; it cannot be executed on a machine without a server, and this file",
        "# says so instead of pretending otherwise.",
    ]
    code, out = run('python -c "import psycopg"')
    blocks += ['$ python -c "import psycopg"', out, "[exit code: %d]" % code, ""]
    expect("import psycopg must fail", code, 1)

    code, out = run("python -c \"import socket; s=socket.socket(); s.settimeout(1); "
                    "print('5432 open' if s.connect_ex(('127.0.0.1',5432))==0 else 'no listener on 5432')\"")
    blocks += ["$ # is a PostgreSQL server reachable on 127.0.0.1:5432?", out, "[exit code: %d]" % code, ""]
    expect("port 5432", code, 0)

    blocks += [
        "# Nothing below needs a server, a credential, a token or an environment variable:",
        "# the suite builds its own SQLite databases, exactly as CI runs it.",
    ]
    clean = env_for(UNSET)
    clean["PROMPT_CREATED_BY"] = ""
    code, out = run("python -m pytest -q", env=clean)
    blocks += ["$ unset DATABASE_URL PROMPT_CREATED_BY", "$ python -m pytest -q", out,
               "[exit code: %d]" % code]
    expect("pytest without any configuration", code, 0)
    write("v2-environment.txt", blocks)


# --------------------------------------------------------------------------- #
# 2. install and migration path
# --------------------------------------------------------------------------- #
def capture_install(db_file: Path) -> None:
    blocks = ["# ---- what has to be installed -------------------------------------------"]
    for name in ("requirements.txt", "requirements-dev.txt", "requirements-postgres.txt"):
        blocks += ["$ cat %s" % name, (ROOT / name).read_text(encoding="utf-8").rstrip(), ""]

    code, out = run("python -m pip install -r requirements-dev.txt")
    kept = [line for line in out.split("\n") if not line.startswith("DEPRECATION: Loading egg at")]
    satisfied = [line for line in kept if line.startswith("Requirement already satisfied")]
    blocks += [
        "# DEPRECATION lines about an unrelated .egg in this interpreter are stripped; nothing else edited.",
        "$ python -m pip install -r requirements-dev.txt",
        "\n".join(kept[:6]),
        "... (%d 'already satisfied' line(s) in total)" % len(satisfied),
        "[exit code: %d]" % code,
        "",
    ]
    expect("pip install", code, 0)

    blocks += [
        "# ---- migrations ---------------------------------------------------------",
        "# alembic refuses to guess a database, even for SQLite:",
    ]
    code, out = run("python -m alembic upgrade head", env=env_for(UNSET))
    blocks += ["$ python -m alembic upgrade head            (DATABASE_URL unset)",
               out.split("\n")[-1], "[exit code: %d]" % code, ""]
    expect("alembic without DATABASE_URL", code, 1)

    pg = env_for("postgresql+psycopg://registry:registry@127.0.0.1:5432/prompt_registry")
    code, out = run("python -m alembic upgrade head", env=pg)
    blocks += [
        "$ DATABASE_URL=postgresql+psycopg://registry:registry@127.0.0.1:5432/prompt_registry "
        "python -m alembic upgrade head",
        out.split("\n")[-1],
        "[exit code: %d]" % code,
        "# ^ this machine has no PostgreSQL server and no psycopg driver. The error names both",
        "#   remedies; on CI (pgvector/pgvector:pg16 service container) the same command applies",
        "#   all four tables. The DDL it would apply is rendered below, offline.",
        "",
    ]
    expect("alembic against PostgreSQL on a machine without one", code, 1)

    offline = env_for("sqlite:///./build/v2/db/offline.sqlite3")
    code, out = run("python -m alembic upgrade head --sql", env=offline)
    ddl = [line for line in out.split("\n")
           if line.strip() and not line.startswith("INFO  [alembic")]
    blocks += ["$ python -m alembic upgrade head --sql        (offline: renders DDL, connects nowhere)",
               "\n".join(ddl[:14]),
               "... (%d non-blank DDL lines in total)" % len(ddl),
               "[exit code: %d]" % code, ""]
    expect("alembic offline SQL", code, 0)

    local_url = "sqlite:///./build/v2/db/mig.sqlite3"
    code, out = run("python -m alembic upgrade head", env=env_for(local_url))
    blocks += ["$ DATABASE_URL=%s python -m alembic upgrade head" % local_url,
               out, "[exit code: %d]" % code]
    expect("alembic against SQLite", code, 0)
    write("v2-install.txt", blocks)


# --------------------------------------------------------------------------- #
# 3. the suite and its inventory
# --------------------------------------------------------------------------- #
def capture_pytest() -> None:
    code, out = run("python -m pytest -q")
    expect("pytest", code, 0)
    write("v2-pytest.txt", ["$ python -m pytest -q", out, "", "[exit code: %d]" % code])

    code, out = run("python -m pytest --collect-only -q")
    counts: dict[str, int] = {}
    for line in out.split("\n"):
        if line.startswith("tests/") and "::" in line:
            counts[line.split("::")[0]] = counts.get(line.split("::")[0], 0) + 1
    listing = ["%s: %d" % (name, count) for name, count in sorted(counts.items())]
    total = sum(counts.values())
    write("v2-test-inventory.txt", [
        "$ python -m pytest --collect-only -q",
        *listing,
        "",
        "test files: %d" % len(counts),
        "test cases: %d" % total,
        "",
        "[exit code: %d]" % code,
    ])
    expect("test case count", total, 54)


# --------------------------------------------------------------------------- #
# 4. AC2 then AC1: registration, idempotency, the in-place edit
# --------------------------------------------------------------------------- #
def capture_registration(db_url: str, db_file: Path) -> None:
    blocks = ["# AC2 -- registering the same content twice must not create a second row"]
    code, out = run("python -m app.dbcheck init", env=env_for(db_url))
    blocks += ["$ python -m app.dbcheck init", out, "[exit code: %d]" % code, ""]
    expect("dbcheck init", code, 0)

    before = rows(db_file, "ai_prompt_version")
    code, out = run("python -m app.cli_prompt_register --prompt-dir prompts")
    after = rows(db_file, "ai_prompt_version")
    blocks += ["# first run", "$ python -m app.cli_prompt_register --prompt-dir prompts", out,
               "[exit code: %d]" % code, "ai_prompt_version rows: %d -> %d" % (before, after), ""]
    expect("first registration exit", code, 0)
    expect("first registration rows", after - before, 5)

    code, out = run("python -m app.cli_prompt_register --prompt-dir prompts")
    after2 = rows(db_file, "ai_prompt_version")
    blocks += ["# second run: byte-identical input, must insert nothing",
               "$ python -m app.cli_prompt_register --prompt-dir prompts", out,
               "[exit code: %d]" % code, "ai_prompt_version rows: %d -> %d" % (after, after2)]
    expect("second registration exit", code, 0)
    expect("second registration rows", after2, after)
    write("v2-ac2-idempotent.txt", blocks)


def _copy_prompts(destination: Path) -> Path:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(ROOT / "prompts", destination)
    return destination


def _edit_faq(prompt_dir: Path, *, version: str, body_line: str) -> None:
    """Rewrite answers/faq_answer.md: set the front-matter version, append one body line.

    Written with an explicit encoding because on Windows the console default
    (cp936) mangles the Chinese body and silently changes the hash.
    """
    path = prompt_dir / "answers" / "faq_answer.md"
    with io.open(path, encoding="utf-8", newline="") as handle:
        lines = handle.read().replace("\r\n", "\n").split("\n")
    closing = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
    header = [line.replace("version: 1.1.0", "version: %s" % version) for line in lines[: closing + 1]]
    body = lines[closing + 1 :]
    with io.open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(header + ["", body_line] + body))


def capture_ac1(db_file: Path) -> None:
    source = _copy_prompts(BUILD / "ac1_prompts")
    _edit_faq(source, version="1.1.0", body_line="追加一行正文：内容已改，但 version 仍然是 1.1.0。")

    before = rows(db_file, "ai_prompt_version")
    code, out = run("python -m app.cli_prompt_register --prompt-dir build/v2/ac1_prompts")
    after = rows(db_file, "ai_prompt_version")
    blocks = [
        "# AC1 -- the body of member_service/faq_answer changed in place, version left at 1.1.0",
        "# input: a copy of prompts/ with exactly one extra body line in answers/faq_answer.md",
        "# must: exit non-zero, emit code=version_content_conflict, write NOTHING",
        "$ python -m app.cli_prompt_register --prompt-dir build/v2/ac1_prompts",
        out,
        "[exit code: %d]" % code,
        "# (the 'file(s)' count on the header line counts action entries only; the conflicting",
        "#  file is reported as an error instead, so 5 files - 1 error = 4 actions)",
        "ai_prompt_version rows: %d -> %d" % (before, after),
    ]
    expect("AC1 exit code", code, 1)
    expect("AC1 wrote nothing", after, before)
    expect("AC1 error code present", "code=version_content_conflict" in out, True)
    write("v2-ac1-conflict.txt", blocks)

    _edit_faq(source, version="1.2.0", body_line="追加一行正文，同时把 version 提到 1.2.0。")
    before2 = rows(db_file, "ai_prompt_version")
    code, out = run("python -m app.cli_prompt_register --prompt-dir build/v2/ac1_prompts")
    after2 = rows(db_file, "ai_prompt_version")
    blocks = [
        "# AC1 control -- the very same body change, but WITH the version bumped to 1.2.0",
        "# must: exit 0 and insert exactly one new row",
        "$ python -m app.cli_prompt_register --prompt-dir build/v2/ac1_prompts",
        out,
        "[exit code: %d]" % code,
        "ai_prompt_version rows: %d -> %d" % (before2, after2),
    ]
    expect("AC1 control exit code", code, 0)
    expect("AC1 control inserted one row", after2 - before2, 1)
    write("v2-ac1-bumped-ok.txt", blocks)
    shutil.rmtree(source, ignore_errors=True)


# --------------------------------------------------------------------------- #
# 5. AC4: an alias is stored, an address never is
# --------------------------------------------------------------------------- #
def capture_ac4(db_url: str, db_file: Path) -> None:
    columns = [row[1] for row in query(db_file, "PRAGMA table_info(ai_model_profile)")]
    prompt_columns = [row[1] for row in query(db_file, "PRAGMA table_info(ai_prompt_version)")]

    code, out = run("python -m app.cli_model_profile --name model.main --endpoint-alias model.main "
                    "--temperature 0.2 --max-tokens 1024")
    stored = query(db_file, "select name, endpoint_alias, params from ai_model_profile")

    tables = [row[0] for row in query(db_file, "select name from sqlite_master where type='table' order by name")]
    hits: list[tuple[str, str, str]] = []
    for table in tables:
        table_columns = [row[1] for row in query(db_file, "PRAGMA table_info(%s)" % table)]
        for row in query(db_file, "select * from %s" % table):
            for column, value in zip(table_columns, row):
                if isinstance(value, str) and ("http://" in value or "https://" in value):
                    hits.append((table, column, value))

    alias_env = run('python -c "from app import config; print(config.alias_env_var(\'model.main\'))"')
    resolve = run('python -c "from app import config; print(config.resolve_model_endpoint(\'model.main\'))"')

    # There is no sqlite3 CLI on this machine, so the two inspection commands are
    # python one-liners that run anywhere the project's own dependencies are installed.
    columns_cmd = ("python -c \"import sqlite3; c=sqlite3.connect('build/v2/db/run.sqlite3'); "
                   "print(','.join(r[1] for r in c.execute('PRAGMA table_info(ai_model_profile)')))\"")
    columns_out = run(columns_cmd)
    row_cmd = ("python -c \"import sqlite3; c=sqlite3.connect('build/v2/db/run.sqlite3'); "
               "print(c.execute('select name, endpoint_alias, params from ai_model_profile').fetchone())\"")
    row_out = run(row_cmd)

    blocks = [
        "# AC4 -- ai_model_profile keeps a name, an alias and sampling parameters; the address",
        "#        lives in the deployment environment and is never written to the database.",
        "",
        "# (no sqlite3 CLI is installed here, so the two inspections below are python one-liners",
        "#  that need nothing beyond the project's own dependencies)",
        "$ " + columns_cmd.replace('"', '"'),
        columns_out[1],
        "[exit code: %d]" % columns_out[0],
        "ai_prompt_version columns: " + ",".join(prompt_columns),
        "# no url / base_url / endpoint_address column exists, and none of the four tables",
        "# carries an address anywhere in a row.",
        "",
        "$ python -m app.cli_model_profile --name model.main --endpoint-alias model.main "
        "--temperature 0.2 --max-tokens 1024",
        out,
        "[exit code: %d]" % code,
        "",
        "$ " + row_cmd.replace('"', '"'),
        row_out[1],
        "[exit code: %d]" % row_out[0],
        "",
        "# every table, every row, every string column, scanned for a literal http(s) address:",
        "tables: " + ", ".join(tables),
        "http literal hits: %d" % len(hits),
        "",
        "# the alias turns into an address only through the environment, at call time:",
        '$ python -c "from app import config; print(config.alias_env_var(\'model.main\'))"',
        alias_env[1],
        "[exit code: %d]" % alias_env[0],
        "",
        "# and with the variable unset it fails loudly rather than falling back to a default:",
        '$ python -c "from app import config; print(config.resolve_model_endpoint(\'model.main\'))"',
        resolve[1],
        "[exit code: %d]" % resolve[0],
    ]
    expect("model profile exit", code, 0)
    expect("alias literal stored", stored[0][1] if stored else None, "model.main")
    expect("no address anywhere in the database", len(hits), 0)
    expect("alias_env_var", alias_env[1], "MODEL_ALIAS_MODEL_MAIN")
    expect("unresolvable alias fails", resolve[0], 1)
    write("v2-ac4-alias-only.txt", blocks)


# --------------------------------------------------------------------------- #
# 6. AC3: three runs of one version, then the two endpoints over real HTTP
# --------------------------------------------------------------------------- #
def capture_ac3_runs(db_file: Path) -> str:
    code, out = run("python scripts/replay_history.py build/v2/db/run.sqlite3")
    seeded = query(
        db_file,
        "select r.id, r.finished_at,"
        " sum(case when e.passed then 1 else 0 end) as passed, count(*) as total"
        " from eval_run r join eval_result e on e.run_id = r.id"
        " group by r.id order by r.finished_at",
    )
    blocks = [
        "# AC3 -- three successive evaluation runs of the SAME version 1.1.0. The history endpoint",
        "#        must report the pass rate of the most recent run, not the first and not the mean.",
        "$ python scripts/replay_history.py build/v2/db/run.sqlite3",
        out,
        "[exit code: %d]" % code,
        "",
        "# the same three runs, read straight out of the database:",
    ]
    for run_id, finished_at, passed, total in seeded:
        blocks.append("run %s finished_at=%s passed=%d total=%d rate=%.4f"
                      % (run_id, finished_at, passed, total, passed / total))
    blocks += ["", "# 0.6000 / 0.9000 / 0.8000, mean 0.7667 - three distinguishable candidates."]
    expect("replay_history exit", code, 0)
    expect("three seeded runs", len(seeded), 3)
    write("v2-ac3-history-runs.txt", blocks)
    return seeded[-1][0]


def _get(base: str, path: str, org: str | None = None) -> tuple[int, str]:
    request = urllib.request.Request(base + path)
    if org:
        request.add_header("X-Org-Id", org)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8")


def capture_ac3_api(db_url: str, db_file: Path, latest_run_id: str) -> None:
    port = free_port()
    base = "http://127.0.0.1:%d" % port
    server = subprocess.Popen(
        "python -m uvicorn app.main:app --host 127.0.0.1 --port %d" % port,
        cwd=str(ROOT),
        env=env_for(db_url),
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                if _get(base, "/healthz")[0] == 200:
                    break
            except Exception:
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError("uvicorn did not become ready within 60s")

        path = "/api/eval/prompt-versions/member_service/faq_answer/history"
        status, body = _get(base, path)
        history = json.loads(body)
        row_latest = next(item for item in history["versions"] if item["version"] == "1.1.0")
        row_never = next(item for item in history["versions"] if item["version"] == "1.2.0")

        blocks = ["# AC3 -- both endpoints, over a real socket, against the database above.", ""]
        status_health, body_health = _get(base, "/healthz")
        blocks += ["$ curl -s %s/healthz" % base, "HTTP %d" % status_health,
                   json.dumps(json.loads(body_health), indent=2, sort_keys=True), ""]

        blocks += [
            "# 1. every registered version with its MOST RECENT run",
            "$ curl -s %s%s" % (base, path),
            "HTTP %d" % status,
            json.dumps(history, indent=2, sort_keys=True, ensure_ascii=False),
            "",
            "# 1.1.0 -> %r  (the third run, 0.8; not the first, 0.6, and not the mean, 0.7667)"
            % row_latest["latest_pass_rate"],
            "# 1.2.0 -> %s  (registered but never evaluated: null, never 0.0)"
            % json.dumps(row_never["latest_pass_rate"]),
            "",
        ]

        run_path = "/api/eval/runs/%s/versions" % row_latest["latest_run_id"]
        status_run, body_run = _get(base, run_path)
        run_versions = json.loads(body_run)
        registered = rows(db_file, "ai_prompt_version")
        blocks += [
            "# 2. the versions one specific run actually exercised (not the whole registry)",
            "$ curl -s %s%s" % (base, run_path),
            "HTTP %d" % status_run,
            json.dumps(run_versions, indent=2, sort_keys=True, ensure_ascii=False),
            "# the registry holds %d versions at this point; this run reports %d."
            % (registered, len(run_versions["versions"])),
            "",
        ]

        status_unknown, body_unknown = _get(base, "/api/eval/runs/00000000-0000-0000-0000-000000000000/versions")
        blocks += ["# 3. a run id that does not exist",
                   "$ curl -s %s/api/eval/runs/00000000-0000-0000-0000-000000000000/versions" % base,
                   "HTTP %d" % status_unknown, body_unknown, ""]

        other_org = "22222222-2222-2222-2222-222222222222"
        status_org, body_org = _get(base, run_path, org=other_org)
        blocks += ["# 4. the same run id, seen from another organisation (404, not 403: 403 would",
                   "#    confirm the id exists)",
                   '$ curl -s -H "X-Org-Id: %s" %s%s' % (other_org, base, run_path),
                   "HTTP %d" % status_org, body_org, ""]

        status_hist_org, body_hist_org = _get(base, path, org=other_org)
        blocks += ["# 5. the same history path from another organisation",
                   '$ curl -s -H "X-Org-Id: %s" %s%s' % (other_org, base, path),
                   "HTTP %d" % status_hist_org, body_hist_org]
        write("v2-ac3-api-responses.txt", blocks)

        expect("history status", status, 200)
        expect("latest pass rate", row_latest["latest_pass_rate"], 0.8)
        expect("never-evaluated version is null", row_never["latest_pass_rate"], None)
        # replay_history seeds plain 32-hex ids; the API renders them as canonical UUIDs,
        # so compare with the dashes removed.
        expect("history run id",
               row_latest["latest_run_id"].replace("-", ""),
               latest_run_id.replace("-", ""))
        expect("run versions status", status_run, 200)
        expect("run versions only its own", len(run_versions["versions"]), 1)
        expect("unknown run 404", status_unknown, 404)
        expect("cross-org run 404", status_org, 404)
        expect("cross-org history 404", status_hist_org, 404)
    finally:
        # shell=True means we hold the shell, not uvicorn: kill the whole tree on Windows,
        # otherwise the child keeps the sqlite file open and the next run cannot reset it.
        if os.name == "nt":
            subprocess.run("taskkill /F /T /PID %d" % server.pid,
                           shell=True, capture_output=True)
        else:
            server.terminate()
        try:
            server.wait(timeout=20)
        except subprocess.TimeoutExpired:
            server.kill()
        # give the OS a moment to release the file handle
        time.sleep(1.5)


# --------------------------------------------------------------------------- #
def main() -> int:
    global DB_URL
    shutil.rmtree(BUILD, ignore_errors=True)
    BUILD.mkdir(parents=True, exist_ok=True)
    # Everything lands under build/v2 (git-ignored) so the captured command lines show
    # repository-relative paths and can be pasted back verbatim.
    workspace = BUILD / "db"
    workspace.mkdir(parents=True, exist_ok=True)
    db_file = workspace / "run.sqlite3"
    # A previous run's database would make the row counts in the evidence wrong.
    for stale in workspace.glob("*.sqlite3"):
        stale.unlink()
    db_url = "sqlite:///./build/v2/db/run.sqlite3"
    DB_URL = db_url

    print("workspace: %s" % workspace)
    capture_environment(db_url)
    capture_install(db_file)
    capture_pytest()
    capture_registration(db_url, db_file)
    capture_ac1(db_file)
    capture_ac4(db_url, db_file)
    latest_run_id = capture_ac3_runs(db_file)
    capture_ac3_api(db_url, db_file, latest_run_id)

    print("")
    if EXPECT_FAILURES:
        print("MISMATCHES (%d):" % len(EXPECT_FAILURES))
        for item in EXPECT_FAILURES:
            print("  - %s" % item)
        return 1
    print("all captured commands matched their expectations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
