import { useMutation, useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { bugKeys, listBugs } from '../api/bugs'
import {
  getRun,
  listSteps,
  listTestCases,
  runKeys,
  testCaseScreenshotPath,
  type Run,
  type RunStep,
  type TestCase,
} from '../api/runs'
import { AuthImage } from '../components/AuthImage'
import { SeverityBadge, TestStatusBadge } from '../components/Badges'
import { RunStatusBadge } from '../components/RunStatusBadge'
import { Alert, Button } from '../components/ui'
import { ApiError } from '../lib/api'
import { downloadFile } from '../lib/download'
import { errorText } from '../lib/errors'

function duration(run: Run): string {
  const seconds = (run.stats.seconds as number | undefined) ??
    (run.started_at && run.finished_at ? (Date.parse(run.finished_at) - Date.parse(run.started_at)) / 1000 : undefined)
  if (seconds === undefined) return '—'
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return m ? `${m} min ${s} s` : `${s} s`
}

function Stat({ label, value, tone = 'text-slate-900' }: { label: string; value: string | number; tone?: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-sm">
      <p className={`text-2xl font-bold tabular-nums ${tone}`}>{value}</p>
      <p className="text-xs text-slate-600">{label}</p>
    </div>
  )
}

function TestReport({ runId, tc, steps }: { runId: string; tc: TestCase; steps: RunStep[] }) {
  const log = steps.filter((s) => s.test_case_index === tc.index)
  return (
    <li className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h3 className="font-semibold text-slate-900">
          {tc.index + 1}. {tc.title} <span className="ml-1 rounded bg-slate-100 px-1.5 text-[11px] font-medium text-slate-600">{tc.type}</span>
        </h3>
        <TestStatusBadge status={tc.status} />
      </div>
      <p className="mt-1 text-sm text-slate-600">Expected: {tc.expected}</p>
      {tc.reason && <p className="mt-1 text-sm text-slate-800">{tc.reason}</p>}
      {tc.bug_id && (
        <p className="mt-2 text-sm">
          <Link to={`/bugs/${tc.bug_id}`} className="font-medium text-rose-700 hover:underline">View the bug report →</Link>
        </p>
      )}
      <details className="mt-3">
        <summary className="cursor-pointer text-sm font-medium text-indigo-600">
          Agent log ({log.length} steps){tc.has_final_screenshot ? ' and final screenshot' : ''}
        </summary>
        <ol className="mt-2 space-y-1 rounded bg-slate-900 p-3 font-mono text-xs leading-relaxed text-slate-100" aria-label={`Agent log of test ${tc.index + 1}`}>
          {log.map((s) => (
            <li key={s.index} className="whitespace-pre-line break-words">
              <span className="text-sky-300">{s.kind.toUpperCase()}</span> {s.message}
            </li>
          ))}
        </ol>
        {tc.has_final_screenshot && (
          <AuthImage
            key={`${runId}-${tc.index}`}
            path={testCaseScreenshotPath(runId, tc.index)}
            alt={`Final screen of test ${tc.index + 1}`}
            className="mt-3 max-h-[60vh] w-full rounded border border-slate-200 object-contain object-top"
          />
        )}
      </details>
    </li>
  )
}

export function RunReportPage() {
  const runId = useParams().runId ?? ''
  const run = useQuery({ queryKey: runKeys.detail(runId), queryFn: () => getRun(runId) })
  const testCases = useQuery({ queryKey: runKeys.testCases(runId), queryFn: () => listTestCases(runId), enabled: run.isSuccess })
  const steps = useQuery({ queryKey: runKeys.steps(runId), queryFn: () => listSteps(runId), enabled: run.isSuccess })
  const bugs = useQuery({ queryKey: bugKeys.list({ run_id: runId }), queryFn: () => listBugs({ run_id: runId }), enabled: run.isSuccess })
  const download = useMutation({ mutationFn: () => downloadFile(`/runs/${runId}/report.html`, 'qa-pilot-report.html') })

  if (run.error instanceof ApiError && run.error.status === 404) {
    return (
      <>
        <h1 className="text-2xl font-bold tracking-tight text-slate-900">Run not found</h1>
        <p className="mt-2"><Link to="/projects" className="text-indigo-600 hover:underline">Back to projects</Link></p>
      </>
    )
  }
  if (run.isPending) return <p className="text-slate-500">Loading report…</p>
  if (run.isError) return <Alert>{errorText(run.error)}</Alert>
  const r = run.data
  const cases = testCases.data ?? []
  const count = (status: TestCase['status']) => cases.filter((t) => t.status === status).length
  const tokens = ((r.stats.input_tokens as number | undefined) ?? 0) + ((r.stats.output_tokens as number | undefined) ?? 0)

  return (
    <>
      <p className="text-sm">
        <Link to={`/projects/${r.project_id}/settings`} className="text-indigo-600 hover:underline">{r.project_name}</Link>
        <span className="text-slate-400"> / </span>
        <Link to={`/runs/${r.id}`} className="text-indigo-600 hover:underline">Live view</Link>
      </p>
      <div className="mt-1 flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-bold tracking-tight text-slate-900">Run report</h1>
        <RunStatusBadge status={r.status} />
        <Button variant="secondary" className="ml-auto" disabled={download.isPending} onClick={() => download.mutate()}>
          {download.isPending ? 'Preparing…' : 'Download report'}
        </Button>
      </div>
      <p className="mt-1 text-sm text-slate-600">Goal: {r.goal}</p>
      <p className="text-xs text-slate-500">
        {r.started_at ? new Date(r.started_at).toLocaleString() : 'Not started'} · {duration(r)} ·{' '}
        {(r.stats.llm_calls as number | undefined) ?? 0} LLM calls · {(tokens / 1000).toFixed(1)}k tokens
      </p>
      <div className="mt-2"><Alert>{download.isError ? errorText(download.error) : ''}</Alert></div>
      {r.error && <p role="alert" className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">{r.error}</p>}

      <section aria-label="Summary" className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-5">
        <Stat label="tests" value={cases.length} />
        <Stat label="passed" value={count('passed')} tone="text-emerald-700" />
        <Stat label="failed" value={count('failed')} tone="text-rose-700" />
        <Stat label="blocked" value={count('blocked')} tone="text-amber-800" />
        <Stat label="bugs" value={bugs.data?.length ?? '—'} />
      </section>

      <section aria-labelledby="bugs-heading" className="mt-8">
        <h2 id="bugs-heading" className="text-lg font-semibold text-slate-900">Bugs found</h2>
        {bugs.data?.length === 0 && <p className="mt-2 text-sm text-slate-600">No bugs in this run.</p>}
        {bugs.data && bugs.data.length > 0 && (
          <ul className="mt-3 divide-y divide-slate-200 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
            {bugs.data.map((bug) => (
              <li key={bug.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <SeverityBadge severity={bug.severity} />
                <Link to={`/bugs/${bug.id}`} className="min-w-0 flex-1 font-medium text-slate-900 hover:text-indigo-700 hover:underline">
                  {bug.title}
                </Link>
                {bug.occurrences > 1 && <span className="text-xs text-slate-500">seen {bug.occurrences}×</span>}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="tests-heading" className="mt-8">
        <h2 id="tests-heading" className="text-lg font-semibold text-slate-900">Test cases</h2>
        {testCases.isPending && <p className="mt-2 text-sm text-slate-500">Loading…</p>}
        <ol className="mt-3 space-y-3" aria-label="Test results">
          {cases.map((tc) => (
            <TestReport key={tc.index} runId={r.id} tc={tc} steps={steps.data ?? []} />
          ))}
        </ol>
      </section>
    </>
  )
}
