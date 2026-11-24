"""Print the two evaluation query endpoints for a database that already holds runs.

    python scripts/query_history.py build/demo/run.sqlite3

Runs the app in-process through FastAPI's TestClient, so no server, no curl and
no port are needed, and it behaves the same on Linux, macOS and Windows. The
output is exactly what the endpoints return: one history row per registered
version with the LATEST pass rate, then the versions of the latest run, then the
same URL seen from another organisation (404) and for an unknown run (404).

Exit code 0 when every call behaved as documented, 1 otherwise.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TOOL = "member_service"
PROMPT_KEY = "faq_answer"
OTHER_ORG = "22222222-2222-2222-2222-222222222222"
UNKNOWN_RUN = "00000000-0000-0000-0000-000000000000"


def show(client, path, org=None, expect=200):
    headers = {"X-Org-Id": org} if org else None
    response = client.get(path, headers=headers)
    print("$ curl -s %s%s" % ("-H 'X-Org-Id: %s' " % org if org else "", path))
    print("HTTP %d" % response.status_code)
    try:
        print(json.dumps(response.json(), indent=2, sort_keys=True, ensure_ascii=False))
    except ValueError:
        print(response.text)
    print()
    return response, response.status_code == expect


def main(argv):
    path = Path(argv[1]) if len(argv) > 1 else Path("build/demo/run.sqlite3")
    if not path.exists():
        print("[fail] database %s does not exist; run app.dbcheck init first" % path)
        return 1
    os.environ["DATABASE_URL"] = "sqlite:///" + str(path.resolve())

    from fastapi.testclient import TestClient

    from app.main import app

    checks = []
    with TestClient(app) as client:
        history, ok = show(client, "/api/eval/prompt-versions/%s/%s/history" % (TOOL, PROMPT_KEY))
        checks.append(ok)
        versions = history.json()["versions"] if ok else []
        if not versions:
            print("[fail] history returned no version; register a prompt and record a run first")
            return 1
        latest = versions[0]["latest_run_id"]
        print("latest_pass_rate=%r for version %s (run %s)"
              % (versions[0]["latest_pass_rate"], versions[0]["version"], latest))
        print()

        _, ok = show(client, "/api/eval/runs/%s/versions" % latest)
        checks.append(ok)
        _, ok = show(client, "/api/eval/runs/%s/versions" % latest, org=OTHER_ORG, expect=404)
        checks.append(ok)
        _, ok = show(client, "/api/eval/runs/%s/versions" % UNKNOWN_RUN, expect=404)
        checks.append(ok)

    if all(checks):
        print("all query endpoints behaved as documented")
        return 0
    print("[fail] at least one endpoint returned an unexpected status")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
