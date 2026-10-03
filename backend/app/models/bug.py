from datetime import datetime
from typing import Annotated, Literal

from beanie import Document, Indexed, PydanticObjectId
from pydantic import Field

from app.models.base import utcnow

Severity = Literal["critical", "high", "medium", "low"]
BugStatus = Literal["open", "fixed", "ignored"]
SEVERITY_ORDER: dict[str, int] = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def title_key(title: str) -> str:
    """Normalised title used as a cheap exact-duplicate check (case, punctuation and spacing ignored)."""
    return " ".join("".join(c.lower() if c.isalnum() else " " for c in title).split())


class Bug(Document):
    """A bug found by the Reporter. The same bug seen again (in any run) increments `occurrences`."""

    project_id: Annotated[PydanticObjectId, Indexed()]
    title: str
    title_key: Annotated[str, Indexed()]
    severity: Severity
    steps: list[str]
    expected: str
    actual: str
    suggested_fix: str
    url: str | None = None
    screenshot_path: str | None = None  # latest occurrence, relative to STORAGE_DIR
    status: Annotated[BugStatus, Indexed()] = "open"
    occurrences: int = 1
    run_ids: list[PydanticObjectId] = Field(default_factory=list)  # every run it was seen in, oldest first
    first_run_id: PydanticObjectId
    first_test_title: str
    reopened: bool = False  # was "fixed" and then seen again (regression)
    first_seen_at: datetime = Field(default_factory=utcnow)
    last_seen_at: datetime = Field(default_factory=utcnow)
    created_at: Annotated[datetime, Indexed()] = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "bugs"
