"""Bugs found by the Reporter, across all of the user's projects."""

from beanie import PydanticObjectId
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse

from app.core.deps import CurrentUser, parse_object_id
from app.models.base import utcnow
from app.models.bug import SEVERITY_ORDER, Bug, BugStatus, Severity
from app.models.project import Project
from app.models.user import User
from app.routes.runs import screenshot_file
from app.schemas.bugs import BugRead, BugUpdate

router = APIRouter(prefix="/bugs", tags=["bugs"])


def bug_read(bug: Bug, project_name: str) -> BugRead:
    return BugRead.model_validate(
        bug.model_dump(exclude={"id", "project_id", "run_ids", "first_run_id", "title_key", "screenshot_path"})
        | {
            "id": str(bug.id),
            "project_id": str(bug.project_id),
            "project_name": project_name,
            "run_ids": [str(r) for r in bug.run_ids],
            "first_run_id": str(bug.first_run_id),
            "has_screenshot": bool(bug.screenshot_path),
        }
    )


async def owned_projects(user: User) -> dict[PydanticObjectId, str]:
    projects = await Project.find(Project.owner_id == user.id).to_list()
    return {p.id: p.name for p in projects if p.id is not None}


async def get_owned_bug(user: User, bug_id: str) -> tuple[Bug, str]:
    oid = parse_object_id(bug_id)
    bug = await Bug.get(oid) if oid else None
    project = await Project.get(bug.project_id) if bug else None
    if bug is None or project is None or project.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bug not found")
    return bug, project.name


@router.get("", response_model=list[BugRead])
async def list_bugs(
    user: CurrentUser,
    project_id: str | None = None,
    run_id: str | None = None,
    severity: list[Severity] | None = Query(None),
    status_: list[BugStatus] | None = Query(None, alias="status"),
) -> list[BugRead]:
    """Most severe first, then most recently seen. Filters: project_id, run_id, severity=…&severity=…, status=…"""
    projects = await owned_projects(user)
    project_ids = list(projects)
    if project_id is not None:
        oid = parse_object_id(project_id)
        if oid not in projects:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
        project_ids = [oid]
    query: dict = {"project_id": {"$in": project_ids}}
    if run_id is not None:
        query["run_ids"] = parse_object_id(run_id)
    if severity:
        query["severity"] = {"$in": severity}
    if status_:
        query["status"] = {"$in": status_}
    bugs = await Bug.find(query).sort(-Bug.last_seen_at).to_list()
    bugs.sort(key=lambda b: SEVERITY_ORDER[b.severity])  # stable: keeps recency within a severity
    return [bug_read(b, projects[b.project_id]) for b in bugs]


@router.get("/{bug_id}", response_model=BugRead)
async def get_bug(bug_id: str, user: CurrentUser) -> BugRead:
    bug, project_name = await get_owned_bug(user, bug_id)
    return bug_read(bug, project_name)


@router.patch("/{bug_id}", response_model=BugRead)
async def update_bug(bug_id: str, body: BugUpdate, user: CurrentUser) -> BugRead:
    """Mark a bug open, fixed or ignored."""
    bug, project_name = await get_owned_bug(user, bug_id)
    bug.status = body.status
    if body.status != "open":
        bug.reopened = False
    bug.updated_at = utcnow()
    await bug.save()
    return bug_read(bug, project_name)


@router.get("/{bug_id}/screenshot", response_class=FileResponse)
async def bug_screenshot(bug_id: str, user: CurrentUser) -> FileResponse:
    bug, _ = await get_owned_bug(user, bug_id)
    return screenshot_file(bug.screenshot_path)
