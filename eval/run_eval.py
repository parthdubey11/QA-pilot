"""Benchmark QA Pilot against demo-shop (see demo-shop/GROUND_TRUTH.md).

Needs the stack running (`docker compose up -d`). Each run: reset demo-shop, create a fresh project (so bug
deduplication across runs can't hide findings), start an agent run with the accessibility audit, wait for it,
then score the bugs and accessibility issues it reported against the ground truth.

    python eval/run_eval.py                      # 3 runs, results in eval/results/
    python eval/run_eval.py --runs 1 --max-tests 6
    python eval/run_eval.py --rescore eval/results/eval-20261001-1200.json   # re-score saved raw data, no runs

Groq's free tier allows ~200k tokens/day, roughly 3-4 full runs; plan the budget before starting.
"""

import argparse
import json
import os
import secrets
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

from matching import ACCESSIBILITY, FUNCTIONAL, Score, score_a11y, score_bugs

RESULTS = Path(__file__).parent / "results"
DEFAULT_GOAL = ("test signup, login and logout, product list sorting, product pages, adding to cart, "
                "changing quantities and removing items, checkout, and the contact form")
SEED_LOGIN = ("Test shopper", "demo@shop.test", "demo1234")  # demo-shop's seed account (GROUND_TRUTH.md)


class Api:
    def __init__(self, base: str) -> None:
        self.http = httpx.Client(base_url=base, timeout=30)

    def register(self) -> None:
        email = f"eval-{int(time.time())}-{secrets.token_hex(3)}@example.com"
        r = self.http.post("/auth/register", json={"name": "Eval", "email": email, "password": secrets.token_urlsafe(18)})
        r.raise_for_status()
        self.http.headers["Authorization"] = f"Bearer {r.json()['access_token']}"

    def get(self, path: str, **params: object) -> object:
        r = self.http.get(path, params=params)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, body: dict) -> dict:
        r = self.http.post(path, json=body)
        r.raise_for_status()
        return r.json()


def one_run(api: Api, n: int, args: argparse.Namespace) -> dict:
    httpx.post(f"{args.shop}/reset", timeout=10,
               headers={"X-Reset-Token": os.getenv("DEMO_SHOP_RESET_TOKEN", "")}).raise_for_status()
    project = api.post("/projects", {"name": f"Eval {datetime.now():%Y-%m-%d %H:%M} #{n}", "base_url": args.target,
                                     "authorised_testing_confirmed": True})
    label, username, password = SEED_LOGIN
    api.post(f"/projects/{project['id']}/credentials", {"label": label, "username": username, "password": password})
    run = api.post(f"/projects/{project['id']}/runs", {"goal": args.goal, "options": {
        "accessibility": True, "mobile_viewport": False, "max_tests": args.max_tests}})
    print(f"run {n}: {run['id']} started", flush=True)
    deadline = time.monotonic() + args.timeout * 60
    while run["status"] in ("queued", "running"):
        if time.monotonic() > deadline:
            sys.exit(f"run {run['id']} did not finish within {args.timeout} minutes")
        time.sleep(10)
        run = api.get(f"/runs/{run['id']}")  # type: ignore[assignment]
    stats = run["stats"]
    print(f"run {n}: {run['status']} in {stats.get('seconds')}s, {stats.get('tests')} tests, "
          f"{stats.get('bugs_new', 0)} bugs, a11y {stats.get('a11y_score')}", flush=True)
    bugs = api.get("/bugs", run_id=run["id"])
    a11y = api.get(f"/projects/{project['id']}/accessibility", run_id=run["id"])
    cases = api.get(f"/runs/{run['id']}/test-cases")
    return {
        "run_id": run["id"], "project_id": project["id"], "status": run["status"], "error": run.get("error"),
        "stats": stats,
        "test_cases": [{k: c.get(k) for k in ("title", "type", "status", "reason")} for c in cases],  # type: ignore[union-attr]
        "bugs": [{k: b.get(k) for k in ("title", "severity", "steps", "expected", "actual")} for b in bugs],  # type: ignore[union-attr]
        "a11y_issues": [{k: i.get(k) for k in ("rule_id", "source", "impact", "page_path", "title")}
                        for i in a11y["issues"]],  # type: ignore[index]
    }


def pct(x: float | None) -> str:
    return "—" if x is None else f"{100 * x:.0f}%"


