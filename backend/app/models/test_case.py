from datetime import datetime
from typing import Annotated, Literal

from beanie import Document, Indexed, PydanticObjectId
from pydantic import Field

from app.models.base import utcnow

TestType = Literal["happy", "edge"]
TestStatus = Literal["pending", "running", "passed", "failed", "blocked", "error"]


class TestCase(Document):
    """A test case the planner wrote for a run. Its steps are plain English and embedded (small, owned data)."""

    __test__ = False  # not a pytest test class

    run_id: Annotated[PydanticObjectId, Indexed()]
    project_id: PydanticObjectId
    index: int
    title: str
    type: TestType
    start_path: str = "/"
    steps: list[str]
    expected: str
    status: TestStatus = "pending"
    reason: str | None = None
    steps_used: int = 0
    final_url: str | None = None
    final_screenshot_path: str | None = None  # what the judge saw, relative to STORAGE_DIR
    bug_id: PydanticObjectId | None = None  # the bug reported for this failed test
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "test_cases"
