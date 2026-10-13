from datetime import datetime
from typing import Annotated

from beanie import Document, Indexed, PydanticObjectId
from pydantic import BaseModel, Field

from app.models.base import utcnow


class AssistantSource(BaseModel):
    """A document from the project's history the answer is based on."""

    n: int  # the [n] the answer cites
    kind: str  # bug | test | a11y | heal
    ref_id: str
    title: str
    run_id: str | None = None
    similarity: float
    excerpt: str


class AssistantQuestion(Document):
    """A question asked about a project's QA history, with the RAG answer and its sources."""

    project_id: Annotated[PydanticObjectId, Indexed()]
    user_id: Annotated[PydanticObjectId, Indexed()]
    question: str
    answer: str
    sources: list[AssistantSource] = Field(default_factory=list)
    trace: list[str] = Field(default_factory=list)  # what the agent graph did (retrieve, grade, rewrite…)
    llm_calls: int = 0
    created_at: Annotated[datetime, Indexed()] = Field(default_factory=utcnow)

    class Settings:
        name = "assistant_questions"
