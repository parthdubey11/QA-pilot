import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import { screenshotPath, type Run, type RunStep, type StepKind, type TestCase, type TestStatus } from '../api/runs'
import { AuthImage } from '../components/AuthImage'
import { Breadcrumbs } from '../components/ProjectHeader'
import { RunStatusBadge } from '../components/RunStatusBadge'
import { useRunStream } from '../hooks/useRunStream'

// ---------- step presentation ----------

const KIND: Record<StepKind, { label: string; dot: string; icon: ReactNode }> = {
  thought: {
    label: 'Thinking',
    dot: 'bg-violet-100 text-violet-700 ring-violet-200',
    icon: <path d="M10 2.5a5.5 5.5 0 0 0-3.2 9.98V14a1 1 0 0 0 1 1h4.4a1 1 0 0 0 1-1v-1.52A5.5 5.5 0 0 0 10 2.5ZM8 17h4" strokeLinecap="round" />,
  },
  action: {
    label: 'Action',
    dot: 'bg-sky-100 text-sky-700 ring-sky-200',
    icon: <path d="m5 3 10 5.5-4.5 1.5L8.5 15 5 3Z" strokeLinejoin="round" />,
  },
  observation: {
    label: 'Observed',
    dot: 'bg-amber-100 text-amber-800 ring-amber-200',
    icon: <><path d="M2.5 10s2.7-5 7.5-5 7.5 5 7.5 5-2.7 5-7.5 5-7.5-5-7.5-5Z" /><circle cx="10" cy="10" r="2.2" /></>,
  },
  info: {
    label: 'Info',
    dot: 'bg-slate-100 text-slate-600 ring-slate-200',
    icon: <><circle cx="10" cy="10" r="7" /><path d="M10 9v4.5M10 6.5v.01" strokeLinecap="round" /></>,
  },
  error: {
    label: 'Error',
    dot: 'bg-rose-100 text-rose-700 ring-rose-200',
    icon: <><path d="M10 3 2.5 16.5h15L10 3Z" strokeLinejoin="round" /><path d="M10 8.5v3.5M10 14.3v.01" strokeLinecap="round" /></>,
  },
}

const testStatusStyles: Record<TestStatus, string> = {
  pending: 'bg-slate-100 text-slate-600 ring-slate-200',
  running: 'bg-sky-50 text-sky-800 ring-sky-200',
  passed: 'bg-emerald-50 text-emerald-800 ring-emerald-200',
  failed: 'bg-rose-50 text-rose-800 ring-rose-200',
  blocked: 'bg-amber-50 text-amber-900 ring-amber-200',
  error: 'bg-slate-100 text-slate-800 ring-slate-300',
}

const time = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })

const PHASE_SUFFIX: Partial<Record<string, string>> = { judge: ' · verdict', report: ' · bug report', heal: ' · heal' }

function stepLabel(step: RunStep): string | null {
  if (step.phase === 'explore') return 'Explore'
  if (step.phase === 'audit') return 'Accessibility'
  if (step.phase === 'plan') return 'Plan'
  if (step.test_case_index !== null) return `Test ${step.test_case_index + 1}${PHASE_SUFFIX[step.phase ?? ''] ?? ''}`
  return null
}

