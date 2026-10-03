import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { bugKeys, bugScreenshotPath, getBug, updateBugStatus, type BugStatus } from '../api/bugs'
import { AuthImage } from '../components/AuthImage'
import { BugStatusBadge, SeverityBadge } from '../components/Badges'
import { Alert, Button, Card } from '../components/ui'
import { ApiError } from '../lib/api'
import { errorText } from '../lib/errors'

const ACTIONS: { status: BugStatus; label: string; variant: 'primary' | 'secondary' }[] = [
  { status: 'fixed', label: 'Mark fixed', variant: 'primary' },
  { status: 'ignored', label: 'Ignore', variant: 'secondary' },
  { status: 'open', label: 'Reopen', variant: 'secondary' },
]

export function BugDetailPage() {
  const bugId = useParams().bugId ?? ''
  const queryClient = useQueryClient()
  const bug = useQuery({ queryKey: bugKeys.detail(bugId), queryFn: () => getBug(bugId) })
  const setStatus = useMutation({
    mutationFn: (status: BugStatus) => updateBugStatus(bugId, status),
    onSuccess: (updated) => {
      queryClient.setQueryData(bugKeys.detail(bugId), updated)
      queryClient.invalidateQueries({ queryKey: bugKeys.all })
    },
  })

  if (bug.error instanceof ApiError && bug.error.status === 404) {
    return (
      <>
        <h1 className="text-2xl font-bold tracking-tight text-slate-900">Bug not found</h1>
        <p className="mt-2 text-slate-600"><Link to="/bugs" className="text-indigo-600 hover:underline">Back to bugs</Link></p>
      </>
    )
  }
  if (bug.isPending) return <p className="text-slate-500">Loading bug…</p>
  if (bug.isError) return <Alert>{errorText(bug.error)}</Alert>
  const b = bug.data

  return (
    <>
      <p className="text-sm">
        <Link to="/bugs" className="text-indigo-600 hover:underline">Bugs</Link>
        <span className="text-slate-400"> / </span>
        <span className="text-slate-600">{b.project_name}</span>
      </p>
      <div className="mt-1 flex flex-wrap items-start justify-between gap-3">
        <h1 className="text-2xl font-bold tracking-tight text-slate-900">{b.title}</h1>
        <div className="flex items-center gap-2">
          <SeverityBadge severity={b.severity} />
          <BugStatusBadge status={b.status} />
        </div>
      </div>
      <p className="mt-1 text-sm text-slate-600">
        Seen {b.occurrences}× in {b.run_ids.length} run{b.run_ids.length === 1 ? '' : 's'} · first{' '}
        {new Date(b.first_seen_at).toLocaleString()} · last {new Date(b.last_seen_at).toLocaleString()}
      </p>
      {b.reopened && (
        <p role="status" className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
          This bug was marked fixed, but a later run found it again.
        </p>
      )}

      <div className="mt-4 flex flex-wrap gap-2" role="group" aria-label="Change status">
        {ACTIONS.filter((a) => a.status !== b.status).map((a) => (
          <Button key={a.status} variant={a.variant} disabled={setStatus.isPending} onClick={() => setStatus.mutate(a.status)}>
            {a.label}
          </Button>
        ))}
      </div>
      <div className="mt-2"><Alert>{setStatus.isError ? errorText(setStatus.error) : ''}</Alert></div>

      <div className="mt-6 grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="space-y-6">
          <Card title="Steps to reproduce">
            <ol className="list-decimal space-y-1 pl-5 text-sm text-slate-800">
              {b.steps.map((step, i) => <li key={i}>{step}</li>)}
            </ol>
            {b.url && <p className="mt-3 break-all text-xs text-slate-500">Page: {b.url}</p>}
          </Card>
          <Card title="Expected vs actual">
            <dl className="space-y-3 text-sm">
              <div><dt className="font-semibold text-slate-700">Expected</dt><dd className="text-slate-800">{b.expected}</dd></div>
              <div><dt className="font-semibold text-slate-700">Actual</dt><dd className="text-slate-800">{b.actual}</dd></div>
            </dl>
          </Card>
          <Card title="Suggested fix">
            <p className="text-sm text-slate-800">{b.suggested_fix}</p>
          </Card>
          <Card title="Found in">
            <p className="text-sm text-slate-600">First found by the test “{b.first_test_title}”.</p>
            <ul className="mt-2 space-y-1 text-sm">
              {[...b.run_ids].reverse().map((runId) => (
                <li key={runId}>
                  <Link to={`/runs/${runId}/report`} className="text-indigo-600 hover:underline">Run report {runId.slice(-6)}</Link>
                </li>
              ))}
            </ul>
          </Card>
        </div>
        <Card title="Screenshot">
          {b.has_screenshot ? (
            <AuthImage key={b.id} path={bugScreenshotPath(b.id)} alt={`Screenshot of: ${b.title}`} className="w-full rounded border border-slate-200" />
          ) : (
            <p className="text-sm text-slate-500">No screenshot.</p>
          )}
        </Card>
      </div>
    </>
  )
}
