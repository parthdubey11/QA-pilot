import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { a11yKeys, getProjectAccessibility, IMPACTS, type A11yIssue, type IssueSource } from '../api/accessibility'
import { ImpactBadge } from '../components/Badges'
import { Alert } from '../components/ui'
import { ApiError } from '../lib/api'
import { errorText } from '../lib/errors'
import { ProjectHeader } from '../components/ProjectHeader'

const SOURCE_LABEL: Record<IssueSource, string> = { axe: 'axe-core', keyboard: 'Keyboard check', vision: 'Vision check' }

function scoreTone(score: number) {
  if (score >= 90) return { text: 'text-emerald-700', ring: 'border-emerald-500', label: 'Good' }
  if (score >= 70) return { text: 'text-amber-800', ring: 'border-amber-500', label: 'Needs work' }
  return { text: 'text-rose-700', ring: 'border-rose-500', label: 'Poor' }
}

function groupBy<T>(items: T[], key: (item: T) => string): [string, T[]][] {
  const groups = new Map<string, T[]>()
  for (const item of items) groups.set(key(item), [...(groups.get(key(item)) ?? []), item])
  return [...groups.entries()]
}

function Elements({ issue }: { issue: A11yIssue }) {
  if (issue.nodes.length === 0) return null
  return (
    <details className="mt-2">
      <summary className="cursor-pointer text-xs font-medium text-indigo-600">
        {issue.nodes.length} affected element{issue.nodes.length === 1 ? '' : 's'} on {issue.page_path}
      </summary>
      <ul className="mt-2 space-y-2">
        {issue.nodes.map((node, i) => (
          <li key={i} className="rounded bg-slate-50 p-2 text-xs">
            <code className="break-all text-slate-800">{node.target}</code>
            {node.html && <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-all text-slate-600">{node.html}</pre>}
            {node.summary && <p className="mt-1 text-slate-700">{node.summary}</p>}
          </li>
        ))}
      </ul>
    </details>
  )
}

function IssueType({ issues }: { issues: A11yIssue[] }) {
  const first = issues[0]
  const pages = [...new Set(issues.map((i) => i.page_path))]
  return (
    <li className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3 className="font-semibold text-slate-900">{first.title}</h3>
        <ImpactBadge impact={first.impact} />
      </div>
      <p className="mt-1 text-xs text-slate-500">
        {SOURCE_LABEL[first.source]} · {first.rule_id}
        {first.wcag.length > 0 && ` · WCAG ${first.wcag.join(', ')}`} · on {pages.length} page{pages.length === 1 ? '' : 's'}: {pages.join(', ')}
      </p>
      <p className="mt-2 text-sm text-slate-700">{first.description}</p>
      <div className="mt-3 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-950">
        <span className="font-semibold">How to fix: </span>
        {first.how_to_fix}
        {first.help_url && (
          <>
            {' '}
            <a href={first.help_url} target="_blank" rel="noreferrer" className="font-medium text-emerald-800 underline">
              Learn more<span className="sr-only"> about {first.rule_id} (opens in a new tab)</span>
            </a>
          </>
        )}
      </div>
      {issues.map((issue) => <Elements key={issue.id} issue={issue} />)}
    </li>
  )
}

function PageIssues({ path, issues }: { path: string; issues: A11yIssue[] }) {
  return (
    <li className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <h3 className="font-semibold text-slate-900">{path}</h3>
      <ul className="mt-2 divide-y divide-slate-100">
        {issues.map((issue) => (
          <li key={issue.id} className="py-3">
            <div className="flex flex-wrap items-center gap-2">
              <ImpactBadge impact={issue.impact} />
              <span className="text-sm font-medium text-slate-900">{issue.title}</span>
              <span className="text-xs text-slate-500">{SOURCE_LABEL[issue.source]}{issue.wcag.length > 0 && ` · WCAG ${issue.wcag.join(', ')}`}</span>
            </div>
            <p className="mt-1 text-sm text-slate-700"><span className="font-semibold">How to fix: </span>{issue.how_to_fix}</p>
            <Elements issue={issue} />
          </li>
        ))}
      </ul>
    </li>
  )
}

export function AccessibilityPage() {
  const projectId = useParams().projectId ?? ''
  const runId = useSearchParams()[0].get('run') ?? undefined
  const [groupMode, setGroupMode] = useState<'type' | 'page'>('type')
  const data = useQuery({ queryKey: a11yKeys.project(projectId, runId), queryFn: () => getProjectAccessibility(projectId, runId) })

  if (data.error instanceof ApiError && data.error.status === 404) {
    return (
      <>
        <h1 className="text-2xl font-bold tracking-tight text-slate-900">Accessibility audit not found</h1>
        <p className="mt-2"><Link to="/projects" className="text-indigo-600 hover:underline">Back to projects</Link></p>
      </>
    )
  }
  if (data.isPending) return <p className="text-slate-500">Loading accessibility results…</p>
  if (data.isError) return <Alert>{errorText(data.error)}</Alert>
  const { audit, issues, history } = data.data

  const heading = (
    <>
      <ProjectHeader projectId={projectId} tab="accessibility" title="Accessibility" />
    </>
  )

  if (!audit) {
    return (
      <>
        {heading}
        <div className="mt-6 rounded-lg border border-dashed border-slate-300 bg-white p-8 text-center">
          <h2 className="font-semibold text-slate-900">No accessibility audit yet</h2>
          <p className="mt-1 text-sm text-slate-600">Start a run with “Accessibility audit” ticked to check every page against WCAG.</p>
          <Link to={`/projects/${projectId}/runs/new`} className="mt-4 inline-block rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700">
            Start a run with the audit
          </Link>
        </div>
      </>
    )
  }

  const tone = scoreTone(audit.score)
  const counts = IMPACTS.map((impact) => [impact, issues.filter((i) => i.impact === impact).length] as const)
  const groups = groupMode === 'type'
    ? groupBy(issues, (i) => i.rule_id)
    : groupBy([...issues].sort((a, b) => a.page_path.localeCompare(b.page_path)), (i) => i.page_path)

  return (
    <>
      {heading}
      <section aria-label="Score" className="mt-6 flex flex-wrap items-center gap-6 rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
        <div className={`flex h-28 w-28 shrink-0 flex-col items-center justify-center rounded-full border-8 ${tone.ring}`}>
          <span className={`text-3xl font-bold tabular-nums ${tone.text}`}>{audit.score}</span>
          <span className="text-xs text-slate-600">/ 100</span>
        </div>
        <div className="min-w-0 flex-1">
          <p className={`text-lg font-semibold ${tone.text}`}>WCAG score: {tone.label}</p>
          <p className="text-sm text-slate-600">
            {audit.issue_count} issue{audit.issue_count === 1 ? '' : 's'} on {audit.pages.filter((p) => !p.error).length} page(s) ·
            audited {new Date(audit.created_at).toLocaleString()} ·{' '}
            <Link to={`/runs/${audit.run_id}/report`} className="text-indigo-600 hover:underline">run report</Link>
          </p>
          <ul className="mt-2 flex flex-wrap gap-3 text-sm" aria-label="Issues by impact">
            {counts.map(([impact, n]) => (
              <li key={impact} className="flex items-center gap-1.5"><ImpactBadge impact={impact} /> {n}</li>
            ))}
          </ul>
          {!audit.llm_checks && (
            <p className="mt-2 text-xs text-amber-800">The keyboard review and alt-text check ran without the AI model (limit reached); some issues may be missing.</p>
          )}
        </div>
        {history.length > 1 && (
          <div aria-label="Score history" className="flex items-end gap-1" role="img">
            {history.map((h) => (
              <Link
                key={h.run_id}
                to={`/projects/${projectId}/accessibility?run=${h.run_id}`}
                title={`${h.score}/100 on ${new Date(h.created_at).toLocaleDateString()}`}
                aria-label={`Score ${h.score} on ${new Date(h.created_at).toLocaleDateString()}`}
                className={`w-3 rounded-t ${h.run_id === audit.run_id ? 'bg-indigo-600' : 'bg-slate-300 hover:bg-slate-400'}`}
                style={{ height: `${Math.max(6, h.score * 0.6)}px` }}
              />
            ))}
          </div>
        )}
      </section>

      <section aria-labelledby="pages-heading" className="mt-6">
        <h2 id="pages-heading" className="text-lg font-semibold text-slate-900">Pages</h2>
        <table className="mt-3 w-full overflow-hidden rounded-lg border border-slate-200 bg-white text-left text-sm shadow-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-600">
            <tr><th scope="col" className="px-4 py-2">Page</th><th scope="col" className="px-4 py-2">Score</th><th scope="col" className="px-4 py-2">Issues</th></tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {audit.pages.map((p) => (
              <tr key={p.path}>
                <td className="px-4 py-2">
                  <span className="font-medium text-slate-900">{p.path}</span>
                  {p.same_as.length > 0 && <span className="block text-xs text-slate-500">also covers {p.same_as.join(', ')}</span>}
                </td>
                <td className={`px-4 py-2 font-semibold tabular-nums ${p.error ? 'text-slate-500' : scoreTone(p.score).text}`}>{p.error ? '—' : p.score}</td>
                <td className="px-4 py-2 text-slate-700">{p.error ? `Could not open: ${p.error}` : p.issues}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section aria-labelledby="issues-heading" className="mt-8">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 id="issues-heading" className="text-lg font-semibold text-slate-900">Issues</h2>
          <fieldset className="flex items-center gap-1 rounded-md border border-slate-300 bg-white p-1 text-sm">
            <legend className="sr-only">Group issues by</legend>
            {(['type', 'page'] as const).map((mode) => (
              <label key={mode} className={`cursor-pointer rounded px-3 py-1 has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-indigo-600 ${groupMode === mode ? 'bg-indigo-600 text-white' : 'text-slate-700 hover:bg-slate-100'}`}>
                <input type="radio" name="group" value={mode} checked={groupMode === mode} onChange={() => setGroupMode(mode)} className="sr-only" />
                By {mode}
              </label>
            ))}
          </fieldset>
        </div>
        {issues.length === 0 ? (
          <p className="mt-3 text-sm text-slate-600">No accessibility issues found. 🎉</p>
        ) : (
          <ul className="mt-3 space-y-3" aria-label={groupMode === 'type' ? 'Issues by type' : 'Issues by page'}>
            {groups.map(([key, group]) =>
              groupMode === 'type' ? <IssueType key={key} issues={group} /> : <PageIssues key={key} path={key} issues={group} />,
            )}
          </ul>
        )}
      </section>
    </>
  )
}
