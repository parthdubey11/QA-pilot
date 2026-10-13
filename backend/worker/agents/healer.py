"""Healer: when a replayed step can't find its element, pick the matching element on the changed page.

Retrieval-augmented: the prompt includes the most similar earlier repairs on the same site (from the vector store),
so a rename that was already healed in one saved test (e.g. "Sign Up" -> "Create Account") guides the others."""

from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.models.saved_test import SavedStep
from app.rag import Hit, get_knowledge_base
from worker.agents import render_prompt
from worker.browser import Snapshot
from worker.llm import LLMProvider

COMPATIBLE_ROLES: dict[str, set[str]] = {
    "type": {"textbox", "searchbox", "combobox", "spinbutton"},
    "select": {"combobox", "listbox"},
}


MAX_PAST_REPAIRS = 3


async def past_repairs(project_id: object, step: SavedStep) -> list[Hit]:
    """Earlier repairs on this project most similar to the lost element (empty without the vector store)."""
    kb = get_knowledge_base()
    if kb is None or step.locators is None:
        return []
    query = f"the element {step.locators.describe()} was no longer found. {step.describe()}. {step.note}"
    try:
        return await kb.search(project_id, query, kinds=["heal"], k=MAX_PAST_REPAIRS,
                               min_similarity=get_settings().rag_min_similarity)
    except Exception:  # noqa: BLE001 - healing works without the hints
        return []


class HealDecision(BaseModel):
    ref: int | None = Field(default=None, description="ref [N] of the matching element, or null")
    reason: str = Field(min_length=1, max_length=500)


async def find_replacement(
    llm: LLMProvider,
    *,
    test_title: str,
    step: SavedStep,
    previous: list[str],
    snapshot: Snapshot,
    snapshot_text: str,
    repairs: list[Hit] | None = None,
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
        past_repairs="\n".join(f"- {r.text}" for r in repairs or []) or "(none)",
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
