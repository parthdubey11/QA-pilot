"""Test runs: start one (creates a run + a job for the worker), read it, and stream its steps live (SSE)."""

import asyncio
import json
import re
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from app.core.deps import CurrentUser, parse_object_id
from app.models.accessibility import A11yAudit, A11yIssue
from app.models.bug import Bug
from app.models.project import Project
from app.models.run import ACTIVE_RUN_STATUSES, Run, RunStep
from app.models.test_case import TestCase
from app.models.user import User
from app.routes.projects import get_owned_project
from app.schemas.runs import RunCreate, RunRead, RunStepRead, TestCaseRead
from app.services.report_html import build_report_html
from app.services.run_steps import storage_root
from app.services.runs import ActiveRunError, RunQuotaError, start_run

router = APIRouter(tags=["runs"])

STREAM_POLL_SECONDS = 0.5
STREAM_HEARTBEAT_SECONDS = 15.0


async def get_owned_run(user: User, run_id: str) -> tuple[Run, Project]:
    oid = parse_object_id(run_id)
    run = await Run.get(oid) if oid else None
    project = await Project.get(run.project_id) if run else None
    if run is None or project is None or project.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    return run, project


def run_read(run: Run, project: Project) -> RunRead:
    return RunRead.model_validate(
        run.model_dump(exclude={"id", "project_id", "created_by", "saved_test_ids", "schedule_id"})
        | {"id": str(run.id), "project_id": str(run.project_id), "project_name": project.name,
           "saved_test_ids": [str(i) for i in run.saved_test_ids],
           "schedule_id": str(run.schedule_id) if run.schedule_id else None}
    )


def step_read(step: RunStep) -> RunStepRead:
    return RunStepRead.model_validate(
        step.model_dump(exclude={"id", "run_id", "screenshot_path"}) | {"has_screenshot": bool(step.screenshot_path)}
    )


def test_case_read(test_case: TestCase) -> TestCaseRead:
    return TestCaseRead.model_validate(
        test_case.model_dump(exclude={"id", "run_id", "project_id", "bug_id", "final_screenshot_path"})
        | {"bug_id": str(test_case.bug_id) if test_case.bug_id else None,
           "has_final_screenshot": bool(test_case.final_screenshot_path)}
    )


async def test_cases_of(run: Run) -> list[TestCase]:
    return await TestCase.find(TestCase.run_id == run.id).sort(+TestCase.index).to_list()


async def steps_after(run: Run, after: int) -> list[RunStep]:
    return await RunStep.find(RunStep.run_id == run.id, RunStep.index > after).sort(+RunStep.index).to_list()


# ---------- create / list ----------


@router.post("/projects/{project_id}/runs", response_model=RunRead, status_code=status.HTTP_201_CREATED)
async def create_run(project_id: str, body: RunCreate, user: CurrentUser) -> RunRead:
    project = await get_owned_project(user, project_id)
    assert user.id is not None
    try:
        run = await start_run(project, created_by=user.id, goal=body.goal, options=body.options)
    except RunQuotaError as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from None
    except ActiveRunError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return run_read(run, project)


@router.get("/projects/{project_id}/runs", response_model=list[RunRead])
async def list_runs(project_id: str, user: CurrentUser, limit: int = Query(20, ge=1, le=100)) -> list[RunRead]:
    project = await get_owned_project(user, project_id)
    runs = await Run.find(Run.project_id == project.id).sort(-Run.created_at).limit(limit).to_list()
    return [run_read(r, project) for r in runs]


# ---------- one run ----------


@router.get("/runs/{run_id}", response_model=RunRead)
async def get_run(run_id: str, user: CurrentUser) -> RunRead:
    run, project = await get_owned_run(user, run_id)
    return run_read(run, project)


@router.get("/runs/{run_id}/steps", response_model=list[RunStepRead])
async def list_steps(run_id: str, user: CurrentUser, after: int = Query(-1, ge=-1)) -> list[RunStepRead]:
    run, _ = await get_owned_run(user, run_id)
    return [step_read(s) for s in await steps_after(run, after)]


@router.get("/runs/{run_id}/test-cases", response_model=list[TestCaseRead])
async def list_test_cases(run_id: str, user: CurrentUser) -> list[TestCaseRead]:
    run, _ = await get_owned_run(user, run_id)
    return [test_case_read(tc) for tc in await test_cases_of(run)]


