from datetime import datetime
from typing import Annotated, Literal

from beanie import Document, Indexed, PydanticObjectId
from pydantic import BaseModel, Field

from app.models.base import utcnow

Impact = Literal["critical", "serious", "moderate", "minor"]
IssueSource = Literal["axe", "keyboard", "vision"]


class IssueNode(BaseModel):
    target: str  # CSS selector or a readable description of the element
    html: str = ""  # short snippet of the element
    summary: str = ""  # what exactly is wrong with this element


class A11yIssue(Document):
    """One accessibility problem (a rule) on one page of one run, with the affected elements."""

    project_id: Annotated[PydanticObjectId, Indexed()]
    run_id: Annotated[PydanticObjectId, Indexed()]
    page_path: str
    page_url: str
    rule_id: str  # axe rule id (e.g. "image-alt") or keyboard-trap / focus-not-visible / unreachable-control / alt-text-quality
    source: IssueSource
    impact: Impact
    title: str
    description: str
    how_to_fix: str
    wcag: list[str] = Field(default_factory=list)  # success criteria, e.g. ["1.1.1"]
    help_url: str | None = None
    nodes: list[IssueNode] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "a11y_issues"


class A11yPageScore(BaseModel):
    path: str
    url: str
    title: str
    score: int
    issues: int
    same_as: list[str] = Field(default_factory=list)  # other pages with the same template (not audited again)
    error: str | None = None


class A11yAudit(Document):
    """The accessibility audit of one run: a WCAG score per page and for the whole site."""

    project_id: Annotated[PydanticObjectId, Indexed()]
    run_id: Annotated[PydanticObjectId, Indexed()]
    score: int
    pages: list[A11yPageScore]
    issue_count: int
    llm_checks: bool = True  # False if the keyboard/alt-text reviews ran without the LLM (quota, disabled…)
    created_at: Annotated[datetime, Indexed()] = Field(default_factory=utcnow)

    class Settings:
        name = "a11y_audits"
