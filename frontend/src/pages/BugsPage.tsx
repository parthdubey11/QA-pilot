import { useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import {
  BUG_STATUSES,
  SEVERITIES,
  bugKeys,
  bugScreenshotPath,
  listBugs,
  type BugFilters,
  type BugStatus,
  type Severity,
} from '../api/bugs'
import { listProjects, projectKeys } from '../api/projects'
import { AuthImage } from '../components/AuthImage'
import { BugStatusBadge, SeverityBadge } from '../components/Badges'
import { Alert } from '../components/ui'
import { errorText } from '../lib/errors'

const selectClass = 'rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm shadow-sm'

export function BugsPage() {
  const [params, setParams] = useSearchParams()
  const severity = params.getAll('severity') as Severity[]
  const status = (params.has('status') ? params.get('status') : 'open') as BugStatus | ''
  const projectId = params.get('project') ?? ''
  const filters: BugFilters = {
    severity: severity.length ? severity : undefined,
    status: status ? [status] : undefined,
    project_id: projectId || undefined,
  }
  const bugs = useQuery({ queryKey: bugKeys.list(filters), queryFn: () => listBugs(filters) })
  const projects = useQuery({ queryKey: projectKeys.all, queryFn: listProjects })

  function update(change: (p: URLSearchParams) => void) {
    const next = new URLSearchParams(params)
    change(next)
    setParams(next, { replace: true })
  }

  function toggleSeverity(value: Severity) {
    update((p) => {
      const current = p.getAll('severity')
      p.delete('severity')
      const next = current.includes(value) ? current.filter((s) => s !== value) : [...current, value]
      next.forEach((s) => p.append('severity', s))
    })
  }

  return (
    <>
      <h1 className="text-2xl font-bold tracking-tight text-slate-900">Bugs</h1>
      <p className="mt-1 text-sm text-slate-600">Found by QA Pilot in your test runs. The same bug seen again is counted, not duplicated.</p>

      <div className="mt-6 flex flex-wrap items-center gap-x-6 gap-y-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <fieldset className="flex flex-wrap items-center gap-2">
          <legend className="sr-only">Severity</legend>
          <span className="text-sm font-medium text-slate-700" aria-hidden="true">Severity</span>
          {SEVERITIES.map((s) => (
            <label
              key={s}
              className={`cursor-pointer rounded-full border px-3 py-1 text-xs font-semibold capitalize has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-indigo-600 ${severity.includes(s) ? 'border-indigo-600 bg-indigo-50 text-indigo-800' : 'border-slate-300 text-slate-700 hover:bg-slate-50'}`}
            >
              <input type="checkbox" className="sr-only" checked={severity.includes(s)} onChange={() => toggleSeverity(s)} />
              {s}
            </label>
          ))}
        </fieldset>
        <div className="flex items-center gap-2">
          <label htmlFor="bug-status" className="text-sm font-medium text-slate-700">Status</label>
          <select id="bug-status" value={status} onChange={(e) => update((p) => p.set('status', e.target.value))} className={selectClass}>
            <option value="">All</option>
            {BUG_STATUSES.map((s) => (
              <option key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-2">
          <label htmlFor="bug-project" className="text-sm font-medium text-slate-700">Project</label>
          <select
            id="bug-project"
            value={projectId}
            onChange={(e) => update((p) => (e.target.value ? p.set('project', e.target.value) : p.delete('project')))}
            className={selectClass}
          >
            <option value="">All projects</option>
            {projects.data?.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </div>
      </div>

      <div className="mt-6">
        {bugs.isPending && <p className="text-slate-500">Loading bugs…</p>}
        {bugs.isError && <Alert>{errorText(bugs.error)}</Alert>}
        {bugs.data?.length === 0 && (
          <p className="rounded-lg border border-dashed border-slate-300 bg-white p-8 text-center text-sm text-slate-600">
            No bugs match these filters.
          </p>
        )}
        {bugs.data && bugs.data.length > 0 && (
          <ul aria-label="Bugs" className="divide-y divide-slate-200 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
            {bugs.data.map((bug) => (
              <li key={bug.id} className="flex flex-wrap items-center gap-4 px-4 py-3">
                <Link to={`/bugs/${bug.id}`} tabIndex={-1} aria-hidden="true" className="w-32 shrink-0">
                  {bug.has_screenshot ? (
                    <AuthImage
                      key={bug.id}
                      path={bugScreenshotPath(bug.id)}
                      alt=""
                      className="aspect-[16/10] w-32 rounded border border-slate-200 object-cover object-top"
                    />
                  ) : (
                    <span className="flex aspect-[16/10] w-32 items-center justify-center rounded border border-dashed border-slate-300 text-xs text-slate-500">
                      No screenshot
                    </span>
                  )}
                </Link>
                <div className="min-w-0 flex-1">
                  <SeverityBadge severity={bug.severity} />
                  <Link to={`/bugs/${bug.id}`} className="mt-1 block font-medium text-slate-900 hover:text-indigo-700 hover:underline">
                    {bug.title}
                  </Link>
                  <p className="text-xs text-slate-500">
                    {bug.project_name} · seen {bug.occurrences}× · last {new Date(bug.last_seen_at).toLocaleString()}
                    {bug.reopened && <span className="font-semibold text-rose-700"> · reopened</span>}
                  </p>
                </div>
                <BugStatusBadge status={bug.status} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  )
}
