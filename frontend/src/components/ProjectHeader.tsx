import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { getProject, projectKeys } from '../api/projects'

export type ProjectTab = 'overview' | 'tests' | 'accessibility' | 'schedules' | 'settings' | null

const TABS: { id: Exclude<ProjectTab, null>; label: string; path: string }[] = [
  { id: 'overview', label: 'Overview', path: '' },
  { id: 'tests', label: 'Saved tests', path: '/tests' },
  { id: 'accessibility', label: 'Accessibility', path: '/accessibility' },
  { id: 'schedules', label: 'Schedules', path: '/schedules' },
  { id: 'settings', label: 'Settings', path: '/settings' },
]

/** Two-letter avatar text: "Demo Shop" -> "DS", "shop" -> "SH". */
function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean)
  if (words.length === 0) return '?'
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase()
  return (words[0][0] + words[1][0]).toUpperCase()
}

function hostOf(url: string): string {
  try {
    const u = new URL(url)
    return u.host + (u.pathname === '/' ? '' : u.pathname)
  } catch {
    return url
  }
}

export function ProjectAvatar({ name, size = 'lg' }: { name: string; size?: 'md' | 'lg' }) {
  return (
    <div
      aria-hidden="true"
      className={`flex shrink-0 items-center justify-center bg-gradient-to-br from-indigo-600 to-violet-600 font-bold text-white shadow-sm ${
        size === 'lg' ? 'h-12 w-12 rounded-xl text-lg' : 'h-10 w-10 rounded-lg text-sm'
      }`}
    >
      {name ? initials(name) : ''}
    </div>
  )
}

export function Breadcrumbs({ items }: { items: { label: ReactNode; to?: string }[] }) {
  return (
    <nav aria-label="Breadcrumb">
      <ol className="flex flex-wrap items-center gap-1 text-sm">
        {items.map((item, i) => (
          <li key={i} className="flex min-w-0 items-center gap-1">
            {i > 0 && (
              <svg aria-hidden="true" viewBox="0 0 20 20" className="h-4 w-4 shrink-0 text-slate-400" fill="currentColor">
                <path fillRule="evenodd" d="M7.2 14.8a.75.75 0 0 1 0-1.06L10.94 10 7.2 6.26a.75.75 0 1 1 1.06-1.06l4.27 4.27a.75.75 0 0 1 0 1.06L8.26 14.8a.75.75 0 0 1-1.06 0Z" clipRule="evenodd" />
              </svg>
            )}
            {item.to ? (
              <Link to={item.to} className="truncate rounded px-1 font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-900">
                {item.label}
              </Link>
            ) : (
              <span aria-current="page" className="truncate px-1 font-medium text-slate-900">{item.label}</span>
            )}
          </li>
        ))}
      </ol>
    </nav>
  )
}

/**
 * The header every project page shares: breadcrumb, project identity card with the primary action, and tabs.
 * `title` names the current sub-page in the breadcrumb (e.g. "New run"); the tab shows where you are.
 */
export function ProjectHeader({
  projectId,
  tab,
  title,
  actions,
}: {
  projectId: string
  tab: ProjectTab
  title?: string
  actions?: ReactNode
}) {
  const project = useQuery({ queryKey: projectKeys.detail(projectId), queryFn: () => getProject(projectId), enabled: projectId !== '' })
  const p = project.data
  const name = p?.name ?? '…'
  const base = `/projects/${projectId}`

  return (
    <header className="mb-6">
      <Breadcrumbs items={[{ label: 'Projects', to: '/projects' }, { label: name, to: title ? base : undefined }, ...(title ? [{ label: title }] : [])]} />

      <div className="mt-3 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        <div className="h-1.5 bg-gradient-to-r from-indigo-500 via-violet-500 to-sky-400" aria-hidden="true" />
        <div className="flex flex-wrap items-center gap-4 px-5 pb-2 pt-4">
          <ProjectAvatar name={p?.name ?? ''} />
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-xl font-bold tracking-tight text-slate-900 sm:text-2xl">{name}</h1>
            {p && (
              <p className="mt-0.5 flex items-center gap-1.5 text-sm text-slate-500">
                <svg aria-hidden="true" viewBox="0 0 20 20" className="h-4 w-4 shrink-0" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <circle cx="10" cy="10" r="7.25" />
                  <path d="M2.75 10h14.5M10 2.75c2 2.2 2.9 4.6 2.9 7.25S12 15.05 10 17.25C8 15.05 7.1 12.65 7.1 10S8 4.95 10 2.75Z" />
                </svg>
                <span className="truncate">{hostOf(p.base_url)}</span>
                {p.is_own_site && (
                  <span className="ml-1 rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700 ring-1 ring-emerald-600/20">
                    Own site
                  </span>
                )}
              </p>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {actions}
            <Link
              to={`${base}/runs/new`}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3.5 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
            >
              <svg aria-hidden="true" viewBox="0 0 20 20" className="h-4 w-4" fill="currentColor">
                <path d="M6.3 2.84A1.5 1.5 0 0 0 4 4.11v11.78a1.5 1.5 0 0 0 2.3 1.27l9.34-5.89a1.5 1.5 0 0 0 0-2.54L6.3 2.84Z" />
              </svg>
              New run
            </Link>
          </div>
        </div>
        <nav aria-label="Project" className="-mb-px flex gap-1 overflow-x-auto px-3">
          {TABS.map((t) => (
            <Link
              key={t.id}
              to={base + t.path}
              aria-current={tab === t.id ? 'page' : undefined}
              className={`whitespace-nowrap border-b-2 px-3 py-2.5 text-sm font-medium transition-colors ${
                tab === t.id ? 'border-indigo-600 text-indigo-700' : 'border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-800'
              }`}
            >
              {t.label}
            </Link>
          ))}
        </nav>
      </div>
    </header>
  )
}
