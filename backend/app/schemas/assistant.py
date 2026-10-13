from datetime import datetime

from pydantic import BaseModel, Field

from app.models.assistant import AssistantSource
from app.models.bug import BugStatus, Severity


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)


class AssistantAnswerRead(BaseModel):
    id: str
    question: str
    answer: str
    sources: list[AssistantSource]
    trace: list[str]
    llm_calls: int
    created_at: datetime


class SimilarBugRead(BaseModel):
    id: str
    title: str
    severity: Severity
    status: BugStatus
    occurrences: int
    last_seen_at: datetime
    similarity: float
