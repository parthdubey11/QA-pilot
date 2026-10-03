from datetime import timedelta

import httpx
from beanie import PydanticObjectId

from app.models.accessibility import A11yAudit
from app.models.base import utcnow
from app.models.bug import Bug, title_key
from app.models.run import Run
from tests.conftest import RegisterFn


async def test_overview_aggregates_runs_bugs_and_a11y(client: httpx.AsyncClient, register: RegisterFn) -> None:
    alice = await register(email="alice@example.com")
    bob = await register(email="bob@example.com")
    pid = (await client.post("/projects", json={"name": "Shop", "base_url": "http://shop.test",
                                                 "authorised_testing_confirmed": True}, headers=alice)).json()["id"]
    empty = (await client.get(f"/projects/{pid}/overview", headers=alice)).json()
    assert empty == {"runs": [], "open_bugs": {"critical": 0, "high": 0, "medium": 0, "low": 0}, "a11y": [],
                     "saved_tests": 0, "schedules_enabled": 0}

    project_id, now = PydanticObjectId(pid), utcnow()
    first = Run(project_id=project_id, created_by=project_id, goal="g1", status="completed", created_at=now - timedelta(days=1),
                stats={"tests": 4, "passed": 2, "failed": 1, "blocked": 1, "errors": 0, "a11y_score": 60, "seconds": 300})
    second = Run(project_id=project_id, created_by=project_id, goal="g2", status="completed", created_at=now, kind="replay",
                 stats={"tests": 3, "passed": 3, "failed": 0, "errors": 0})
    failed = Run(project_id=project_id, created_by=project_id, goal="g3", status="failed", created_at=now - timedelta(hours=1),
                 stats={"tests": 2, "errors": 2})
    for run in (first, second, failed):
        await run.insert()
    for severity, status in (("critical", "open"), ("high", "open"), ("high", "open"), ("low", "fixed")):
        await Bug(project_id=project_id, title=f"{severity}{status}", title_key=title_key(f"{severity}{status}{utcnow()}"),
                  severity=severity, status=status, steps=["x"], expected="e", actual="a", suggested_fix="f",
                  run_ids=[first.id], first_run_id=first.id, first_test_title="t").insert()  # type: ignore[list-item, arg-type]
    await A11yAudit(project_id=project_id, run_id=first.id, score=60, pages=[], issue_count=9,  # type: ignore[arg-type]
                    created_at=first.created_at).insert()

    data = (await client.get(f"/projects/{pid}/overview", headers=alice)).json()

    assert [r["goal"] for r in data["runs"]] == ["g1", "g3", "g2"]  # oldest first
    assert [r["pass_rate"] for r in data["runs"]] == [50.0, None, 100.0]  # errors aren't counted as verdicts
    assert data["runs"][2]["kind"] == "replay" and data["runs"][0]["a11y_score"] == 60
    assert data["open_bugs"] == {"critical": 1, "high": 2, "medium": 0, "low": 0}  # fixed bugs excluded
    assert [(a["score"], a["issues"]) for a in data["a11y"]] == [(60, 9)]
    assert (await client.get(f"/projects/{pid}/overview", headers=bob)).status_code == 404
