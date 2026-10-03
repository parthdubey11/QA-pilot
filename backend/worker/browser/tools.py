"""The typed tool set agents use to drive the browser. An LLM picks one of these as validated JSON."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class Goto(BaseModel):
    tool: Literal["goto"] = "goto"
    url: str


class Click(BaseModel):
    tool: Literal["click"] = "click"
    ref: int


class Type(BaseModel):
    tool: Literal["type"] = "type"
    ref: int
    text: str


class Select(BaseModel):
    tool: Literal["select"] = "select"
    ref: int
    value: str  # option label or value


class Press(BaseModel):
    tool: Literal["press"] = "press"
    key: str  # e.g. "Enter", "Tab", "Escape", "Shift+Tab"


class Scroll(BaseModel):
    tool: Literal["scroll"] = "scroll"
    direction: Literal["down", "up"] = "down"


class Back(BaseModel):
    tool: Literal["back"] = "back"


class Wait(BaseModel):
    tool: Literal["wait"] = "wait"
    ms: int = Field(ge=0, le=10_000)


class TakeSnapshot(BaseModel):
    tool: Literal["snapshot"] = "snapshot"


class TakeScreenshot(BaseModel):
    tool: Literal["screenshot"] = "screenshot"


class Done(BaseModel):
    tool: Literal["done"] = "done"
    result: Literal["pass", "fail", "blocked"]
    reason: str


ToolCall = Annotated[
    Goto | Click | Type | Select | Press | Scroll | Back | Wait | TakeSnapshot | TakeScreenshot | Done,
    Field(discriminator="tool"),
]


class ToolResult(BaseModel):
    ok: bool
    message: str
    url: str | None = None


class ToolError(Exception):
    """A tool call that couldn't be carried out (unknown ref, navigation outside the project, timeout…)."""
