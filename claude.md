# QA Pilot — Project Brief

> An autonomous QA engineer for teams that don't have one.
> Final-year CSE project: full-stack web application with agentic AI and test automation.

## What we are building

A web application where a user logs in, adds their website (URL), and describes what to test in
plain English (e.g. "test signup, login and add-to-cart"). AI agents open a real browser on the
server, explore the site, plan test cases (including edge cases), execute them, judge results,
report bugs with screenshots and reproduction steps, and audit accessibility (WCAG).
The user watches the test run live in the dashboard. Passing tests are saved, re-run automatically
on a schedule, and self-heal when the UI changes.

## Problem it solves (keep in mind when making design choices)

- AI-generated / "vibe-coded" apps ship with more logic bugs and are rarely runtime-tested.
- 95.9% of top home pages fail WCAG checks (WebAIM Million 2026); India's Supreme Court
  (Amar Jain v. Union of India, 2025) held inclusive digital access is part of Article 21.
- Traditional test scripts are costly to write and break when the UI changes.
- Target users: student teams, startups, freelancers — no dedicated QA.

## Tech stack (do not add technologies without a clear reason)

| Layer | Choice |
|---|---|
| Frontend | React + Vite + TypeScript + Tailwind CSS, React Router, TanStack Query, Recharts |
| Backend API | Python FastAPI |
| Auth | JWT (access + refresh tokens), bcrypt password hashing, role: owner / member |
| Database | MongoDB 7 (official Docker image) accessed with Beanie 2 (async ODM built on Pydantic + PyMongo's native async client; Motor is deprecated). Collections and indexes are defined in the Beanie models, so there are no migrations. Why MongoDB: run steps, test plans, agent outputs and bug reports are nested JSON documents with varying shape |
| Background jobs | Separate worker process that picks jobs from a `jobs` collection in MongoDB (no extra queue service). Claim jobs atomically with `find_one_and_update({status: 'pending'}, {$set: {status: 'running'}})`, sorted by created_at |
| Scheduling | APScheduler inside the worker (cron-style scheduled test runs) |
| Live updates | Server-Sent Events (SSE) from API to dashboard during a run |
| Browser automation | Playwright (Python), headless Chromium in the worker |
| LLM | Pluggable provider interface. Default: Google Gemini (free tier, vision). Also: Groq, Ollama |
| Accessibility | axe-core injected via Playwright + LLM keyboard-navigation checks |
| File storage | Screenshots on local disk (`storage/`), served by the API (can switch to S3 later) |
| Dev setup | Docker Compose with 5 services: mongo, api, worker, frontend, demo-shop. Named volumes: `mongo-data` (database) and `qapilot-files` (screenshots, shared by api and worker). Mongo healthcheck so api/worker start after it is ready. Worker image based on the official Playwright Python image. To browse data, use MongoDB Compass on `mongodb://localhost:27017` |
| Demo target | `demo-shop/`: small deliberately buggy web shop |
| Tests | pytest (backend, against a real MongoDB with a throwaway database per test; start it with `docker compose up -d mongo`), Vitest (frontend) |

## Architecture

```
 React Dashboard  ──HTTP/JWT──►  FastAPI API  ──►  MongoDB
      ▲                              │   ▲
      └────────── SSE live ◄─────────┘   │ run status, steps, bugs
                                         │
                        jobs coll. ──►  Worker process
                                         │
                                         ▼
                                   Orchestrator
     Explorer agent ─► site map (pages, forms, buttons, links)
     Planner agent  ─► test cases (happy path + edge cases)
     Executor agent ─► Playwright browser (click / type / navigate)  ◄─ snapshot (a11y tree + screenshot)
     Judge agent    ─► pass / fail / blocked + reason
     Reporter agent ─► bugs (title, steps, severity, screenshot, suggested fix)
     A11y audit     ─► axe-core + keyboard navigation → WCAG score
     Healer agent   ─► on replay failure, finds moved/renamed element, repairs step, re-runs
```

### Agent design rules

- Agents talk to the browser ONLY through a small typed tool set:
  `goto(url)`, `click(ref)`, `type(ref, text)`, `select(ref, value)`, `press(key)`,
  `scroll()`, `back()`, `wait(ms)`, `snapshot()`, `screenshot()`, `done(result)`.
- `snapshot()` returns a compact, numbered accessibility-tree view of the page
  (role, name, ref id), never raw full HTML, to keep prompts small.
- Every LLM response that drives an action must be structured JSON validated with Pydantic.
  On invalid JSON: retry once with the error message, then fail the step gracefully.
- Hard limits: max steps per test (default 25), max tests per run (default 15), timeouts.
- Every agent thought/action/observation is saved as a `run_steps` document and streamed to the UI.

## Data model (initial MongoDB collections)

users, projects (name, base_url, owner), project_members, credentials (test logins, encrypted),
runs (project, goal, status, started/finished, stats), test_cases, run_steps (thought, action,
screenshot_path, timestamp), bugs (severity, title, steps, expected, actual, screenshot, status
open/fixed/ignored), a11y_issues, saved_tests (replayable steps with multiple locators),
schedules (cron, project, enabled), jobs (type, payload, status, attempts).
Reference other documents by id (e.g. `project_id`); embed only small, owned data (e.g. a test
case's steps inside the test case). Add indexes on `project_id`, `run_id`, `status`, `created_at`.
run_steps stay a separate collection (runs can have hundreds of steps).

## Dashboard pages

Login / Register · Projects list · Project overview (pass-rate trend, open bugs, a11y score) ·
New run (goal text, options: accessibility, mobile viewport, max tests) · Live run view
(step feed + latest screenshot, updating live) · Run report · Bugs (filter by severity/status) ·
Accessibility · Saved tests (replay, view heal history, export Playwright script) · Schedules ·
Settings (LLM provider, test credentials, team members).

## Directory layout

```
qa-pilot/
  CLAUDE.md
  README.md
  docker-compose.yml      # mongo, api, worker, frontend, demo-shop
  .env.example            # GEMINI_API_KEY=, LLM_PROVIDER=gemini, JWT_SECRET=, MONGO_URL=mongodb://mongo:27017, MONGO_DB=qapilot
  backend/
    app/
      main.py             # FastAPI app
      core/               # config, security (JWT), db
      models/             # Beanie documents (collections + indexes)
      schemas/            # Pydantic request/response
      routes/             # auth, projects, runs, bugs, a11y, tests, schedules, stream (SSE)
      services/
    worker/
      main.py             # job loop + APScheduler
      orchestrator.py
      agents/             # explorer, planner, executor, judge, reporter, healer
      agents/prompts/*.md
      browser/            # session, snapshot, actions
      llm/                # base, gemini, groq, ollama
      audits/             # accessibility
    tests/
  frontend/               # React + Vite app
  demo-shop/              # buggy target app + GROUND_TRUTH.md (planted bugs)
  eval/                   # benchmark script + results
```

## Safety rules (must be enforced in code)

- Show a notice when adding a project: only test sites you own or are authorised to test.
- Security-style probes (XSS/SQLi strings) are off by default; only for projects the owner marks
  as "my own site" and enables explicitly.
- Never submit real payments; stop and mark the test "blocked: needs human" at payment/OTP/CAPTCHA.
- Test-site credentials encrypted at rest; never logged or sent to the LLM in plain text
  (use placeholders the executor fills in).
- Small delays between browser actions; one run per project at a time.

## Coding conventions

- Type hints everywhere; Pydantic models for all agent inputs/outputs and API schemas.
- Prompts live in `worker/agents/prompts/*.md`, not inline strings.
- Every API route requires auth except register/login; users only see their own projects.
- Every feature gets at least one test; LLM calls are mocked in unit tests.
- Never hard-code API keys; read from `.env`.
- Before finishing a task: run the tests, start the stack, try the feature against `demo-shop`,
  and report what works and what doesn't.

## Evaluation (for the project report)

`demo-shop/GROUND_TRUTH.md` lists ~10 planted functional bugs and ~10 planted accessibility
violations. The eval script runs QA Pilot against demo-shop and reports detection rate (recall),
false positives (precision), time per run, tokens/cost per run, and a comparison with axe-core
alone for accessibility.