"""Self-contained HTML run report: inline CSS, screenshots embedded as base64, no external requests.

Opens in any browser, can be emailed, and prints cleanly to PDF (browser "Save as PDF").
Every value from the database or the LLM is escaped.
"""

import base64
from collections.abc import Callable
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from app.models.accessibility import A11yAudit, A11yIssue
from app.models.bug import SEVERITY_ORDER, Bug
from app.models.project import Project
from app.models.run import Run, RunStep
from app.models.test_case import TestCase

STATUS_LABEL = {"passed": "Passed", "failed": "Failed", "blocked": "Blocked", "error": "Error",
                "pending": "Not run", "running": "Running"}

CSS = """
:root { --ink:#1f2937; --muted:#4b5563; --line:#e5e7eb; --bg:#f8fafc; --pass:#047857; --fail:#b91c1c;
        --block:#92400e; --err:#374151; }
* { box-sizing: border-box; }
body { margin: 0; font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; color: var(--ink);
       background: var(--bg); }
main { max-width: 960px; margin: 0 auto; padding: 32px 20px 64px; }
h1 { font-size: 26px; margin: 0 0 4px; } h2 { font-size: 19px; margin: 36px 0 12px; }
h3 { font-size: 16px; margin: 0; }
.muted { color: var(--muted); } .small { font-size: 13px; }
.card { background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 16px 18px; margin: 12px 0; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 10px; margin-top: 18px; }
.stat { background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; }
.stat b { display: block; font-size: 24px; }
.badge { display: inline-block; border-radius: 999px; padding: 1px 10px; font-size: 12px; font-weight: 600;
         border: 1px solid currentColor; white-space: nowrap; }
.passed { color: var(--pass); } .failed { color: var(--fail); } .blocked { color: var(--block); }
.error, .pending, .running { color: var(--err); }
.critical { color: #7f1d1d; } .high { color: var(--fail); } .medium { color: var(--block); } .low { color: var(--err); }
table { width: 100%; border-collapse: collapse; background: #fff; border: 1px solid var(--line); border-radius: 10px; }
th, td { text-align: left; padding: 9px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { font-size: 13px; color: var(--muted); }
.row { display: flex; gap: 12px; align-items: baseline; justify-content: space-between; flex-wrap: wrap; }
ol { margin: 6px 0; padding-left: 22px; }
img { max-width: 100%; border: 1px solid var(--line); border-radius: 6px; margin-top: 10px; }
details { margin-top: 10px; } summary { cursor: pointer; font-weight: 600; font-size: 14px; }
.log { font: 12.5px/1.5 ui-monospace, SFMono-Regular, Consolas, monospace; background: #0f172a; color: #e2e8f0;
       padding: 10px 12px; border-radius: 6px; white-space: pre-wrap; word-break: break-word; }
.kind { color: #93c5fd; }
footer { margin-top: 40px; font-size: 12px; color: var(--muted); }
@media print {
  body { background: #fff; } main { padding: 0; } .card, .stat, table { break-inside: avoid; }
  details { display: block; } details > summary { list-style: none; }
  details:not([open]) > *:not(summary) { display: block; }
}
"""


def _img(path: Path | None, alt: str) -> str:
    if path is None:
        return ""
    data = base64.b64encode(path.read_bytes()).decode()
    return f'<img src="data:image/png;base64,{data}" alt="{escape(alt)}">'


def _time(value: datetime | None) -> str:
    return value.strftime("%d %b %Y, %H:%M UTC") if value else "—"


def _duration(run: Run) -> str:
    seconds = run.stats.get("seconds")
    if seconds is None and run.started_at and run.finished_at:
        seconds = (run.finished_at - run.started_at).total_seconds()
    if seconds is None:
        return "—"
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes} min {secs} s" if minutes else f"{secs} s"