function StepItem({ step, onShowScreenshot, active }: { step: RunStep; onShowScreenshot: (i: number) => void; active: boolean }) {
  const label = stepLabel(step)
  const k = KIND[step.kind]
  const body =
    step.kind === 'thought' ? 'rounded-lg rounded-tl-sm bg-violet-50/70 px-3 py-2 italic text-slate-800 ring-1 ring-violet-100'
      : step.kind === 'action' ? 'font-mono text-[13px] text-slate-800'
        : step.kind === 'error' ? 'text-rose-800'
          : 'text-slate-700'
  return (
    <li className="relative flex gap-3 py-2.5 pl-1 pr-3">
      <span aria-hidden="true" className="absolute bottom-0 left-[17px] top-0 w-px bg-slate-200" />
      <span className={`relative z-10 mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full ring-1 ${k.dot}`}>
        <svg viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">{k.icon}</svg>
      </span>
      <div className="min-w-0 flex-1">
        <p className="flex flex-wrap items-baseline gap-x-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
          <span>{k.label}</span>
          {label && <span className="text-slate-400">{label}</span>}
          <time dateTime={step.created_at} className="ml-auto font-normal normal-case tracking-normal tabular-nums text-slate-400">
            {time(step.created_at)}
          </time>
        </p>
        <p className={`mt-1 whitespace-pre-line break-words text-sm ${body}`}>{step.message}</p>
        {(step.url || step.has_screenshot) && (
          <p className="mt-1 flex min-w-0 items-center gap-2 text-xs text-slate-500">
            {step.url && <span className="truncate">{step.url}</span>}
            {step.has_screenshot && (
              <button
                type="button"
                onClick={() => onShowScreenshot(step.index)}
                aria-pressed={active}
                className={`shrink-0 rounded px-1.5 py-0.5 font-medium ${active ? 'bg-indigo-600 text-white' : 'text-indigo-600 hover:bg-indigo-50'}`}
              >
                {active ? 'Showing screenshot' : 'Show screenshot'}
              </button>
            )}
          </p>
        )}
        {step.snapshot && (
          <details className="mt-2">
            <summary className="cursor-pointer text-xs font-medium text-indigo-600">Accessibility snapshot</summary>
            <pre className="mt-2 max-h-72 overflow-auto rounded-md bg-slate-900 p-3 text-xs leading-relaxed text-slate-100">{step.snapshot}</pre>
          </details>
        )}
      </div>
    </li>
  )
}

function RunStats({ run }: { run: Run }) {
  const s = run.stats as Record<string, number | undefined>
  if (s.pages === undefined) return null
  const parts = [
    `${s.pages} pages explored`,
    s.tests !== undefined && `${s.tests} tests`,
    s.passed !== undefined && `${s.passed} passed`,
    s.failed !== undefined && `${s.failed} failed`,
    s.blocked !== undefined && `${s.blocked} blocked`,
    s.errors ? `${s.errors} errors` : null,
    s.llm_calls !== undefined && `${s.llm_calls} LLM calls`,
    s.input_tokens !== undefined && `${((s.input_tokens + (s.output_tokens ?? 0)) / 1000).toFixed(1)}k tokens`,
    s.seconds !== undefined && `${Math.round(s.seconds)} s`,
  ].filter(Boolean)
  return <p className="mt-2 text-xs text-slate-500">{parts.join(' · ')}</p>
}

function TestCasesPanel({ testCases, selected, onSelect }: { testCases: TestCase[]; selected: number | null; onSelect: (index: number | null) => void }) {
  const [open, setOpen] = useState(true)
  const done = testCases.filter((t) => t.status !== 'pending' && t.status !== 'running').length
  return (
    <section aria-labelledby="tests-heading" className="mt-4 rounded-xl border border-slate-200 bg-white shadow-sm">
      <div className="flex items-center gap-3 px-4 py-3">
        <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-controls="test-case-list"
          className="flex items-center gap-2 text-left">
          <svg aria-hidden="true" viewBox="0 0 20 20" className={`h-4 w-4 text-slate-500 transition-transform ${open ? 'rotate-90' : ''}`} fill="currentColor">
            <path fillRule="evenodd" d="M7.2 14.8a.75.75 0 0 1 0-1.06L10.94 10 7.2 6.26a.75.75 0 1 1 1.06-1.06l4.27 4.27a.75.75 0 0 1 0 1.06L8.26 14.8a.75.75 0 0 1-1.06 0Z" clipRule="evenodd" />
          </svg>
          <h2 id="tests-heading" className="text-sm font-semibold text-slate-900">
            Test cases <span className="font-normal text-slate-500">({testCases.length})</span>
          </h2>
        </button>
        <div className="hidden h-1.5 w-32 overflow-hidden rounded-full bg-slate-100 sm:block" aria-hidden="true">
          <div className="h-full rounded-full bg-indigo-500 transition-all" style={{ width: `${testCases.length ? (100 * done) / testCases.length : 0}%` }} />
        </div>
        <span className="text-xs text-slate-500">{done}/{testCases.length} done</span>
        {selected !== null && (
          <button type="button" onClick={() => onSelect(null)} className="ml-auto text-xs font-medium text-indigo-600 hover:underline">
            Show all steps
          </button>
        )}
      </div>
      <ol id="test-case-list" hidden={!open} className="max-h-48 divide-y divide-slate-100 overflow-y-auto border-t border-slate-100" aria-label="Test cases">
        {testCases.map((tc) => (
          <li key={tc.index}>
            <button
              type="button"
              onClick={() => onSelect(selected === tc.index ? null : tc.index)}
              aria-pressed={selected === tc.index}
              className={`flex w-full items-start gap-3 px-4 py-2 text-left hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-indigo-600 ${selected === tc.index ? 'bg-indigo-50' : ''}`}
            >
              <span className="w-6 shrink-0 pt-0.5 text-xs tabular-nums text-slate-500">{tc.index + 1}.</span>
              <span className="min-w-0 flex-1">
                <span className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium text-slate-900">{tc.title}</span>
                  <span className="rounded bg-slate-100 px-1.5 text-[11px] font-medium text-slate-600">{tc.type}</span>
                </span>
                {tc.bug_id && <span className="ml-1 text-xs font-semibold text-rose-700">· Bug reported</span>}
                {selected === tc.index && (
                  <>
                    <span className="mt-0.5 block text-xs text-slate-600">Expected: {tc.expected}</span>
                    {tc.reason && <span className="mt-1 block text-sm text-slate-800">{tc.reason}</span>}
                  </>
                )}
              </span>
              <span className={`shrink-0 rounded-full px-2.5 py-0.5 text-xs font-semibold capitalize ring-1 ${testStatusStyles[tc.status]}`}>
                {tc.status}
              </span>
            </button>
          </li>
        ))}
      </ol>
    </section>
  )
}

