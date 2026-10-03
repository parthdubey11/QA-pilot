import { apiFetch } from '../lib/api'

export type Impact = 'critical' | 'serious' | 'moderate' | 'minor'
export type IssueSource = 'axe' | 'keyboard' | 'vision'

export type A11yIssue = {
  id: string
  page_path: string
  page_url: string
  rule_id: string
  source: IssueSource
  impact: Impact
  title: string
  description: string
  how_to_fix: string
  wcag: string[]
  help_url: string | null
  nodes: { target: string; html: string; summary: string }[]
}

export type A11yPageScore = {
  path: string
  url: string
  title: string
  score: number
  issues: number
  same_as: string[]
  error: string | null
}

export type ProjectAccessibility = {
  audit: {
    run_id: string
    score: number
    pages: A11yPageScore[]
    issue_count: number
    llm_checks: boolean
    created_at: string
  } | null
  issues: A11yIssue[]
  history: { run_id: string; score: number; issue_count: number; created_at: string }[]
}

export const IMPACTS: Impact[] = ['critical', 'serious', 'moderate', 'minor']

export const a11yKeys = { project: (projectId: string, runId?: string) => ['accessibility', projectId, runId ?? 'latest'] as const }

export const getProjectAccessibility = (projectId: string, runId?: string) =>
  apiFetch<ProjectAccessibility>(`/projects/${projectId}/accessibility${runId ? `?run_id=${runId}` : ''}`)
