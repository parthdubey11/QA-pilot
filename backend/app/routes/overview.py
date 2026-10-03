"""Project overview: pass-rate trend, open bugs by severity, accessibility score over time, last runs."""

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.deps import CurrentUser
from app.models.accessibility import A11yAudit
from app.models.bug import Bug
from app.models.run import Run, RunKind, RunStatus, RunTrigger
from app.models.saved_test import SavedTest, Schedule
from app.routes.projects import get_owned_project

router = APIRouter(tags=["overview"])

HISTORY = 30


class RunPoint(BaseModel):
    id: str
    created_at: datetime
    kind: RunKind
    trigger: RunTrigger
    status: RunStatus
    goal: str
    tests: int
    passed: int
    failed: int
    blocked: int
    pass_rate: float | None  # passed / tests that got a verdict, 0-100; None if no test got a verdict
    bugs_new: int
    a11y_score: int | None
    seconds: float | None


class A11yPoint(BaseModel):
    run_id: str
    created_at: datetime
    score: int
    issues: int


class ProjectOverview(BaseModel):
    runs: list[RunPoint]  # oldest first (last 30)
    open_bugs: dict[str, int]  # severity -> count
    a11y: list[A11yPoint]  # oldest first
    saved_tests: int
    schedules_enabled: int


def run_point(run: Run) -> RunPoint:
    s = run.stats
    passed, failed, blocked = s.get("passed", 0), s.get("failed", 0), s.get("blocked", 0)
    judged = passed + failed + blocked
    return RunPoint(
        id=str(run.id), created_at=run.created_at, kind=run.kind, trigger=run.trigger, status=run.status, goal=run.goal,
        tests=s.get("tests", 0), passed=passed, failed=failed, blocked=blocked,
        pass_rate=round(100 * passed / judged, 1) if judged else None,
        bugs_new=s.get("bugs_new", 0), a11y_score=s.get("a11y_score"), seconds=s.get("seconds"),
    )


@router.get("/projects/{project_id}/overview", response_model=ProjectOverview)
async def project_overview(project_id: str, user: CurrentUser) -> ProjectOverview:
    project = await get_owned_project(user, project_id)
    runs = await Run.find(Run.project_id == project.id).sort(-Run.created_at).limit(HISTORY).to_list()
    audits = await A11yAudit.find(A11yAudit.project_id == project.id).sort(-A11yAudit.created_at).limit(HISTORY).to_list()
    open_bugs = {severity: 0 for severity in ("critical", "high", "medium", "low")}
    async for bug in Bug.find(Bug.project_id == project.id, Bug.status == "open"):
        open_bugs[bug.severity] += 1
    return ProjectOverview(
        runs=[run_point(r) for r in reversed(runs)],
        open_bugs=open_bugs,
        a11y=[A11yPoint(run_id=str(a.run_id), created_at=a.created_at, score=a.score, issues=a.issue_count)
              for a in reversed(audits)],
        saved_tests=await SavedTest.find(SavedTest.project_id == project.id).count(),
        schedules_enabled=await Schedule.find(Schedule.project_id == project.id, Schedule.enabled == True).count(),  # noqa: E712
    )