// ---------- resizable workspace ----------

function readNumber(key: string, fallback: number): number {
  try {
    const v = Number(localStorage.getItem(key))
    return Number.isFinite(v) && v > 0 ? v : fallback
  } catch {
    return fallback
  }
}

function hasStored(key: string): boolean {
  try {
    return localStorage.getItem(key) !== null
  } catch {
    return false
  }
}

function usePersistentNumber(key: string, fallback: () => number): [number, (v: number) => void, (v: number) => void] {
  const [value, setValue] = useState(() => readNumber(key, fallback()))
  const save = useCallback((v: number) => {
    setValue(v)
    try {
      localStorage.setItem(key, String(Math.round(v)))
    } catch {
      /* storage unavailable: keep it for this visit only */
    }
  }, [key])
  return [value, save, setValue]
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))
const MIN_HEIGHT = 280
const maxHeight = () => Math.max(MIN_HEIGHT, (typeof window === 'undefined' ? 900 : window.innerHeight) - 96)

/** Starts a pointer drag; `onMove` gets the pointer's offset from where the drag started. */
function startDrag(e: PointerEvent, onMove: (dx: number, dy: number) => void) {
  e.preventDefault()
  const x0 = e.clientX
  const y0 = e.clientY
  const move = (ev: globalThis.PointerEvent) => onMove(ev.clientX - x0, ev.clientY - y0)
  const up = () => {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', up)
    document.body.style.userSelect = ''
    document.body.style.cursor = ''
  }
  document.body.style.userSelect = 'none'
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', up)
}

type Filter = 'all' | 'thought' | 'action' | 'error'
const FILTERS: { id: Filter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'thought', label: 'Thinking' },
  { id: 'action', label: 'Actions' },
  { id: 'error', label: 'Errors' },
]

