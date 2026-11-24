#!/usr/bin/env python
"""Regenerate the two AC3 / AC4 evidence files that come from the new scripts.

    python scripts/capture_checks_v3.py

Builds a throwaway SQLite database the same way the delivery write-up describes
(dbcheck init -> prompt-register -> model-profile -> replay_history), then runs
scripts/query_history.py and scripts/verify_alias_only.py against it and writes

    docs/checks/v3-ac3-query.txt
    docs/checks/v3-ac4-verify.txt

Each captured run is asserted: a non-zero exit code or a missing marker line
fails the script with MISMATCHES, exactly like capture_checks_v2.py. Exit code 0
means the evidence files are current.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKS = ROOT / "docs" / "checks"
BUILD = ROOT / "build" / "v3"
DATABASE_URL = "sqlite:///./build/v3/run.sqlite3"
DATABASE_FILE = BUILD / "run.sqlite3"
OPERATOR = "platform-ops@example.invalid"

EXPECTATIONS = {
    "v3-ac3-query.txt": (
        "all query endpoints behaved as documented",
        'latest_pass_rate=0.8 for version 1.1.0',
        "HTTP 404",
    ),
    "v3-ac4-verify.txt": (
        "all AC4 checks passed: only an alias is stored, the address stays in the environment",
        "http(s) literal hits: 0",
        "no url/base_url/endpoint_url/address column; only endpoint_alias",
    ),
}


def env_for(database_url=None):
    environment = dict(os.environ)
    environment["PYTHONIOENCODING"] = "utf-8"
    environment.pop("MODEL_ALIAS_MODEL_MAIN", None)
    if database_url is None:
        environment["DATABASE_URL"] = DATABASE_URL
    else:
        environment["DATABASE_URL"] = database_url
    environment["PROMPT_CREATED_BY"] = OPERATOR
    return environment


def run(command, environment, expect=0):
    completed = subprocess.run(
        command, cwd=str(ROOT), env=environment, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    output = output.replace(str(ROOT) + os.sep, "").replace(str(ROOT), ".")
    return completed.returncode, output.rstrip()


def main() -> int:
    CHECKS.mkdir(parents=True, exist_ok=True)
    if BUILD.exists():
        shutil.rmtree(BUILD)
    BUILD.mkdir(parents=True)

    environment = env_for()
    steps = [
        ([sys.executable, "-m", "app.dbcheck", "init"], None),
        ([sys.executable, "-m", "app.cli_prompt_register", "--prompt-dir", "prompts"], None),
        ([sys.executable, "-m", "app.cli_model_profile", "--name", "model.main",
          "--endpoint-alias", "model.main", "--temperature", "0.2", "--max-tokens", "1024"], None),
        ([sys.executable, "scripts/replay_history.py", "build/v3/run.sqlite3"], None),
    ]
    for command, _ in steps:
        code, output = run(command, environment)
        if code != 0:
            print("[fail] step exited %d: %s" % (code, " ".join(command)))
            print(output)
            return 1
        print("[ok] %s" % " ".join(command[1:]))

    captures = [
        ("v3-ac3-query.txt",
         [sys.executable, "scripts/query_history.py", "build/v3/run.sqlite3"],
         "python scripts/query_history.py build/v3/run.sqlite3"),
        ("v3-ac4-verify.txt",
         [sys.executable, "scripts/verify_alias_only.py", "build/v3/run.sqlite3"],
         "python scripts/verify_alias_only.py build/v3/run.sqlite3"),
    ]

    mismatches = []
    for name, command, pretty in captures:
        code, output = run(command, environment)
        body = ("$ %s\n%s\n[exit code: %d]\n" % (pretty, output, code))
        (CHECKS / name).write_text(body, encoding="utf-8", newline="\n")
        print("[write] docs/checks/%s" % name)
        if code != 0:
            mismatches.append("%s: exit code %d" % (name, code))
            continue
        for marker in EXPECTATIONS[name]:
            if marker not in output:
                mismatches.append("%s: missing %r" % (name, marker[:60]))

    if mismatches:
        print()
        print("MISMATCHES:")
        for item in mismatches:
            print("  - %s" % item)
        return 1
    print("all captured commands matched their expectations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
