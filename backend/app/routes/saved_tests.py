"""Saved tests (replayable), replays, schedules and in-app notifications."""

import re
from datetime import UTC, datetime

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, HTTPException, Response, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, field_validator

from app.core.deps import CurrentUser, parse_object_id
from app.models.base import utcnow
from app.models.project import Project
from app.models.saved_test import Assertion, HealEvent, Notification, SavedStep, SavedTest, Schedule
from app.models.user import User
from app.routes.projects import get_owned_project
from app.routes.runs import run_read
from app.schemas.runs import RunRead
from app.services.playwright_export import export_spec
from app.services.runs import ActiveRunError, start_run

router = APIRouter(tags=["saved tests"])


# ---------- saved tests ----------


class SavedTestRead(BaseModel):
    id: str
    project_id: str
    title: str
    start_path: str
    steps: list[SavedStep]
    assertions: list[Assertion]
    expected: str
    source_run_id: str
    last_result: str
    last_run_id: str | None
    last_run_at: datetime | None
    replays: int
    heal_history: list[HealEvent]
    created_at: datetime
    updated_at: datetime


def saved_read(saved: SavedTest) -> SavedTestRead:
    return SavedTestRead.model_validate(
        saved.model_dump(exclude={"id", "project_id", "source_run_id", "last_run_id", "title_key"})
        | {"id": str(saved.id), "project_id": str(saved.project_id), "source_run_id": str(saved.source_run_id),
           "last_run_id": str(saved.last_run_id) if saved.last_run_id else None}
    )


async def get_owned_saved_test(user: User, saved_id: str) -> tuple[SavedTest, Project]:
    oid = parse_object_id(saved_id)
    saved = await SavedTest.get(oid) if oid else None
    project = await Project.get(saved.project_id) if saved else None
    if saved is None or project is None or project.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Saved test not found")
    return saved, project


@router.get("/projects/{project_id}/saved-tests", response_model=list[SavedTestRead])
async def list_saved_tests(project_id: str, user: CurrentUser) -> list[SavedTestRead]:
    project = await get_owned_project(user, project_id)
    tests = await SavedTest.find(SavedTest.project_id == project.id).sort(+SavedTest.created_at).to_list()
    return [saved_read(t) for t in tests]


@router.get("/saved-tests/{saved_id}", response_model=SavedTestRead)
async def get_saved_test(saved_id: str, user: CurrentUser) -> SavedTestRead:
    return saved_read((await get_owned_saved_test(user, saved_id))[0])


