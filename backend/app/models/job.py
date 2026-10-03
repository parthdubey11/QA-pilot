from datetime import datetime
from typing import Any, Literal

import pymongo
from beanie import Document
from pydantic import Field
from pymongo import IndexModel

from app.models.base import utcnow

JobStatus = Literal["pending", "running", "done", "failed"]


class Job(Document):
    """Work for the worker process. Claimed atomically (see worker.main.claim_next_job)."""

    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    status: JobStatus = "pending"
    attempts: int = 0
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    class Settings:
        name = "jobs"
        indexes = [IndexModel([("status", pymongo.ASCENDING), ("created_at", pymongo.ASCENDING)])]
