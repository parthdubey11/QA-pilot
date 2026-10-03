"""Export a saved test as a Playwright Test (TypeScript) spec that runs without QA Pilot.

Test logins are never written into the file: {{cred:Label:field}} placeholders become environment variables
(QA_CRED_<LABEL>_<FIELD>), and the base URL comes from QA_BASE_URL.
"""

import json
import re

from app.models.saved_test import Locators, SavedStep, SavedTest

PLACEHOLDER = re.compile(r"\{\{cred:([^:{}]+):(username|password)\}\}")


def env_name(label: str, field: str) -> str:
    return "QA_CRED_" + re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_").upper() + "_" + field.upper()


def js_comment(text: str) -> str:
    """Text safe inside a `//` comment. Notes and titles are written by the LLM (and can echo the target site's
    text), so a line break, including JS's U+2028/U+2029 line terminators, must not end the comment and inject code."""
    return re.sub(r"[\r\n  ]+", " ", text).strip()


def js_string(text: str) -> str:
    """A JS expression for `text`: a string literal, or a template literal reading env vars for credentials."""
    if not PLACEHOLDER.search(text):
        return json.dumps(text)
    parts, last = [], 0
    for match in PLACEHOLDER.finditer(text):
        literal = text[last:match.start()].replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
        parts.append(literal + "${process.env." + env_name(match.group(1), match.group(2)) + " ?? ''}")
        last = match.end()
    parts.append(text[last:].replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${"))
    return "`" + "".join(parts) + "`"


def locator_js(loc: Locators) -> str:
    if loc.role and loc.name:
        return f"page.getByRole({json.dumps(loc.role)}, {{ name: {json.dumps(loc.name)}, exact: true }})"
    if loc.css:
        return f"page.locator({json.dumps(loc.css)})"
    if loc.text:
        return f"page.getByText({json.dumps(loc.text)}, {{ exact: true }})"
    return f"page.mouse /* no stable locator: element was at ({loc.x}, {loc.y}) */"


def settle_js(step: SavedStep, current_path: str) -> str:
    """Wait for what the action set off, like the replayer does: the recorded next page if it navigated,
    otherwise network idle. Steps recorded before url_after existed get a short pause first."""
    if step.tool not in ("click", "press", "back"):
        return ""
    if step.url_after and step.url_after != current_path:
        return f"  await page.waitForURL((url) => url.pathname + url.search === {json.dumps(step.url_after)})\n"
    pause = "" if step.url_after else "  await page.waitForTimeout(500)\n"
    return pause + "  await page.waitForLoadState('networkidle')\n"


def step_js(step: SavedStep, current_path: str = "") -> str:
    comment = f"  // {js_comment(step.note)}\n" if step.note else ""
    if step.tool == "goto":
        line = f"await page.goto(BASE_URL + {json.dumps(step.url or '/')})"
    elif step.tool == "press":
        line = f"await page.keyboard.press({json.dumps(step.key or 'Enter')})"
    elif step.tool == "back":
        line = "await page.goBack()"
    elif step.tool == "scroll":
        line = f"await page.mouse.wheel(0, {600 if (step.direction or 'down') == 'down' else -600})"
    elif step.tool == "wait":
        line = f"await page.waitForTimeout({int(step.ms or 0)})"
    else:
        assert step.locators is not None
        target = locator_js(step.locators)
        if step.tool == "type":
            line = f"await {target}.fill({js_string(step.text or '')})"
        elif step.tool == "select":
            line = f"await {target}.selectOption({{ label: {json.dumps(step.value or '')} }})"
        else:
            line = f"await {target}.click()"
    return f"{comment}  {line}\n{settle_js(step, current_path)}"


def export_spec(saved: SavedTest, base_url: str) -> str:
    env_vars = sorted({env_name(m.group(1), m.group(2)) for s in saved.steps if s.text
                       for m in PLACEHOLDER.finditer(s.text)})
    header = [
        "// Exported from QA Pilot — run with: npx playwright test",
        f"// Saved test: {js_comment(saved.title)}",
        "// Set QA_BASE_URL to the site under test" + (f" and {', '.join(env_vars)} to the test login." if env_vars else "."),
        "import { test, expect } from '@playwright/test'",
        "",
        f"const BASE_URL = process.env.QA_BASE_URL ?? {json.dumps(base_url.rstrip('/'))}",
        "",
        f"test({json.dumps(saved.title)}, async ({{ page }}) => {{",
        f"  await page.goto(BASE_URL + {json.dumps(saved.start_path)})",
    ]
    body, current = "", saved.start_path
    for step in saved.steps:
        body += step_js(step, current)
        if step.tool == "goto" and step.url:
            current = step.url
        elif step.url_after:
            current = step.url_after
    checks = []
    for assertion in saved.assertions:
        if assertion.kind == "url":
            pattern = json.dumps(re.escape(assertion.value) + r"(\?|$)")  # the path, then a query string or the end
            checks.append(f"  await expect(page).toHaveURL(new RegExp({pattern}))\n")
        else:
            checks.append(f"  await expect(page.getByText({json.dumps(assertion.value)}).first()).toBeVisible()\n")
    return "\n".join(header) + "\n" + body + "".join(checks) + "})\n"
