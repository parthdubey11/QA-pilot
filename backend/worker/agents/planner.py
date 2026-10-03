"""Planner: turns the user's goal + the site map into test cases (happy paths and edge cases)."""

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from worker.agents import render_prompt
from worker.llm import LLMProvider

SECURITY_OFF = "Do NOT use security attack strings (XSS, SQL injection or similar); this site isn't enabled for them."
SECURITY_ON = ("The owner enabled security probes for this site: you may include 1-2 edge cases that type harmless "
               "XSS/SQL-injection test strings into inputs and check they are shown as plain text or rejected.")


class PlannedTest(BaseModel):
    title: str = Field(min_length=1, max_length=150)
    type: Literal["happy", "edge"]
    start_path: str = "/"
    steps: list[str] = Field(min_length=1, max_length=15)
    expected: str = Field(min_length=1)

    @field_validator("start_path")
    @classmethod
    def as_path(cls, value: str) -> str:
        """Keep only the path (the executor can't leave the project's site anyway)."""
        parts = urlsplit(value.strip())
        path = parts.path or "/"
        path = path if path.startswith("/") else "/" + path
        return path + (f"?{parts.query}" if parts.query else "")


class TestPlan(BaseModel):
    __test__ = False  # not a pytest test class

    test_cases: list[PlannedTest] = Field(min_length=1)


async def plan_tests(
    llm: LLMProvider,
    *,
    goal: str,
    site_map_text: str,
    max_tests: int,
    credential_placeholders: list[str],
    security_probes: bool = False,
) -> list[PlannedTest]:
    credentials = (
        "\n".join(f"- {p}" for p in credential_placeholders) if credential_placeholders else "(none configured)"
    )
    prompt = render_prompt(
        "planner",
        goal=goal,
        site_map=site_map_text,
        credentials=credentials,
        max_tests=max_tests,
        security_rule=SECURITY_ON if security_probes else SECURITY_OFF,
    )
    plan = await llm.generate_json(prompt, TestPlan)
    return plan.test_cases[:max_tests]