function ActivityWorkspace({ runId, run, steps, selected, layoutKey }: {
  runId: string
  run: Run | null
  steps: RunStep[]
  selected: number | null
  layoutKey: string // changes when the content above the panel changes height
}) {
  const [height, setHeight, setHeightUnsaved] = usePersistentNumber('qa-pilot.run.height', () => clamp(Math.round(maxHeight() * 0.75), 420, 760))
  const sectionRef = useRef<HTMLElement>(null)
  const fillWindow = useCallback(() => {
    // Narrow screens stack feed and screenshot; there the page scrolls anyway, so give the panel most of the screen.
    if (window.innerWidth < 1024) return clamp(Math.round(window.innerHeight * 0.85), 480, maxHeight())
    const top = sectionRef.current?.getBoundingClientRect().top ?? 0
    return clamp(Math.round(window.innerHeight - top - 24), 360, maxHeight())
  }, [])

  // Until the user picks a size, the panel fills the rest of the window, so the page needn't scroll.
  useLayoutEffect(() => {
    const fit = () => {
      if (!hasStored('qa-pilot.run.height')) setHeightUnsaved(fillWindow())
    }
    fit()
    window.addEventListener('resize', fit)
    return () => window.removeEventListener('resize', fit)
  }, [fillWindow, setHeightUnsaved, layoutKey])
  const [split, setSplit] = usePersistentNumber('qa-pilot.run.split', () => 58) // feed width, % of the panel
  const [full, setFull] = useState(false)
  const [filter, setFilter] = useState<Filter>('all')
  const [follow, setFollow] = useState(true)
  const [pinned, setPinned] = useState<number | null>(null) // screenshot chosen from the feed; null = latest
  const feedRef = useRef<HTMLDivElement>(null)
  const panesRef = useRef<HTMLDivElement>(null)

  const byTest = selected === null ? steps : steps.filter((s) => s.test_case_index === selected)
  const shown = filter === 'all' ? byTest : byTest.filter((s) => (filter === 'error' ? s.kind === 'error' : s.kind === filter))
  const latestShot = [...byTest].reverse().find((s) => s.has_screenshot)
  const shot = (pinned !== null && byTest.find((s) => s.index === pinned && s.has_screenshot)) || latestShot
  const live = run?.status === 'queued' || run?.status === 'running'
  const lastStep = steps.at(-1)
  const thinking = live && lastStep?.kind !== 'thought' && steps.length > 0

  // Follow the newest step inside the feed only; the page itself never scrolls.
  useLayoutEffect(() => {
    const el = feedRef.current
    if (el && follow) el.scrollTop = el.scrollHeight
  }, [shown.length, follow, height, split, full])

  const onFeedScroll = () => {
    const el = feedRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40
    if (atBottom !== follow) setFollow(atBottom)
  }

  useEffect(() => {
    if (!full) return
    const onKey = (e: globalThis.KeyboardEvent) => e.key === 'Escape' && setFull(false)
    window.addEventListener('keydown', onKey)
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = ''
    }
  }, [full])

  const resizeHeight = (e: PointerEvent) => {
    const h0 = height
    startDrag(e, (_dx, dy) => setHeight(clamp(h0 + dy, MIN_HEIGHT, maxHeight())))
    document.body.style.cursor = 'row-resize'
  }
  const resizeSplit = (e: PointerEvent) => {
    const w = panesRef.current?.getBoundingClientRect().width ?? 1
    const s0 = split
    startDrag(e, (dx) => setSplit(clamp(s0 + (100 * dx) / w, 30, 80)))
    document.body.style.cursor = 'col-resize'
  }
  const keyHeight = (e: KeyboardEvent) => {
    const step = e.shiftKey ? 120 : 40
    if (e.key === 'ArrowUp') setHeight(clamp(height - step, MIN_HEIGHT, maxHeight()))
    else if (e.key === 'ArrowDown') setHeight(clamp(height + step, MIN_HEIGHT, maxHeight()))
    else return
    e.preventDefault()
  }
  const keySplit = (e: KeyboardEvent) => {
    if (e.key === 'ArrowLeft') setSplit(clamp(split - 5, 30, 80))
    else if (e.key === 'ArrowRight') setSplit(clamp(split + 5, 30, 80))
    else return
    e.preventDefault()
  }

  const heading = selected === null ? 'Agent activity' : `Agent activity · Steps of test ${selected + 1}`

  return (
    <section
      ref={sectionRef}
      aria-labelledby="feed-heading"
      className={full
        ? 'fixed inset-0 z-50 flex flex-col bg-slate-100 p-3 sm:p-5'
        : 'mt-4'}
    >
      <div
        className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm"
        style={full ? undefined : { height }}
      >
        {/* toolbar */}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-slate-200 bg-slate-50/80 px-4 py-2.5">
          <h2 id="feed-heading" className="flex items-center gap-2 text-sm font-semibold text-slate-900">
            {live && (
              <span className="relative flex h-2.5 w-2.5" aria-hidden="true">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
                <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-emerald-500" />
              </span>
            )}
            {heading} <span className="font-normal text-slate-500">({shown.length})</span>
          </h2>
          <div role="group" aria-label="Show" className="flex rounded-lg bg-slate-200/70 p-0.5">
            {FILTERS.map((f) => (
              <button key={f.id} type="button" onClick={() => setFilter(f.id)} aria-pressed={filter === f.id}
                className={`rounded-md px-2.5 py-1 text-xs font-medium ${filter === f.id ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-600 hover:text-slate-900'}`}>
                {f.label}
              </button>
            ))}
          </div>
          <div className="ml-auto flex items-center gap-1">
            <button type="button" onClick={() => setFollow((v) => !v)} aria-pressed={follow}
              className={`rounded-md px-2.5 py-1 text-xs font-medium ${follow ? 'bg-indigo-50 text-indigo-700 ring-1 ring-indigo-200' : 'text-slate-600 hover:bg-slate-200/70'}`}>
              {follow ? 'Following latest' : 'Follow latest'}
            </button>
            <button type="button" onClick={() => setFull((v) => !v)} aria-pressed={full}
              className="rounded-md p-1.5 text-slate-600 hover:bg-slate-200/70 hover:text-slate-900"
              title={full ? 'Exit full screen (Esc)' : 'Full screen'}>
              <span className="sr-only">{full ? 'Exit full screen' : 'Full screen'}</span>
              <svg viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
                {full
                  ? <path d="M8 3v5H3M12 3v5h5M8 17v-5H3M12 17v-5h5" strokeLinecap="round" strokeLinejoin="round" />
                  : <path d="M3 8V3h5M17 8V3h-5M3 12v5h5M17 12v5h-5" strokeLinecap="round" strokeLinejoin="round" />}
              </svg>
            </button>
          </div>
        </div>

        {/* panes */}
        <div ref={panesRef} className="flex min-h-0 flex-1 flex-col lg:flex-row" style={{ ['--split' as string]: `${split}%` }}>
          <div className="relative flex min-h-0 flex-1 flex-col lg:w-[var(--split)] lg:flex-none">
            <div ref={feedRef} onScroll={onFeedScroll} className="min-h-[220px] flex-1 overflow-y-auto px-3 py-2 lg:min-h-0" tabIndex={0} aria-label="Agent activity feed">
              {shown.length === 0 ? (
                <p className="px-2 py-8 text-center text-sm text-slate-500">
                  {run?.status === 'queued' ? 'Waiting for the worker to pick up this run…'
                    : steps.length === 0 ? 'No steps yet.' : 'No steps match this filter.'}
                </p>
              ) : (
                <ol aria-label="Step feed">
                  {shown.map((step) => (
                    <StepItem key={step.index} step={step} active={shot?.index === step.index && pinned !== null}
                      onShowScreenshot={(i) => setPinned(pinned === i ? null : i)} />
                  ))}
                </ol>
              )}
              {thinking && filter === 'all' && selected === null && (
                <p className="flex items-center gap-2 px-2 py-2 text-xs text-slate-500" aria-hidden="true">
                  <span className="flex gap-1">
                    <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-violet-400 [animation-delay:-0.3s]" />
                    <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-violet-400 [animation-delay:-0.15s]" />
                    <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-violet-400" />
                  </span>
                  Working…
                </p>
              )}
            </div>
            {!follow && shown.length > 0 && (
              <button type="button" onClick={() => setFollow(true)}
                className="absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full bg-slate-900 px-3 py-1.5 text-xs font-semibold text-white shadow-lg hover:bg-slate-800">
                Jump to latest ↓
              </button>
            )}
          </div>

          {/* vertical splitter (wide screens) */}
          <div
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize activity and screenshot panes"
            aria-valuemin={30}
            aria-valuemax={80}
            aria-valuenow={Math.round(split)}
            tabIndex={0}
            onPointerDown={resizeSplit}
            onKeyDown={keySplit}
            onDoubleClick={() => setSplit(58)}
            className="group hidden w-2 shrink-0 cursor-col-resize items-center justify-center border-x border-slate-200 bg-slate-50 hover:bg-indigo-50 focus-visible:bg-indigo-50 focus-visible:outline-none lg:flex"
          >
            <span className="h-10 w-0.5 rounded-full bg-slate-300 group-hover:bg-indigo-400 group-focus-visible:bg-indigo-500" />
          </div>

          <div className="flex h-44 shrink-0 flex-col border-t border-slate-200 bg-slate-50 lg:h-auto lg:min-w-0 lg:flex-1 lg:border-t-0">
            <div className="flex items-center gap-2 px-4 py-2 text-xs">
              <h3 id="shot-heading" className="font-semibold text-slate-900">{pinned !== null && shot ? 'Screenshot' : 'Latest screenshot'}</h3>
              {shot && <span className="truncate text-slate-500">Step {shot.index + 1} · {shot.url}</span>}
              {pinned !== null && (
                <button type="button" onClick={() => setPinned(null)} className="ml-auto shrink-0 font-medium text-indigo-600 hover:underline">
                  Back to latest
                </button>
              )}
            </div>
            <div className="min-h-0 flex-1 overflow-auto px-3 pb-3">
              {shot ? (
                <AuthImage
                  key={shot.index}
                  path={screenshotPath(runId, shot.index)}
                  alt={`Screenshot from step ${shot.index + 1}`}
                  className="w-full rounded-lg border border-slate-200 bg-white object-contain object-top shadow-sm"
                />
              ) : (
                <div className="flex h-full items-center justify-center rounded-lg border border-dashed border-slate-300 text-sm text-slate-500">
                  No screenshot yet.
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* horizontal resize handle */}
      {!full && (
        <div
          role="separator"
          aria-orientation="horizontal"
          aria-label="Resize activity panel"
          aria-valuemin={MIN_HEIGHT}
          aria-valuemax={maxHeight()}
          aria-valuenow={Math.round(height)}
          tabIndex={0}
          onPointerDown={resizeHeight}
          onKeyDown={keyHeight}
          onDoubleClick={() => {
            try {
              localStorage.removeItem('qa-pilot.run.height')
            } catch {
              /* ignore */
            }
            setHeightUnsaved(fillWindow())
          }}
          title="Drag to resize (double-click to reset)"
          className="group mx-auto mt-1 flex h-4 w-full cursor-row-resize items-center justify-center rounded focus-visible:outline-none"
        >
          <span className="h-1 w-16 rounded-full bg-slate-300 transition-colors group-hover:bg-indigo-400 group-focus-visible:bg-indigo-500" />
        </div>
      )}
    </section>
  )
}

