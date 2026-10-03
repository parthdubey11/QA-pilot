"""Agents (explorer, planner, executor, judge; later reporter, healer). Prompts live in prompts/*.md."""

import re
from functools import cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "prompts"
_FIELD = re.compile(r"\{(\w+)\}")


@cache
def load_prompt(name: str) -> str:
    """Return the text of prompts/<name>.md."""
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


def render_prompt(name: str, **values: object) -> str:
    """Fill {field} slots in prompts/<name>.md. Other braces (JSON, {{cred:…}} placeholders) are left alone."""
    return _FIELD.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), load_prompt(name))