def score_run(raw: dict) -> dict:
    bugs, annotated_bugs = score_bugs(raw["bugs"])
    findings = [(i["rule_id"], i["page_path"]) for i in raw["a11y_issues"]]
    qa, annotated_a11y = score_a11y(findings)
    axe, _ = score_a11y([(i["rule_id"], i["page_path"]) for i in raw["a11y_issues"] if i["source"] == "axe"])
    s = raw["stats"]
    return {
        "run_id": raw["run_id"], "status": raw["status"],
        "functional": summary(bugs), "a11y_qa_pilot": summary(qa), "a11y_axe_only": summary(axe),
        "seconds": s.get("seconds"), "llm_calls": s.get("llm_calls", 0),
        "tokens": s.get("input_tokens", 0) + s.get("output_tokens", 0),
        "input_tokens": s.get("input_tokens", 0), "output_tokens": s.get("output_tokens", 0),
        "tests": s.get("tests", 0), "passed": s.get("passed", 0), "failed": s.get("failed", 0),
        "bug_matches": [{"title": b["title"], "match": b["match"]} for b in annotated_bugs],
        "a11y_matches": [{"rule_id": r, "page": p, "match": m} for r, p, m in annotated_a11y],
    }


def summary(score: Score) -> dict:
    return {"found": sorted(score.found), "recall": score.recall, "precision": score.precision,
            "true_positives": score.true_positives, "reported": score.reported, "total": score.total}


