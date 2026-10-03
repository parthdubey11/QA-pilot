import { useQuery } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipContentProps,
} from 'recharts'
import type { NameType, ValueType } from 'recharts/types/component/DefaultTooltipContent'
import { getOverview, overviewKey, type RunPoint } from '../api/overview'
import { getProject, projectKeys } from '../api/projects'
import { RunStatusBadge } from '../components/RunStatusBadge'
import { Alert } from '../components/ui'
import { ApiError } from '../lib/api'
import { errorText } from '../lib/errors'
import { ProjectHeader } from '../components/ProjectHeader'

// Chart tokens (reference data-viz palette, light surface). Series and status colours never colour text.
const C = {
  series: '#2a78d6',
  surface: '#fcfcfb',
  grid: '#e1e0d9',
  axis: '#c3c2b7',
  muted: '#898781',
  critical: '#d03b3b',
  serious: '#ec835a',
  warning: '#fab219',
  neutral: '#a3a29b',
}
const SEVERITY_COLOR = { critical: C.critical, high: C.serious, medium: C.warning, low: C.neutral }

type TipProps = TooltipContentProps<ValueType, NameType>

const shortDate = (iso: string) => new Date(iso).toLocaleDateString([], { day: 'numeric', month: 'short' })
/** Tick labels: the date, or the time of day when everything happened within ~a day. */
function tickFormatter(points: { created_at: string }[]) {
  const times = points.map((p) => Date.parse(p.created_at))
  const sameDay = times.length > 0 && Math.max(...times) - Math.min(...times) < 36 * 3600 * 1000
  return (iso: string) => sameDay
    ? new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : shortDate(iso)
}

const fullDate = (iso: string) => new Date(iso).toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })

function trend(now: number, before: number): string {
  if (now === before) return 'same as the run before'
  return `${now > before ? '▲' : '▼'} from ${before}% the run before`
}

function Tile({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-sm">
      <p className="text-xs font-medium text-slate-600">{label}</p>
      <p className="mt-1 text-2xl font-bold text-slate-900">{value}</p>
      {sub && <p className="text-xs text-slate-500">{sub}</p>}
    </div>
  )
}

function ChartCard({ title, description, children, table }: { title: string; description: string; children: ReactNode; table: ReactNode }) {
  const [showTable, setShowTable] = useState(false)
  return (
    <section aria-label={title} className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
          <p className="text-xs text-slate-600">{description}</p>
        </div>
        <button type="button" onClick={() => setShowTable((v) => !v)} aria-pressed={showTable}
          className="shrink-0 text-xs font-medium text-indigo-600 hover:underline">
          {showTable ? 'Show chart' : 'Show table'}
        </button>
      </div>
      <div className="mt-3">{showTable ? table : children}</div>
    </section>
  )
}

function TipBox({ children }: { children: ReactNode }) {
  return <div className="rounded-md border border-slate-200 bg-white px-3 py-2 text-xs text-slate-800 shadow-md">{children}</div>
}

function PassRateTip({ active, payload }: TipProps) {
  const run = active && payload?.[0]?.payload as RunPoint | undefined
  if (!run) return null
  return (
    <TipBox>
      <p className="font-semibold">{run.pass_rate}% passed</p>
      <p className="text-slate-600">{run.passed} passed · {run.failed} failed · {run.blocked} blocked</p>
      <p className="text-slate-500">{run.kind === 'replay' ? 'Replay' : 'Agent run'}{run.trigger === 'schedule' ? ' (scheduled)' : ''} · {fullDate(run.created_at)}</p>
    </TipBox>
  )
}

function A11yTip({ active, payload }: TipProps) {
  const point = active && payload?.[0]?.payload as { score: number; issues: number; created_at: string } | undefined
  if (!point) return null
  return (
    <TipBox>
      <p className="font-semibold">WCAG score {point.score}/100</p>
      <p className="text-slate-600">{point.issues} issues · {fullDate(point.created_at)}</p>
    </TipBox>
  )
}

function SmallTable({ head, rows }: { head: string[]; rows: ReactNode[][] }) {
  return (
    <div className="max-h-60 overflow-auto">
      <table className="w-full text-left text-xs">
        <thead className="text-slate-600"><tr>{head.map((h) => <th key={h} scope="col" className="py-1 pr-3 font-semibold">{h}</th>)}</tr></thead>
        <tbody className="divide-y divide-slate-100 tabular-nums text-slate-800">
          {rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j} className="py-1 pr-3">{c}</td>)}</tr>)}
        </tbody>
      </table>
    </div>
  )
}

