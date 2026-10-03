import type { Impact } from '../api/accessibility'
import type { BugStatus, Severity } from '../api/bugs'
import type { TestStatus } from '../api/runs'

const base = 'inline-flex shrink-0 items-center rounded-full px-2.5 py-0.5 text-xs font-semibold capitalize'

const severityStyles: Record<Severity, string> = {
  critical: 'bg-rose-700 text-white',
  high: 'bg-rose-100 text-rose-800',
  medium: 'bg-amber-100 text-amber-900',
  low: 'bg-slate-100 text-slate-700',
}

const bugStatusStyles: Record<BugStatus, string> = {
  open: 'bg-sky-100 text-sky-800',
  fixed: 'bg-emerald-100 text-emerald-800',
  ignored: 'bg-slate-200 text-slate-700',
}

const testStatusStyles: Record<TestStatus, string> = {
  pending: 'bg-slate-100 text-slate-600',
  running: 'bg-sky-100 text-sky-800',
  passed: 'bg-emerald-100 text-emerald-800',
  failed: 'bg-rose-100 text-rose-800',
  blocked: 'bg-amber-100 text-amber-900',
  error: 'bg-slate-200 text-slate-800',
}

export function SeverityBadge({ severity }: { severity: Severity }) {
  return <span className={`${base} ${severityStyles[severity]}`}>{severity}</span>
}

export function BugStatusBadge({ status }: { status: BugStatus }) {
  return <span className={`${base} ${bugStatusStyles[status]}`}>{status}</span>
}

export function TestStatusBadge({ status }: { status: TestStatus }) {
  return <span className={`${base} ${testStatusStyles[status]}`}>{status}</span>
}

const impactStyles: Record<Impact, string> = {
  critical: 'bg-rose-700 text-white',
  serious: 'bg-rose-100 text-rose-800',
  moderate: 'bg-amber-100 text-amber-900',
  minor: 'bg-slate-100 text-slate-700',
}

export function ImpactBadge({ impact }: { impact: Impact }) {
  return <span className={`${base} ${impactStyles[impact]}`}>{impact}</span>
}