def mean(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return statistics.mean(present) if present else None


def aggregate(scored: list[dict]) -> dict:
    out: dict = {"runs": len(scored)}
    for key in ("functional", "a11y_qa_pilot", "a11y_axe_only"):
        union = sorted({i for s in scored for i in s[key]["found"]})
        out[key] = {
            "mean_recall": mean([s[key]["recall"] for s in scored]),
            "mean_precision": mean([s[key]["precision"] for s in scored]),
            "found_in_any_run": union,
            "recall_any_run": len(union) / scored[0][key]["total"] if scored else 0,
            "found_in_every_run": sorted(set.intersection(*(set(s[key]["found"]) for s in scored))) if scored else [],
        }
    for key in ("seconds", "llm_calls", "tokens", "input_tokens", "output_tokens"):
        out[f"mean_{key}"] = mean([s[key] for s in scored])
    return out


def markdown(meta: dict, scored: list[dict], agg: dict) -> str:
    f, q, a = agg["functional"], agg["a11y_qa_pilot"], agg["a11y_axe_only"]
    lines = [
        f"# QA Pilot evaluation — {meta['started']}",
        "",
        f"Target: demo-shop ({len(FUNCTIONAL)} planted functional bugs, {len(ACCESSIBILITY)} planted accessibility "
        f"violations). {agg['runs']} run(s), LLM `{meta['llm']}`, max {meta['max_tests']} tests per run.",
        f"Goal: “{meta['goal']}”",
        "",
        "## Summary (mean over runs)",
        "",
        "| Metric | Functional bugs | Accessibility: QA Pilot | Accessibility: axe-core only |",
        "|---|---|---|---|",
        f"| Recall | {pct(f['mean_recall'])} | {pct(q['mean_recall'])} | {pct(a['mean_recall'])} |",
        f"| Precision | {pct(f['mean_precision'])} | {pct(q['mean_precision'])} | {pct(a['mean_precision'])} |",
        f"| Recall, union of runs | {pct(f['recall_any_run'])} | {pct(q['recall_any_run'])} | {pct(a['recall_any_run'])} |",
        f"| Found in any run | {', '.join(f['found_in_any_run']) or '—'} | {', '.join(q['found_in_any_run']) or '—'} "
        f"| {', '.join(a['found_in_any_run']) or '—'} |",
        "",
        f"Average time per run: **{agg['mean_seconds'] / 60:.1f} min** · LLM calls: **{agg['mean_llm_calls']:.0f}** · "
        f"tokens: **{agg['mean_tokens']:,.0f}** ({agg['mean_input_tokens']:,.0f} in / {agg['mean_output_tokens']:,.0f} out)"
        if agg["mean_seconds"] is not None else "",
        "",
        "## Per run",
        "",
        "| Run | Status | Tests (pass/fail) | Bugs reported | Functional recall | Functional precision | "
        "A11y recall (QA Pilot / axe) | Time | Tokens |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for i, s in enumerate(scored, 1):
        fn = s["functional"]
        lines.append(
            f"| {i} (`{s['run_id'][-6:]}`) | {s['status']} | {s['tests']} ({s['passed']}/{s['failed']}) | {fn['reported']} "
            f"| {pct(fn['recall'])} ({', '.join(fn['found']) or '—'}) | {pct(fn['precision'])} "
            f"| {pct(s['a11y_qa_pilot']['recall'])} / {pct(s['a11y_axe_only']['recall'])} "
            f"| {(s['seconds'] or 0) / 60:.1f} min | {s['tokens']:,} |")
    lines += ["", "## Ground truth coverage", "", "| ID | Planted issue | Runs that found it |", "|---|---|---|"]
    for gt_id, (name, *_) in {**FUNCTIONAL, **ACCESSIBILITY}.items():
        key = "functional" if gt_id.startswith("F") else "a11y_qa_pilot"
        hits = [str(i) for i, s in enumerate(scored, 1) if gt_id in s[key]["found"]]
        lines.append(f"| {gt_id} | {name} | {', '.join(hits) or '—'} |")
    lines += ["", "## Reported bugs and their match", ""]
    for i, s in enumerate(scored, 1):
        for b in s["bug_matches"]:
            lines.append(f"- run {i}: {b['title']} → **{b['match'] or 'no planted bug (false positive)'}**")
    lines += [
        "", "## Method", "",
        "- Each run uses a fresh project and a freshly reset demo-shop, so runs are independent.",
        "- Functional bugs are matched with keyword rules per planted bug (`eval/matching.py`); precision = reported "
        "bugs that match a planted bug / all reported bugs.",
        "- Accessibility issues are matched by rule id and page. “axe-core only” counts the axe findings on the same "
        "pages QA Pilot audited; QA Pilot adds the keyboard walk (keyboard trap, focus visibility) and the vision "
        "alt-text review. Genuine but unplanted violations count against precision, so accessibility precision is a "
        "lower bound.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--max-tests", type=int, default=8)
    p.add_argument("--goal", default=DEFAULT_GOAL)
    p.add_argument("--api", default="http://localhost:8000")
    p.add_argument("--shop", default="http://localhost:8080", help="demo-shop as seen from this machine")
    p.add_argument("--target", default="http://demo-shop:8000/", help="demo-shop as seen from the worker")
    p.add_argument("--llm", default="groq (see .env)", help="label for the report")
    p.add_argument("--timeout", type=int, default=60, help="minutes per run")
    p.add_argument("--rescore", type=Path, help="re-score raw data from an earlier results JSON; starts no runs")
    args = p.parse_args()

    if args.rescore:
        old = json.loads(args.rescore.read_text(encoding="utf-8"))
        meta, raw_runs = old["meta"], old["raw"]
    else:
        api = Api(args.api)
        api.register()
        meta = {"started": f"{datetime.now():%Y-%m-%d %H:%M}", "goal": args.goal, "max_tests": args.max_tests,
                "llm": args.llm, "api": args.api}
        raw_runs = []
        for n in range(1, args.runs + 1):
            raw_runs.append(one_run(api, n, args))
            if raw_runs[-1]["status"] != "completed" and "quota" in (raw_runs[-1]["error"] or ""):
                print("LLM quota used up: stopping. Try again when the quota resets.", flush=True)
                break

    completed = [r for r in raw_runs if r["status"] == "completed"]
    skipped = len(raw_runs) - len(completed)
    if skipped:
        print(f"{skipped} run(s) did not complete and are left out of the scores", flush=True)
    if not completed:
        sys.exit("No run completed; nothing to score.")
    meta["runs_not_completed"] = [{"run_id": r["run_id"], "error": r["error"]} for r in raw_runs if r["status"] != "completed"]

    scored = [score_run(r) for r in completed]
    agg = aggregate(scored)
    RESULTS.mkdir(exist_ok=True)
    stem = RESULTS / f"eval-{meta['started'].replace('-', '').replace(' ', '-').replace(':', '')}"
    stem.with_suffix(".json").write_text(json.dumps({"meta": meta, "summary": agg, "runs": scored, "raw": raw_runs},
                                                    indent=2), encoding="utf-8")
    stem.with_suffix(".md").write_text(markdown(meta, scored, agg), encoding="utf-8")
    f = agg["functional"]
    print(f"functional recall {pct(f['mean_recall'])}, precision {pct(f['mean_precision'])}; "
          f"a11y recall QA Pilot {pct(agg['a11y_qa_pilot']['mean_recall'])} vs axe {pct(agg['a11y_axe_only']['mean_recall'])}")
    print(f"saved {stem}.json and {stem}.md")


if __name__ == "__main__":
    main()
