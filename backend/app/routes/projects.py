"""Projects and their test-site credentials. Every route is scoped to the current user:
another user's project behaves exactly like a missing one (404), so ids don't leak."""

from fastapi import APIRouter, HTTPException, Response, status

from app.core.deps import CurrentUser, parse_object_id
from app.core.security import encrypt_secret
from app.models.base import utcnow
from app.models.accessibility import A11yAudit, A11yIssue
from app.models.bug import Bug
from app.models.job import Job
from app.models.project import Credential, Project
from app.models.run import ACTIVE_RUN_STATUSES, Run, RunStep
from app.models.saved_test import Notification, SavedTest, Schedule
from app.models.test_case import TestCase
from app.models.user import User
from app.schemas.projects import (
    CredentialCreate,
    CredentialRead,
    CredentialUpdate,
    ProjectCreate,
    ProjectRead,
    ProjectUpdate,
)

router = APIRouter(prefix="/projects", tags=["projects"])


async def get_owned_project(user: User, project_id: str) -> Project:
    oid = parse_object_id(project_id)
    project = await Project.get(oid) if oid else None
    if project is None or project.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


async def get_project_credential(project: Project, credential_id: str) -> Credential:
    oid = parse_object_id(credential_id)
    credential = await Credential.get(oid) if oid else None
    if credential is None or credential.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Credential not found")
    return credential


def _project_read(project: Project) -> ProjectRead:
    return ProjectRead.model_validate(project.model_dump(exclude={"id", "owner_id"}) | {"id": str(project.id)})


def _credential_read(credential: Credential) -> CredentialRead:
    return CredentialRead.model_validate(
        credential.model_dump(include={"label", "username", "created_at", "updated_at"}) | {"id": str(credential.id)}
    )


# ---------- projects ----------


@router.get("", response_model=list[ProjectRead])
async def list_projects(user: CurrentUser) -> list[ProjectRead]:
    projects = await Project.find(Project.owner_id == user.id).sort(-Project.created_at).to_list()
    return [_project_read(p) for p in projects]


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
async def create_project(body: ProjectCreate, user: CurrentUser) -> ProjectRead:
    assert user.id is not None
    project = Project(owner_id=user.id, name=body.name, base_url=str(body.base_url))
    await project.insert()
    return _project_read(project)


@router.get("/{project_id}", response_model=ProjectRead)
async def get_project(project_id: str, user: CurrentUser) -> ProjectRead:
    return _project_read(await get_owned_project(user, project_id))


@router.patch("/{project_id}", response_model=ProjectRead)
async def update_project(project_id: str, body: ProjectUpdate, user: CurrentUser) -> ProjectRead:
    project = await get_owned_project(user, project_id)
    if body.name is not None:
        project.name = body.name
    if body.base_url is not None:
        project.base_url = str(body.base_url)
    if body.is_own_site is not None:
        project.is_own_site = body.is_own_site
        if not project.is_own_site:
            project.security_probes_enabled = False
    if body.security_probes_enabled is not None:
        if body.security_probes_enabled and not project.is_own_site:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Security probes can only be enabled for a site you've marked as your own.",
            )
        project.security_probes_enabled = body.security_probes_enabled
    project.updated_at = utcnow()
    await project.save()
    return _project_read(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: str, user: CurrentUser) -> Response:
    project = await get_owned_project(user, project_id)
    runs = await Run.find(Run.project_id == project.id).to_list()
    if any(r.status in ACTIVE_RUN_STATUSES for r in runs):
        raise HTTPException(status.HTTP_409_CONFLICT, "A run is in progress. Wait for it to finish before deleting.")
    run_ids = [r.id for r in runs]
    await RunStep.find({"run_id": {"$in": run_ids}}).delete()
    await TestCase.find({"run_id": {"$in": run_ids}}).delete()
    await Bug.find(Bug.project_id == project.id).delete()
    await A11yIssue.find(A11yIssue.project_id == project.id).delete()
    await A11yAudit.find(A11yAudit.project_id == project.id).delete()
    await SavedTest.find(SavedTest.project_id == project.id).delete()
    await Schedule.find(Schedule.project_id == project.id).delete()
    await Notification.find(Notification.project_id == project.id).delete()
    await Job.find({"payload.run_id": {"$in": [str(i) for i in run_ids]}}).delete()
    await Run.find(Run.project_id == project.id).delete()
    await Credential.find(Credential.project_id == project.id).delete()
    await project.delete()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- credentials ----------


@router.get("/{project_id}/credentials", response_model=list[CredentialRead])
async def list_credentials(project_id: str, user: CurrentUser) -> list[CredentialRead]:
    project = await get_owned_project(user, project_id)
    credentials = await Credential.find(Credential.project_id == project.id).sort(+Credential.created_at).to_list()
    return [_credential_read(c) for c in credentials]


@router.post("/{project_id}/credentials", response_model=CredentialRead, status_code=status.HTTP_201_CREATED)
async def create_credential(project_id: str, body: CredentialCreate, user: CurrentUser) -> CredentialRead:
    project = await get_owned_project(user, project_id)
    assert project.id is not None
    credential = Credential(
        project_id=project.id,
        label=body.label,
        username=body.username,
        password_encrypted=encrypt_secret(body.password),
    )
    await credential.insert()
    return _credential_read(credential)


@router.patch("/{project_id}/credentials/{credential_id}", response_model=CredentialRead)
async def update_credential(
    project_id: str, credential_id: str, body: CredentialUpdate, user: CurrentUser
) -> CredentialRead:
    credential = await get_project_credential(await get_owned_project(user, project_id), credential_id)
    if body.label is not None:
        credential.label = body.label.strip() or credential.label
    if body.username is not None:
        credential.username = body.username
    if body.password is not None:
        credential.password_encrypted = encrypt_secret(body.password)
    credential.updated_at = utcnow()
    await credential.save()
    return _credential_read(credential)


@router.delete("/{project_id}/credentials/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credential(project_id: str, credential_id: str, user: CurrentUser) -> Response:
    credential = await get_project_credential(await get_owned_project(user, project_id), credential_id)
    await credential.delete()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
