from datetime import datetime
from typing import Annotated, Literal

from beanie import Document, Indexed, PydanticObjectId
from pydantic import BaseModel, Field

from app.models.base import utcnow

HealMethod = Literal["fallback-locator", "ai-healer"]


class Locators(BaseModel):
    """Several independent ways to find the same element, tried in this order on replay."""

    role: str | None = None  # ARIA role, e.g. "button"
    name: str | None = None  # accessible name, e.g. "Add to cart"
    text: str | None = None  # visible text
    css: str | None = None  # e.g. "#add-to-cart" or input[name="email"]
    tag: str | None = None  # e.g. "button" (guards fallback matches)
    x: float | None = None  # centre of the element in page coordinates (position fallback)
    y: float | None = None

    def describe(self) -> str:
        if self.role and self.name:
            return f'{self.role} "{self.name}"'
        return self.text or self.css or f"<{self.tag or '?'}> at ({self.x}, {self.y})"


class SavedStep(BaseModel):
    tool: Literal["goto", "click", "type", "select", "press", "back", "scroll", "wait"]
    locators: Locators | None = None  # element steps only
    url: str | None = None  # goto
    text: str | None = None  # type (may contain {{cred:…}} placeholders)
    value: str | None = None  # select
    key: str | None = None  # press
    ms: int | None = None  # wait
    direction: str | None = None  # scroll
    note: str = ""  # what the step is for (the executor's thought when it was recorded)
    url_after: str | None = None  # path (+query) the page was on after the step; refreshed by every replay

    def describe(self) -> str:
        target = self.locators.describe() if self.locators else ""
        match self.tool:
            case "goto":
                return f"Open {self.url}"
            case "type":
                return f'Type "{self.text}" into {target}'
            case "select":
                return f'Select "{self.value}" in {target}'
            case "click":
                return f"Click {target}"
            case "press":
                return f"Press {self.key}"
            case "wait":
                return f"Wait {self.ms} ms"
            case "scroll":
                return f"Scroll {self.direction or 'down'}"
        return "Go back"


class Assertion(BaseModel):
    kind: Literal["text", "url"]
    value: str  # text that must be visible, or the path the page must end on


class HealEvent(BaseModel):
    at: datetime = Field(default_factory=utcnow)
    run_id: PydanticObjectId
    step_index: int
    method: HealMethod
    old: Locators
    new: Locators
    reason: str


class SavedTest(Document):
    """A test that passed, saved as replayable steps. Replays run without the LLM (except to heal)."""

    project_id: Annotated[PydanticObjectId, Indexed()]
    title: str
    title_key: Annotated[str, Indexed()]
    start_path: str
    steps: list[SavedStep]
    assertions: list[Assertion] = Field(default_factory=list)
    expected: str = ""
    source_run_id: PydanticObjectId
    last_result: Literal["passed", "failed", "never"] = "passed"  # the recording run passed
    last_run_id: PydanticObjectId | None = None
    last_run_at: datetime | None = None
    replays: int = 0
    heal_history: list[HealEvent] = Field(default_factory=list)  # newest last, capped
    created_at: Annotated[datetime, Indexed()] = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "saved_tests"


class Schedule(Document):
    """Cron schedule that replays a project's saved tests (APScheduler in the worker)."""

    project_id: Annotated[PydanticObjectId, Indexed()]
    name: str
    cron: str  # 5 fields: minute hour day-of-month month day-of-week
    timezone: str = "UTC"
    enabled: Annotated[bool, Indexed()] = True
    created_by: PydanticObjectId
    last_triggered_at: datetime | None = None
    last_run_id: PydanticObjectId | None = None
    last_skip_reason: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "schedules"


class Notification(Document):
    """In-app notification (no email): e.g. a scheduled run found new failures."""

    user_id: Annotated[PydanticObjectId, Indexed()]
    project_id: PydanticObjectId
    run_id: PydanticObjectId | None = None
    kind: Literal["new_failures", "run_failed"]
    title: str
    body: str
    read: Annotated[bool, Indexed()] = False
    created_at: Annotated[datetime, Indexed()] = Field(default_factory=utcnow)

    class Settings:
        name = "notifications"
