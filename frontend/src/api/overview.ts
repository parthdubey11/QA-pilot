import { apiFetch } from '../lib/api'
import type { RunStatus } from './runs'

export type RunPoint = {
  id: string
  created_at: string
  kind: 'agent' | 'replay'
  trigger: 'manual' | 'schedule'
  status: RunStatus
  goal: string
  tests: number
  passed: number
  failed: number
  blocked: number
  pass_rate: number | null
  bugs_new: number
  a11y_score: number | null
  seconds: number | null
}

export type ProjectOverview = {
  runs: RunPoint[]
  open_bugs: Record<'critical' | 'high' | 'medium' | 'low', number>
  a11y: { run_id: string; created_at: string; score: number; issues: number }[]
  saved_tests: number
  schedules_enabled: number
}

export const overviewKey = (projectId: string) => ['overview', projectId] as const
export const getOverview = (projectId: string) => apiFetch<ProjectOverview>(`/projects/${projectId}/overview`)
