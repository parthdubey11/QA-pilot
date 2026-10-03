"""Healer: when a replayed step can't find its element, pick the matching element on the changed page."""

from pydantic import BaseModel, Field

from app.models.saved_test import SavedStep
from worker.agents import render_prompt
from worker.browser import Snapshot
from worker.llm import LLMProvider

COMPATIBLE_ROLES: dict[str, set[str]] = {
    "type": {"textbox", "searchbox", "combobox", "spinbutton"},
    "select": {"combobox", "listbox"},
}


class HealDecision(BaseModel):
    ref: int | None = Field(default=None, description="ref [N] of the matching element, or null")
    reason: str = Field(min_length=1, max_length=500)


async def find_replacement(
    llm: LLMProvider, *, test_title: str, step: SavedStep, previous: list[str], snapshot: Snapshot, snapshot_text: str
) -> HealDecision:
    """Ask the LLM which element replaces the lost one. A pick of an incompatible element (e.g. a link for a
    typing step) or an unknown ref is rejected (ref=None)."""
    assert step.locators is not None
    prompt = render_prompt(
        "healer",
        test_title=test_title,
        action=step.describe(),
        note=step.note or "(not recorded)",
        old_element=f"{step.locators.describe()} — <{step.locators.tag}>"
                    + (f", css {step.locators.css}" if step.locators.css else ""),
        previous="\n".join(f"- {p}" for p in previous[-8:]) or "(none: this is the first step)",
        snapshot=snapshot_text,
    )
    decision = await llm.generate_json(prompt, HealDecision)
    if decision.ref is None:
        return decision
    element = snapshot.find(decision.ref)
    if element is None:
        return HealDecision(ref=None, reason=f"The healer chose [{decision.ref}], which is not on the page.")
    allowed = COMPATIBLE_ROLES.get(step.tool)
    if allowed and element.role not in allowed:
        return HealDecision(ref=None, reason=f"The healer chose a {element.role}, which can't be used to {step.tool}.")
    return decision
