from datetime import datetime

from pydantic import BaseModel

from app.models.bug import BugStatus, Severity


class BugRead(BaseModel):
    id: str
    project_id: str
    project_name: str
    title: str
    severity: Severity
    status: BugStatus
    steps: list[str]
    expected: str
    actual: str
    suggested_fix: str
    url: str | None
    has_screenshot: bool
    occurrences: int
    run_ids: list[str]
    first_run_id: str
    first_test_title: str
    reopened: bool
    first_seen_at: datetime
    last_seen_at: datetime
    updated_at: datetime


class BugUpdate(BaseModel):
    status: BugStatus