@router.get("/runs/{run_id}/steps/{index}/screenshot", response_class=FileResponse)
async def step_screenshot(run_id: str, index: int, user: CurrentUser) -> FileResponse:
    run, _ = await get_owned_run(user, run_id)
    step = await RunStep.find_one(RunStep.run_id == run.id, RunStep.index == index)
    return screenshot_file(step.screenshot_path if step else None)


@router.get("/runs/{run_id}/test-cases/{index}/screenshot", response_class=FileResponse)
async def test_case_screenshot(run_id: str, index: int, user: CurrentUser) -> FileResponse:
    """The final screenshot of a test case (what the judge looked at)."""
    run, _ = await get_owned_run(user, run_id)
    test_case = await TestCase.find_one(TestCase.run_id == run.id, TestCase.index == index)
    return screenshot_file(test_case.final_screenshot_path if test_case else None)


def resolve_screenshot(relative: str | None) -> Path | None:
    """Absolute path of a stored screenshot, or None if missing or outside STORAGE_DIR."""
    if not relative:
        return None
    root = storage_root()
    path = (root / relative).resolve()
    return path if path.is_relative_to(root) and path.is_file() else None


def screenshot_file(relative: str | None) -> FileResponse:
    path = resolve_screenshot(relative)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Screenshot not found")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


def sse(event: str, data: object, event_id: int | None = None) -> str:
    lines = [f"event: {event}"]
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"data: {json.dumps(data, default=str)}")
    return "\n".join(lines) + "\n\n"


@router.get("/runs/{run_id}/stream")
async def stream_run(
    run_id: str, request: Request, user: CurrentUser, after: int = Query(-1, ge=-1)
) -> StreamingResponse:
    """Server-Sent Events: `step` for each new run step (id = step index), `run` when the run changes,
    `test_case` when a test case is added or changes, and `end` once the run has finished and every step was sent. Resume with ?after=<last id>
    (or the Last-Event-ID header)."""
    run, project = await get_owned_run(user, run_id)
    last_event_id = request.headers.get("last-event-id")
    if last_event_id and last_event_id.lstrip("-").isdigit():
        after = max(after, int(last_event_id))

    async def events() -> AsyncIterator[str]:
        nonlocal run
        last_index = after
        last_run_state: tuple | None = None
        test_case_versions: dict[int, object] = {}
        idle = 0.0
        while True:
            current = await Run.get(run.id)
            if current is None:
                yield sse("end", {"status": "deleted"})
                return
            run = current
            run_state = (run.status, run.finished_at, repr(run.stats))
            if run_state != last_run_state:
                last_run_state = run_state
                yield sse("run", run_read(run, project).model_dump(mode="json"))
            for tc in await test_cases_of(run):
                if test_case_versions.get(tc.index) != tc.updated_at:
                    test_case_versions[tc.index] = tc.updated_at
                    yield sse("test_case", test_case_read(tc).model_dump(mode="json"))
            new_steps = await steps_after(run, last_index)
            for step in new_steps:
                last_index = step.index
                yield sse("step", step_read(step).model_dump(mode="json"), step.index)
            if run.status not in ACTIVE_RUN_STATUSES and not new_steps:
                # Re-check once so steps written just before the status change aren't missed.
                if not await steps_after(run, last_index):
                    yield sse("end", {"status": run.status})
                    return
                continue
            if await request.is_disconnected():
                return
            await asyncio.sleep(STREAM_POLL_SECONDS)
            idle = 0.0 if new_steps else idle + STREAM_POLL_SECONDS
            if idle >= STREAM_HEARTBEAT_SECONDS:
                idle = 0.0
                yield ": keep-alive\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.get("/runs/{run_id}/report.html", response_class=HTMLResponse)
async def download_report(run_id: str, user: CurrentUser) -> HTMLResponse:
    """The run report as one self-contained HTML file (screenshots embedded), sent as a download."""
    run, project = await get_owned_run(user, run_id)
    test_cases = await test_cases_of(run)
    steps = await RunStep.find(RunStep.run_id == run.id).sort(+RunStep.index).to_list()
    bugs = await Bug.find({"run_ids": run.id}).to_list()
    audit = await A11yAudit.find_one(A11yAudit.run_id == run.id)
    a11y = await A11yIssue.find(A11yIssue.run_id == run.id).to_list() if audit else []
    html = build_report_html(run, project, test_cases, steps, bugs, resolve_screenshot, audit, a11y)
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", project.name).strip("-")[:40] or "project"
    day = (run.started_at or run.created_at).strftime("%Y-%m-%d")
    filename = f"qa-pilot-report-{safe_name}-{day}.html"
    return HTMLResponse(html, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