const axisProps = { stroke: C.axis, tick: { fill: C.muted, fontSize: 11 }, tickLine: false }

export function ProjectOverviewPage() {
  const projectId = useParams().projectId ?? ''
  const project = useQuery({ queryKey: projectKeys.detail(projectId), queryFn: () => getProject(projectId) })
  const data = useQuery({ queryKey: overviewKey(projectId), queryFn: () => getOverview(projectId) })

  if (project.error instanceof ApiError && project.error.status === 404) {
    return (
      <>
        <h1 className="text-2xl font-bold tracking-tight text-slate-900">Project not found</h1>
        <p className="mt-2"><Link to="/projects" className="text-indigo-600 hover:underline">Back to projects</Link></p>
      </>
    )
  }

  const header = <ProjectHeader projectId={projectId} tab="overview" />

  if (data.isPending) return <>{header}<p className="mt-6 text-slate-500">Loading overview…</p></>
  if (data.isError) return <>{header}<div className="mt-6"><Alert>{errorText(data.error)}</Alert></div></>
  const o = data.data
  const judged = o.runs.filter((r) => r.pass_rate !== null && r.status === 'completed')
  const latest = judged.at(-1)
  const previous = judged.at(-2)
  const openTotal = Object.values(o.open_bugs).reduce((a, b) => a + b, 0)
  const latestA11y = o.a11y.at(-1)
  const bugBars = (['critical', 'high', 'medium', 'low'] as const).map((s) => ({ severity: s, count: o.open_bugs[s] }))

  if (o.runs.length === 0) {
    return (
      <>
        {header}
        <div className="mt-6 rounded-lg border border-dashed border-slate-300 bg-white p-10 text-center">
          <h2 className="font-semibold text-slate-900">No runs yet</h2>
          <p className="mt-1 text-sm text-slate-600">Start a run to see pass rates, bugs and accessibility scores here.</p>
          <Link to={`/projects/${projectId}/runs/new`} className="mt-4 inline-block rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700">
            Start the first run
          </Link>
        </div>
      </>
    )
  }

  return (
    <>
      {header}
      <section aria-label="Summary" className="mt-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Tile label="Pass rate (last run)" value={latest ? `${latest.pass_rate}%` : '—'}
          sub={latest && previous ? trend(latest.pass_rate!, previous.pass_rate!) : undefined} />
        <Tile label="Open bugs" value={openTotal} sub={<Link to={`/bugs?project=${projectId}`} className="text-indigo-600 hover:underline">view bugs</Link>} />
        <Tile label="Accessibility score" value={latestA11y ? `${latestA11y.score}/100` : '—'} sub={latestA11y ? `${latestA11y.issues} issues` : 'no audit yet'} />
        <Tile label="Saved tests" value={o.saved_tests} sub={`${o.schedules_enabled} schedule${o.schedules_enabled === 1 ? '' : 's'} on`} />
      </section>

      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <ChartCard title="Pass rate per run" description="Share of tests with a verdict that passed (agent runs and replays)"
          table={<SmallTable head={['Run', 'Type', 'Pass rate', 'Passed / failed / blocked']}
            rows={judged.map((r) => [fullDate(r.created_at), r.kind, `${r.pass_rate}%`, `${r.passed} / ${r.failed} / ${r.blocked}`])} />}>
          {judged.length === 0 ? <p className="py-10 text-center text-sm text-slate-500">No finished runs with verdicts yet.</p> : (
            <div className="h-56" role="img" aria-label={`Pass rate over ${judged.length} runs, latest ${latest?.pass_rate}%`}>
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={judged} margin={{ top: 8, right: 12, bottom: 0, left: -12 }}>
                  <CartesianGrid vertical={false} stroke={C.grid} />
                  <XAxis dataKey="created_at" tickFormatter={tickFormatter(judged)} {...axisProps} minTickGap={24} />
                  <YAxis domain={[0, 100]} ticks={[0, 25, 50, 75, 100]} unit="%" {...axisProps} axisLine={false} />
                  <Tooltip content={PassRateTip} cursor={{ stroke: C.axis, strokeWidth: 1 }} />
                  <Line type="linear" dataKey="pass_rate" stroke={C.series} strokeWidth={2} isAnimationActive={false}
                    dot={{ r: 4, fill: C.series, stroke: C.surface, strokeWidth: 2 }} activeDot={{ r: 6, stroke: C.surface, strokeWidth: 2 }} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </ChartCard>

        <ChartCard title="Open bugs by severity" description="Bugs not yet marked fixed or ignored"
          table={<SmallTable head={['Severity', 'Open bugs']} rows={bugBars.map((b) => [b.severity, b.count])} />}>
          <div className="h-56" role="img" aria-label={`Open bugs: ${bugBars.map((b) => `${b.count} ${b.severity}`).join(', ')}`}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={bugBars} margin={{ top: 18, right: 12, bottom: 0, left: -20 }} barCategoryGap="30%">
                <CartesianGrid vertical={false} stroke={C.grid} />
                <XAxis dataKey="severity" {...axisProps} tickFormatter={(s: string) => s[0].toUpperCase() + s.slice(1)} />
                <YAxis allowDecimals={false} {...axisProps} axisLine={false} />
                <Tooltip cursor={{ fill: 'rgba(11,11,11,0.04)' }}
                  content={({ active, payload }: TipProps) => active && payload?.[0]
                    ? <TipBox><p className="font-semibold">{payload[0].value} open {String(payload[0].payload.severity)} bug{payload[0].value === 1 ? '' : 's'}</p></TipBox>
                    : null} />
                <Bar dataKey="count" radius={[4, 4, 0, 0]} isAnimationActive={false}>
                  {bugBars.map((b) => <Cell key={b.severity} fill={SEVERITY_COLOR[b.severity]} />)}
                  <LabelList dataKey="count" position="top" fill="#52514e" fontSize={11} />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>

        <ChartCard title="Accessibility score over time" description="WCAG score of each audit (0–100)"
          table={<SmallTable head={['Audit', 'Score', 'Issues']} rows={o.a11y.map((a) => [fullDate(a.created_at), a.score, a.issues])} />}>
          {o.a11y.length === 0 ? <p className="py-10 text-center text-sm text-slate-500">No accessibility audits yet.</p> : (
            <div className="h-56" role="img" aria-label={`Accessibility score over ${o.a11y.length} audits, latest ${latestA11y?.score}`}>
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={o.a11y} margin={{ top: 8, right: 12, bottom: 0, left: -12 }}>
                  <CartesianGrid vertical={false} stroke={C.grid} />
                  <XAxis dataKey="created_at" tickFormatter={tickFormatter(o.a11y)} {...axisProps} minTickGap={24} />
                  <YAxis domain={[0, 100]} ticks={[0, 25, 50, 75, 100]} {...axisProps} axisLine={false} />
                  <Tooltip content={A11yTip} cursor={{ stroke: C.axis, strokeWidth: 1 }} />
                  <Line type="linear" dataKey="score" stroke={C.series} strokeWidth={2} isAnimationActive={false}
                    dot={{ r: 4, fill: C.series, stroke: C.surface, strokeWidth: 2 }} activeDot={{ r: 6, stroke: C.surface, strokeWidth: 2 }} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </ChartCard>

        <section aria-labelledby="last-runs" className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <h2 id="last-runs" className="text-sm font-semibold text-slate-900">Last runs</h2>
          <ul className="mt-2 divide-y divide-slate-100">
            {[...o.runs].reverse().slice(0, 6).map((r) => (
              <li key={r.id} className="flex flex-wrap items-center gap-x-2 gap-y-1 py-2">
                <Link to={r.status === 'queued' || r.status === 'running' ? `/runs/${r.id}` : `/runs/${r.id}/report`}
                  className="min-w-[12rem] flex-1 truncate text-sm font-medium text-indigo-600 hover:underline">
                  {r.goal}
                </Link>
                <span className="text-xs text-slate-500">
                  {r.kind === 'replay' ? 'replay' : 'agent'}{r.trigger === 'schedule' ? ', scheduled' : ''} · {fullDate(r.created_at)}
                  {r.pass_rate !== null && ` · ${r.pass_rate}%`}
                </span>
                <RunStatusBadge status={r.status} />
              </li>
            ))}
          </ul>
        </section>
      </div>
    </>
  )
}
