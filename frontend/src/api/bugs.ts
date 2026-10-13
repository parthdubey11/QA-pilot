import { apiFetch } from '../lib/api'

export type Severity = 'critical' | 'high' | 'medium' | 'low'
export type BugStatus = 'open' | 'fixed' | 'ignored'

export const SEVERITIES: Severity[] = ['critical', 'high', 'medium', 'low']
export const BUG_STATUSES: BugStatus[] = ['open', 'fixed', 'ignored']

export type Bug = {
  id: string
  project_id: string
  project_name: string
  title: string
  severity: Severity
  status: BugStatus
  steps: string[]
  expected: string
  actual: string
  suggested_fix: string
  url: string | null
  has_screenshot: boolean
  occurrences: number
  run_ids: string[]
  first_run_id: string
  first_test_title: string
  reopened: boolean
  first_seen_at: string
  last_seen_at: string
  updated_at: string
}

export type BugFilters = { severity?: Severity[]; status?: BugStatus[]; project_id?: string; run_id?: string }

export const bugKeys = {
  list: (filters: BugFilters) => ['bugs', filters] as const,
  detail: (id: string) => ['bugs', 'detail', id] as const,
  similar: (id: string) => ['bugs', 'similar', id] as const,
  all: ['bugs'] as const,
}

export function bugQuery(filters: BugFilters): string {
  const params = new URLSearchParams()
  filters.severity?.forEach((s) => params.append('severity', s))
  filters.status?.forEach((s) => params.append('status', s))
  if (filters.project_id) params.set('project_id', filters.project_id)
  if (filters.run_id) params.set('run_id', filters.run_id)
  const query = params.toString()
  return query ? `?${query}` : ''
}

export const listBugs = (filters: BugFilters = {}) => apiFetch<Bug[]>(`/bugs${bugQuery(filters)}`)
export const getBug = (id: string) => apiFetch<Bug>(`/bugs/${id}`)
export const updateBugStatus = (id: string, status: BugStatus) =>
  apiFetch<Bug>(`/bugs/${id}`, { method: 'PATCH', body: { status } })
export const bugScreenshotPath = (id: string) => `/bugs/${id}/screenshot`

export type SimilarBug = {
  id: string
  title: string
  severity: Severity
  status: BugStatus
  occurrences: number
  last_seen_at: string
  similarity: number
}

/** Bugs of the same project that read most like this one (vector search in the RAG memory, no LLM call). */
export const similarBugs = (id: string) => apiFetch<SimilarBug[]>(`/bugs/${id}/similar`)
