"""The two evaluation query endpoints, including their tenancy rules."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .conftest import ORG_A, ORG_B


def test_history_returns_one_row_per_version_with_pass_rates(api_client, seed):
    now = datetime.now(timezone.utc)
    v1 = seed.prompt(tool="member_service", prompt_key="faq_answer", version="1.0.0", sha="1" * 64)
    v2 = seed.prompt(tool="member_service", prompt_key="faq_answer", version="1.1.0", sha="2" * 64)
    run1 = seed.run(results=[(v1.id, True), (v1.id, True), (v1.id, False), (v1.id, False)], finished_at=now - timedelta(days=1))
    run2 = seed.run(results=[(v2.id, True), (v2.id, True), (v2.id, True), (v2.id, False)], finished_at=now)

    response = api_client.get("/api/eval/prompt-versions/member_service/faq_answer/history")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["tool"] == "member_service"
    assert payload["prompt_key"] == "faq_answer"
    assert payload["org_id"] == str(ORG_A)
    assert [item["version"] for item in payload["versions"]] == ["1.0.0", "1.1.0"]
    first, second = payload["versions"]
    assert first["latest_run_id"] == str(run1.id)
    assert first["latest_pass_rate"] == 0.5
    assert second["latest_run_id"] == str(run2.id)
    assert second["latest_pass_rate"] == 0.75


def test_history_uses_the_most_recent_run_not_the_first(api_client, seed):
    now = datetime.now(timezone.utc)
    version = seed.prompt(prompt_key="faq_answer", version="1.0.0")
    old = seed.run(
        results=[(version.id, False), (version.id, False)], finished_at=now - timedelta(days=5)
    )
    new = seed.run(
        results=[(version.id, True), (version.id, True)], finished_at=now - timedelta(hours=2)
    )

    response = api_client.get("/api/eval/prompt-versions/member_service/faq_answer/history")

    item = response.json()["versions"][0]
    assert item["latest_run_id"] == str(new.id)
    assert item["latest_run_id"] != str(old.id)
    assert item["latest_pass_rate"] == 1.0


def test_history_returns_null_pass_rate_when_never_evaluated(api_client, seed):
    now = datetime.now(timezone.utc)
    evaluated = seed.prompt(prompt_key="faq_answer", version="1.0.0", sha="a" * 64)
    seed.prompt(prompt_key="faq_answer", version="1.1.0", sha="b" * 64)
    seed.run(results=[(evaluated.id, True), (evaluated.id, True)], finished_at=now)

    response = api_client.get("/api/eval/prompt-versions/member_service/faq_answer/history")

    assert response.status_code == 200
    versions = {item["version"]: item for item in response.json()["versions"]}
    assert versions["1.0.0"]["latest_pass_rate"] == 1.0
    # null, not 0.0: "never evaluated" must not look like "evaluated and all failed".
    assert versions["1.1.0"]["latest_pass_rate"] is None
    assert versions["1.1.0"]["latest_run_id"] is None
    assert versions["1.1.0"]["latest_run_finished_at"] is None


def test_history_returns_null_pass_rate_when_denominator_is_zero(api_client, seed):
    version = seed.prompt(prompt_key="faq_answer", version="1.0.0")
    seed.run(results=[], finished_at=datetime.now(timezone.utc))

    response = api_client.get("/api/eval/prompt-versions/member_service/faq_answer/history")

    assert response.status_code == 200
    assert response.json()["versions"][0]["latest_pass_rate"] is None


def test_history_is_404_across_orgs(api_client, seed):
    seed.prompt(prompt_key="faq_answer", version="1.0.0", org_id=ORG_B)

    owner = api_client.get(
        "/api/eval/prompt-versions/member_service/faq_answer/history",
        headers={"X-Org-Id": str(ORG_B)},
    )
    foreign = api_client.get(
        "/api/eval/prompt-versions/member_service/faq_answer/history",
        headers={"X-Org-Id": str(ORG_A)},
    )

    assert owner.status_code == 200
    assert foreign.status_code == 404
    assert "no registered prompt version" in foreign.json()["detail"]


def test_history_is_404_for_unknown_prompt(api_client):
    response = api_client.get("/api/eval/prompt-versions/member_service/does_not_exist/history")
    assert response.status_code == 404


def test_history_rejects_malformed_tool_path(api_client):
    response = api_client.get("/api/eval/prompt-versions/bad%20tool/faq/history")
    assert response.status_code == 422


def test_run_versions_returns_only_the_versions_the_run_used(api_client, seed):
    v1 = seed.prompt(prompt_key="faq_answer", version="1.0.0", sha="1" * 64)
    v2 = seed.prompt(prompt_key="faq_answer", version="1.1.0", sha="2" * 64)
    other = seed.prompt(prompt_key="intent_router", version="3.0.0", sha="3" * 64)
    run = seed.run(results=[(v1.id, True), (other.id, False)])

    response = api_client.get(f"/api/eval/runs/{run.id}/versions")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["run_id"] == str(run.id)
    assert payload["tool"] == "member_service"
    assert payload["status"] == "finished"
    returned = [(item["tool"], item["prompt_key"], item["version"]) for item in payload["versions"]]
    assert returned == [
        ("member_service", "faq_answer", "1.0.0"),
        ("member_service", "intent_router", "3.0.0"),
    ]
    assert v2.version not in [item["version"] for item in payload["versions"]]
    assert payload["versions"][0]["content_sha256"] == "1" * 64


def test_run_versions_sorted_by_tool_then_prompt_key(api_client, seed):
    b_tool = seed.prompt(tool="billing_service", prompt_key="refund_reply", version="1.0.0")
    a_second = seed.prompt(tool="member_service", prompt_key="thread_summary", version="1.0.0")
    a_first = seed.prompt(tool="member_service", prompt_key="faq_answer", version="1.0.0")
    run = seed.run(results=[(b_tool.id, True), (a_second.id, True), (a_first.id, True)])

    payload = api_client.get(f"/api/eval/runs/{run.id}/versions").json()

    assert [(item["tool"], item["prompt_key"]) for item in payload["versions"]] == [
        ("billing_service", "refund_reply"),
        ("member_service", "faq_answer"),
        ("member_service", "thread_summary"),
    ]


def test_run_versions_is_404_for_unknown_run(api_client):
    response = api_client.get(f"/api/eval/runs/{uuid4()}/versions")
    assert response.status_code == 404
    assert "no evaluation run" in response.json()["detail"]


def test_run_versions_is_404_across_orgs(api_client, seed):
    version = seed.prompt(prompt_key="faq_answer", version="1.0.0", org_id=ORG_B)
    run = seed.run(results=[(version.id, True)], org_id=ORG_B)

    owner = api_client.get(f"/api/eval/runs/{run.id}/versions", headers={"X-Org-Id": str(ORG_B)})
    foreign = api_client.get(f"/api/eval/runs/{run.id}/versions", headers={"X-Org-Id": str(ORG_A)})

    assert owner.status_code == 200
    assert foreign.status_code == 404