@router.delete("/saved-tests/{saved_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_saved_test(saved_id: str, user: CurrentUser) -> Response:
    saved, _ = await get_owned_saved_test(user, saved_id)
    await saved.delete()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/saved-tests/{saved_id}/export.spec.ts", response_class=PlainTextResponse)
async def export_saved_test(saved_id: str, user: CurrentUser) -> PlainTextResponse:
    """The saved test as a Playwright Test (TypeScript) file, sent as a download."""
    saved, project = await get_owned_saved_test(user, saved_id)
    slug = re.sub(r"[^a-z0-9]+", "-", saved.title.lower()).strip("-")[:50] or "saved-test"
    return PlainTextResponse(
        export_spec(saved, project.base_url), media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{slug}.spec.ts"'},
    )


class ReplayCreate(BaseModel):
    saved_test_ids: list[str] = Field(default_factory=list)  # empty = all of the project's saved tests


@router.post("/projects/{project_id}/replays", response_model=RunRead, status_code=status.HTTP_201_CREATED)
async def replay_saved_tests(project_id: str, body: ReplayCreate, user: CurrentUser) -> RunRead:
    """Replay saved tests now (no LLM unless a step needs healing)."""
    project = await get_owned_project(user, project_id)
    tests = await SavedTest.find(SavedTest.project_id == project.id).to_list()
    by_id = {str(t.id): t for t in tests}
    chosen = [by_id[i] for i in body.saved_test_ids if i in by_id] if body.saved_test_ids else tests
    if body.saved_test_ids and len(chosen) != len(set(body.saved_test_ids)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Saved test not found")
    if not chosen:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "This project has no saved tests yet.")
    assert user.id is not None
    try:
        run = await start_run(project, created_by=user.id, kind="replay",
                              goal=f"Replay {len(chosen)} saved test{'s' if len(chosen) != 1 else ''}",
                              saved_test_ids=[t.id for t in chosen if t.id is not None])
    except ActiveRunError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return run_read(run, project)


# ---------- schedules ----------


def validate_cron(cron: str, timezone: str) -> CronTrigger:
    try:
        return CronTrigger.from_crontab(cron.strip(), timezone=timezone)
    except (ValueError, KeyError) as exc:
        raise ValueError(f"Invalid schedule: {exc}") from None


class ScheduleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    cron: str = Field(min_length=9, max_length=100)
    timezone: str = "UTC"
    enabled: bool = True

    @field_validator("cron")
    @classmethod
    def five_fields(cls, value: str) -> str:
        if len(value.split()) != 5:
            raise ValueError("Use 5 fields: minute hour day-of-month month day-of-week, e.g. 0 9 * * 1-5")
        return " ".join(value.split())


class ScheduleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    cron: str | None = None
    timezone: str | None = None
    enabled: bool | None = None


class ScheduleRead(BaseModel):
    id: str
    project_id: str
    name: str
    cron: str
    timezone: str
    enabled: bool
    next_run_at: datetime | None
    last_triggered_at: datetime | None
    last_run_id: str | None
    last_skip_reason: str | None
    created_at: datetime


def schedule_read(schedule: Schedule) -> ScheduleRead:
    next_run = None
    if schedule.enabled:
        next_run = validate_cron(schedule.cron, schedule.timezone).get_next_fire_time(None, datetime.now(UTC))
    return ScheduleRead.model_validate(
        schedule.model_dump(exclude={"id", "project_id", "created_by", "last_run_id", "updated_at"})
        | {"id": str(schedule.id), "project_id": str(schedule.project_id), "next_run_at": next_run,
           "last_run_id": str(schedule.last_run_id) if schedule.last_run_id else None}
    )


async def get_owned_schedule(user: User, schedule_id: str) -> Schedule:
    oid = parse_object_id(schedule_id)
    schedule = await Schedule.get(oid) if oid else None
    project = await Project.get(schedule.project_id) if schedule else None
    if schedule is None or project is None or project.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Schedule not found")
    return schedule


def check_cron(cron: str, timezone: str) -> None:
    try:
        validate_cron(cron, timezone)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None


@router.get("/projects/{project_id}/schedules", response_model=list[ScheduleRead])
async def list_schedules(project_id: str, user: CurrentUser) -> list[ScheduleRead]:
    project = await get_owned_project(user, project_id)
    return [schedule_read(s) for s in await Schedule.find(Schedule.project_id == project.id).sort(+Schedule.created_at).to_list()]


@router.post("/projects/{project_id}/schedules", response_model=ScheduleRead, status_code=status.HTTP_201_CREATED)
async def create_schedule(project_id: str, body: ScheduleCreate, user: CurrentUser) -> ScheduleRead:
    project = await get_owned_project(user, project_id)
    check_cron(body.cron, body.timezone)
    assert project.id is not None and user.id is not None
    schedule = Schedule(project_id=project.id, name=body.name.strip(), cron=body.cron, timezone=body.timezone,
                        enabled=body.enabled, created_by=user.id)
    await schedule.insert()
    return schedule_read(schedule)


@router.patch("/schedules/{schedule_id}", response_model=ScheduleRead)
async def update_schedule(schedule_id: str, body: ScheduleUpdate, user: CurrentUser) -> ScheduleRead:
    schedule = await get_owned_schedule(user, schedule_id)
    cron = " ".join(body.cron.split()) if body.cron is not None else schedule.cron
    timezone = body.timezone or schedule.timezone
    if len(cron.split()) != 5:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Use 5 cron fields, e.g. 0 9 * * 1-5")
    check_cron(cron, timezone)
    schedule.cron, schedule.timezone = cron, timezone
    if body.name is not None:
        schedule.name = body.name.strip()
    if body.enabled is not None:
        schedule.enabled = body.enabled
    schedule.updated_at = utcnow()
    await schedule.save()
    return schedule_read(schedule)


@router.delete("/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(schedule_id: str, user: CurrentUser) -> Response:
    await (await get_owned_schedule(user, schedule_id)).delete()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- notifications ----------


class NotificationRead(BaseModel):
    id: str
    project_id: str
    run_id: str | None
    kind: str
    title: str
    body: str
    read: bool
    created_at: datetime


class NotificationList(BaseModel):
    unread: int
    items: list[NotificationRead]


@router.get("/notifications", response_model=NotificationList)
async def list_notifications(user: CurrentUser) -> NotificationList:
    items = await Notification.find(Notification.user_id == user.id).sort(-Notification.created_at).limit(50).to_list()
    unread = await Notification.find(Notification.user_id == user.id, Notification.read == False).count()  # noqa: E712
    return NotificationList(unread=unread, items=[NotificationRead.model_validate(
        n.model_dump(exclude={"id", "user_id", "project_id", "run_id"})
        | {"id": str(n.id), "project_id": str(n.project_id), "run_id": str(n.run_id) if n.run_id else None}
    ) for n in items])


@router.post("/notifications/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(notification_id: str, user: CurrentUser) -> Response:
    oid = parse_object_id(notification_id)
    notification = await Notification.get(oid) if oid else None
    if notification is None or notification.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found")
    notification.read = True
    await notification.save()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/notifications/read-all", status_code=status.HTTP_204_NO_CONTENT)
async def mark_all_read(user: CurrentUser) -> Response:
    await Notification.find(Notification.user_id == user.id, Notification.read == False).update(  # noqa: E712
        {"$set": {"read": True}})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
