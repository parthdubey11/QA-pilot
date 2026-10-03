import type { RunStatus } from '../api/runs'

const styles: Record<RunStatus, string> = {
  queued: 'bg-slate-100 text-slate-700',
  running: 'bg-sky-100 text-sky-800',
  completed: 'bg-emerald-100 text-emerald-800',
  failed: 'bg-rose-100 text-rose-800',
}

const labels: Record<RunStatus, string> = {
  queued: 'Queued',
  running: 'Running',
  completed: 'Completed',
  failed: 'Failed',
}

export function RunStatusBadge({ status }: { status: RunStatus }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold ${styles[status]}`}>
      {status === 'running' && <span aria-hidden="true" className="h-1.5 w-1.5 animate-pulse rounded-full bg-sky-600" />}
      {labels[status]}
    </span>
  )
}
