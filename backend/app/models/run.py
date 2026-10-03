from datetime import datetime
from typing import Annotated, Any, Literal

import pymongo
from beanie import Document, Indexed, PydanticObjectId
from pydantic import BaseModel, Field
from pymongo import IndexModel

from app.models.base import utcnow

RunStatus = Literal["queued", "running", "completed", "failed"]
ACTIVE_RUN_STATUSES: tuple[RunStatus, ...] = ("queued", "running")
StepKind = Literal["info", "thought", "action", "observation", "error"]
StepPhase = Literal["explore", "audit", "plan", "execute", "judge", "report", "replay", "heal"]
RunKind = Literal["agent", "replay"]  # agent = explore/plan/execute with the LLM; replay = saved tests, no LLM
RunTrigger = Literal["manual", "schedule"]


class RunOptions(BaseModel):
    accessibility: bool = True
    mobile_viewport: bool = False
    max_tests: int = Field(default=15, ge=1, le=15)


class Run(Document):
    project_id: Annotated[PydanticObjectId, Indexed()]
    created_by: PydanticObjectId
    goal: str
    options: RunOptions = Field(default_factory=RunOptions)
    kind: RunKind = "agent"
    trigger: RunTrigger = "manual"
    saved_test_ids: list[PydanticObjectId] = Field(default_factory=list)  # replay runs
    schedule_id: PydanticObjectId | None = None
    status: Annotated[RunStatus, Indexed()] = "queued"
    error: str | None = None
    stats: dict[str, Any] = Field(default_factory=dict)
    created_at: Annotated[datetime, Indexed()] = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    class Settings:
        name = "runs"


class RunStep(Document):
    """One agent thought / action / observation. Kept in its own collection: runs can have hundreds."""

    run_id: PydanticObjectId
    index: int  # 0, 1, 2… within the run
    kind: StepKind
    phase: StepPhase | None = None
    test_case_index: int | None = None  # which test case this step belongs to (execute/judge phases)
    message: str
    action: dict[str, Any] | None = None  # the tool call, e.g. {"tool": "goto", "url": "…"}
    url: str | None = None
    snapshot: str | None = None  # compact accessibility-tree text
    screenshot_path: str | None = None  # relative to STORAGE_DIR
    created_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "run_steps"
        indexes = [IndexModel([("run_id", pymongo.ASCENDING), ("index", pymongo.ASCENDING)], unique=True)]
