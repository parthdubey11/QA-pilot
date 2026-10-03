from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.models.run import RunKind, RunOptions, RunStatus, RunTrigger, StepKind, StepPhase
from app.models.test_case import TestStatus, TestType


class RunCreate(BaseModel):
    goal: str = Field(min_length=1, max_length=2000)
    options: RunOptions = Field(default_factory=RunOptions)

    @field_validator("goal")
    @classmethod
    def goal_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Describe what to test")
        return value.strip()


class RunRead(BaseModel):
    id: str
    project_id: str
    project_name: str
    goal: str
    options: RunOptions
    kind: RunKind
    trigger: RunTrigger
    saved_test_ids: list[str]
    schedule_id: str | None
    status: RunStatus
    error: str | None
    stats: dict[str, Any]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class RunStepRead(BaseModel):
    index: int
    kind: StepKind
    phase: StepPhase | None
    test_case_index: int | None
    message: str
    action: dict[str, Any] | None
    url: str | None
    snapshot: str | None
    has_screenshot: bool
    created_at: datetime


class TestCaseRead(BaseModel):
    __test__ = False  # not a pytest test class

    index: int
    title: str
    type: TestType
    start_path: str
    steps: list[str]
    expected: str
    status: TestStatus
    reason: str | None
    steps_used: int
    final_url: str | None
    has_final_screenshot: bool
    bug_id: str | None
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime
