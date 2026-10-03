"""Starting runs (used by the API and by the worker's scheduler)."""

from datetime import datetime, time, timezone

from beanie import PydanticObjectId

from app.core.config import get_settings
from app.models.job import Job
from app.models.project import Project
from app.models.run import ACTIVE_RUN_STATUSES, Run, RunKind, RunOptions, RunTrigger


class ActiveRunError(Exception):
    """The project already has a queued or running run (one run per project at a time)."""


class RunQuotaError(Exception):
    """A daily limit on AI runs (per user or for the whole server) is reached."""


async def check_quota(created_by: PydanticObjectId) -> None:
    """Daily caps on AI runs, for public deployments where the LLM key is a shared free quota."""
    settings = get_settings()
    day_start = datetime.combine(datetime.now(timezone.utc).date(), time.min, tzinfo=timezone.utc)
    today = {"kind": "agent", "created_at": {"$gte": day_start}}
    if settings.max_agent_runs_per_user_per_day and await Run.find(today | {"created_by": created_by}).count()             >= settings.max_agent_runs_per_user_per_day:
        raise RunQuotaError(f"You've used today's {settings.max_agent_runs_per_user_per_day} AI runs. "
                            "Replays of saved tests still work; new AI runs are possible again tomorrow (UTC).")
    if settings.max_agent_runs_per_day and await Run.find(today).count() >= settings.max_agent_runs_per_day:
        raise RunQuotaError("This server has used today's AI run budget (a free LLM quota). "
                            "Replays of saved tests still work; please try a new AI run tomorrow (UTC).")


async def start_run(
    project: Project,
    *,
    created_by: PydanticObjectId,
    goal: str,
    options: RunOptions | None = None,
    kind: RunKind = "agent",
    trigger: RunTrigger = "manual",
    saved_test_ids: list[PydanticObjectId] | None = None,
    schedule_id: PydanticObjectId | None = None,
) -> Run:
    """Create a run and the job the worker will claim."""
    assert project.id is not None
    active = await Run.find(Run.project_id == project.id, {"status": {"$in": list(ACTIVE_RUN_STATUSES)}}).first_or_none()
    if active is not None:
        raise ActiveRunError("This project already has a run in progress. Wait for it to finish.")
    options = options or RunOptions()
    if kind == "agent":
        await check_quota(created_by)
        options = options.model_copy(update={"max_tests": min(options.max_tests, get_settings().max_tests_limit)})
    run = Run(project_id=project.id, created_by=created_by, goal=goal, options=options, kind=kind,
              trigger=trigger, saved_test_ids=saved_test_ids or [], schedule_id=schedule_id)
    await run.insert()
    await Job(type="run", payload={"run_id": str(run.id)}).insert()
    return run