// ---------- page ----------

export function RunPage() {
  const runId = useParams().runId ?? ''
  return <RunView key={runId} runId={runId} />
}

function RunView({ runId }: { runId: string }) {
  const { run, steps, testCases, connection, error } = useRunStream(runId)
  const [selected, setSelected] = useState<number | null>(null)

  if (connection === 'error') {
    return (
      <>
        <h1 className="text-2xl font-bold tracking-tight text-slate-900">Run not found</h1>
        <p className="mt-2 text-slate-600">
          It may have been deleted, or it belongs to someone else. <Link to="/projects" className="text-indigo-600 hover:underline">Back to projects</Link>
        </p>
      </>
    )
  }

  const finished = run && run.status !== 'queued' && run.status !== 'running'

  return (
    <>
      <Breadcrumbs items={[
        { label: 'Projects', to: '/projects' },
        ...(run ? [{ label: run.project_name, to: `/projects/${run.project_id}` }] : []),
        { label: 'Run' },
      ]} />

      <div className="mt-3 rounded-xl border border-slate-200 bg-white px-5 py-4 shadow-sm">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-xl font-bold tracking-tight text-slate-900 sm:text-2xl">Run</h1>
          {run && <RunStatusBadge status={run.status} />}
          <span className="text-xs text-slate-500" aria-live="polite">
            {connection === 'live' && '● Live'}
            {connection === 'connecting' && 'Connecting…'}
            {connection === 'reconnecting' && 'Connection lost, reconnecting…'}
          </span>
          {finished && (
            <span className="ml-auto flex gap-2">
              <Link to={`/runs/${run.id}/report`}
                className="rounded-lg bg-indigo-600 px-3.5 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-700">
                View report
              </Link>
              <Link to={`/projects/${run.project_id}/runs/new`}
                className="rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-sm font-semibold text-slate-700 shadow-sm hover:bg-slate-50">
                Run again
              </Link>
            </span>
          )}
        </div>
        {run && <p className="mt-1 text-sm text-slate-700"><span className="text-slate-500">Goal:</span> {run.goal}</p>}
        {run && <RunStats run={run} />}
        {run?.error && (
          <p role="alert" className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">{run.error}</p>
        )}
        {error && connection === 'reconnecting' && <p className="mt-2 text-xs text-slate-500">({error})</p>}
      </div>

      {testCases.length > 0 && <TestCasesPanel testCases={testCases} selected={selected} onSelect={setSelected} />}

      <ActivityWorkspace runId={runId} run={run} steps={steps} selected={selected}
        layoutKey={[testCases.length > 0, run?.status, Boolean(run?.error), Boolean(run && run.stats.pages !== undefined)].join()} />
    </>
  )
}
