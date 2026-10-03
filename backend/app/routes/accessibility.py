"""A project's accessibility audits (latest by default), with issues and score history."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.core.deps import CurrentUser, parse_object_id
from app.models.accessibility import A11yAudit, A11yIssue, A11yPageScore, Impact, IssueNode, IssueSource
from app.routes.projects import get_owned_project

router = APIRouter(tags=["accessibility"])

IMPACT_ORDER = {"critical": 0, "serious": 1, "moderate": 2, "minor": 3}


class A11yIssueRead(BaseModel):
    id: str
    page_path: str
    page_url: str
    rule_id: str
    source: IssueSource
    impact: Impact
    title: str
    description: str
    how_to_fix: str
    wcag: list[str]
    help_url: str | None
    nodes: list[IssueNode]


class A11yAuditRead(BaseModel):
    run_id: str
    score: int
    pages: list[A11yPageScore]
    issue_count: int
    llm_checks: bool
    created_at: datetime


class A11yHistoryPoint(BaseModel):
    run_id: str
    score: int
    issue_count: int
    created_at: datetime


class ProjectAccessibility(BaseModel):
    audit: A11yAuditRead | None
    issues: list[A11yIssueRead]
    history: list[A11yHistoryPoint]  # oldest first


@router.get("/projects/{project_id}/accessibility", response_model=ProjectAccessibility)
async def project_accessibility(project_id: str, user: CurrentUser, run_id: str | None = None) -> ProjectAccessibility:
    project = await get_owned_project(user, project_id)
    audits = await A11yAudit.find(A11yAudit.project_id == project.id).sort(-A11yAudit.created_at).limit(20).to_list()
    history = [A11yHistoryPoint(run_id=str(a.run_id), score=a.score, issue_count=a.issue_count, created_at=a.created_at)
               for a in reversed(audits)]
    audit = audits[0] if audits else None
    if run_id is not None:
        oid = parse_object_id(run_id)
        audit = await A11yAudit.find_one(A11yAudit.project_id == project.id, A11yAudit.run_id == oid) if oid else None
        if audit is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No accessibility audit for this run")
    if audit is None:
        return ProjectAccessibility(audit=None, issues=[], history=history)
    issues = await A11yIssue.find(A11yIssue.run_id == audit.run_id).to_list()
    issues.sort(key=lambda i: (IMPACT_ORDER[i.impact], i.rule_id, i.page_path))
    return ProjectAccessibility(
        audit=A11yAuditRead.model_validate(audit.model_dump(exclude={"id", "project_id", "run_id"}) | {"run_id": str(audit.run_id)}),
        issues=[A11yIssueRead.model_validate(i.model_dump(exclude={"id", "project_id", "run_id", "created_at"}) | {"id": str(i.id)})
                for i in issues],
        history=history,
    )