def build_report_html(
    run: Run,
    project: Project,
    test_cases: list[TestCase],
    steps: list[RunStep],
    bugs: list[Bug],
    resolve: Callable[[str | None], Path | None],
    audit: A11yAudit | None = None,
    a11y_issues: list[A11yIssue] | None = None,
) -> str:
    """resolve: maps a stored screenshot path to a file (or None), e.g. routes.runs.resolve_screenshot."""
    counts = {s: sum(tc.status == s for tc in test_cases) for s in ("passed", "failed", "blocked", "error")}
    not_run = f" · {counts['error']} test(s) could not run" if counts["error"] else ""
    tokens = run.stats.get("input_tokens", 0) + run.stats.get("output_tokens", 0)
    bugs = sorted(bugs, key=lambda b: SEVERITY_ORDER[b.severity])
    bug_by_id = {b.id: b for b in bugs}

    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>QA Pilot report — {escape(project.name)}</title><style>{CSS}</style></head><body><main>",
        f"<p class='muted small'>QA Pilot test report</p><h1>{escape(project.name)}</h1>",
        f"<p class='muted'>{escape(project.base_url)} · run {escape(str(run.id))}</p>",
        f"<div class='card'><b>Goal:</b> {escape(run.goal)}<br>",
        f"<span class='muted small'>Started {_time(run.started_at)} · duration {_duration(run)} · "
        f"status {escape(run.status)}{' · ' + escape(run.error) if run.error else ''}</span></div>",
        "<div class='stats'>",
        f"<div class='stat'><b>{len(test_cases)}</b>tests</div>",
        f"<div class='stat passed'><b>{counts['passed']}</b>passed</div>",
        f"<div class='stat failed'><b>{counts['failed']}</b>failed</div>",
        f"<div class='stat blocked'><b>{counts['blocked']}</b>blocked</div>",
        f"<div class='stat'><b>{len(bugs)}</b>bugs</div>",
        f"<div class='stat'><b>{run.stats.get('pages', '—')}</b>pages explored</div>",
        "</div>",
        f"<p class='muted small'>LLM: {run.stats.get('llm_calls', 0)} calls, {tokens:,} tokens{not_run}</p>",
    ]

    parts.append("<h2>Bugs found</h2>")
    if not bugs:
        parts.append("<p class='muted'>No bugs were reported in this run.</p>")
    for bug in bugs:
        seen = f" · seen {bug.occurrences}× across runs" if bug.occurrences > 1 else ""
        parts += [
            f"<div class='card' id='bug-{escape(str(bug.id))}'><div class='row'><h3>{escape(bug.title)}</h3>",
            f"<span><span class='badge {bug.severity}'>{escape(bug.severity)}</span> "
            f"<span class='badge'>{escape(bug.status)}</span></span></div>",
            f"<p class='muted small'>{escape(bug.url or '')}{seen}</p>",
            "<b>Steps to reproduce</b><ol>" + "".join(f"<li>{escape(s)}</li>" for s in bug.steps) + "</ol>",
            f"<p><b>Expected:</b> {escape(bug.expected)}<br><b>Actual:</b> {escape(bug.actual)}</p>",
            f"<p><b>Suggested fix:</b> {escape(bug.suggested_fix)}</p>",
            _img(resolve(bug.screenshot_path), f"Screenshot: {bug.title}"),
            "</div>",
        ]

    if audit is not None:
        parts += [f"<h2>Accessibility — {audit.score}/100</h2>",
                  "<table><thead><tr><th>Page</th><th>Score</th><th>Issues</th></tr></thead><tbody>"]
        for p in audit.pages:
            parts.append(f"<tr><td>{escape(p.path)}</td><td>{p.score if p.error is None else '—'}</td>"
                         f"<td>{escape(p.error) if p.error else p.issues}</td></tr>")
        parts.append("</tbody></table>")
        for issue in a11y_issues or []:
            wcag = ", ".join(issue.wcag)
            parts += [
                f"<div class='card'><div class='row'><h3>{escape(issue.title)}</h3>",
                f"<span class='badge'>{escape(issue.impact)}</span></div>",
                f"<p class='muted small'>{escape(issue.page_path)} · {escape(issue.rule_id)}"
                f"{' · WCAG ' + escape(wcag) if wcag else ''} · {len(issue.nodes)} element(s)</p>",
                f"<p>{escape(issue.description)}</p><p><b>How to fix:</b> {escape(issue.how_to_fix)}</p></div>",
            ]

    parts += ["<h2>Test cases</h2><table><thead><tr><th>#</th><th>Test</th><th>Type</th><th>Result</th></tr></thead><tbody>"]
    for tc in test_cases:
        parts.append(f"<tr><td>{tc.index + 1}</td><td>{escape(tc.title)}</td><td>{escape(tc.type)}</td>"
                     f"<td><span class='badge {tc.status}'>{STATUS_LABEL.get(tc.status, tc.status)}</span></td></tr>")
    parts.append("</tbody></table>")

    for tc in test_cases:
        log = [s for s in steps if s.test_case_index == tc.index]
        bug = bug_by_id.get(tc.bug_id) if tc.bug_id else None
        parts += [
            f"<div class='card'><div class='row'><h3>{tc.index + 1}. {escape(tc.title)}</h3>",
            f"<span class='badge {tc.status}'>{STATUS_LABEL.get(tc.status, tc.status)}</span></div>",
            "<ol>" + "".join(f"<li>{escape(s)}</li>" for s in tc.steps) + "</ol>",
            f"<p><b>Expected:</b> {escape(tc.expected)}</p>",
            f"<p><b>Verdict:</b> {escape(tc.reason or '—')}</p>",
        ]
        if bug:
            parts.append(f"<p><b>Bug:</b> <a href='#bug-{escape(str(bug.id))}'>{escape(bug.title)}</a></p>")
        parts.append(_img(resolve(tc.final_screenshot_path), f"Final screen of test {tc.index + 1}"))
        if log:
            lines = "\n".join(f"<span class='kind'>{escape(s.kind.upper())}</span> {escape(s.message)}" for s in log)
            parts.append(f"<details><summary>Agent log ({len(log)} steps)</summary><div class='log'>{lines}</div></details>")
        parts.append("</div>")

    parts.append(f"<footer>Generated by QA Pilot on {_time(datetime.now(UTC))}. "
                 "To save as PDF, open this file in a browser and use Print → Save as PDF.</footer>")
    parts.append("</main></body></html>")
    return "".join(parts)
