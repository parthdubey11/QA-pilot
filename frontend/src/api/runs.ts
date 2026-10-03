import { apiFetch } from '../lib/api'

export type RunStatus = 'queued' | 'running' | 'completed' | 'failed'
export type StepKind = 'info' | 'thought' | 'action' | 'observation' | 'error'
export type StepPhase = 'explore' | 'audit' | 'plan' | 'execute' | 'judge' | 'report' | 'replay' | 'heal'
export type TestStatus = 'pending' | 'running' | 'passed' | 'failed' | 'blocked' | 'error'

export type RunOptions = { accessibility: boolean; mobile_viewport: boolean; max_tests: number }

export type Run = {
  id: string
  project_id: string
  project_name: string
  goal: string
  options: RunOptions
  kind: 'agent' | 'replay'
  trigger: 'manual' | 'schedule'
  status: RunStatus
  error: string | null
  stats: Record<string, unknown>
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export type RunStep = {
  index: number
  kind: StepKind
  phase: StepPhase | null
  test_case_index: number | null
  message: string
  action: Record<string, unknown> | null
  url: string | null
  snapshot: string | null
  has_screenshot: boolean
  created_at: string
}

export type TestCase = {
  index: number
  title: string
  type: 'happy' | 'edge'
  start_path: string
  steps: string[]
  expected: string
  status: TestStatus
  reason: string | null
  steps_used: number
  final_url: string | null
  has_final_screenshot: boolean
  bug_id: string | null
  started_at: string | null
  finished_at: string | null
  updated_at: string
}

export const ACTIVE_STATUSES: RunStatus[] = ['queued', 'running']

export const runKeys = {
  forProject: (projectId: string) => ['projects', projectId, 'runs'] as const,
  detail: (runId: string) => ['runs', runId] as const,
  testCases: (runId: string) => ['runs', runId, 'test-cases'] as const,
  steps: (runId: string) => ['runs', runId, 'steps'] as const,
}

export const createRun = (projectId: string, body: { goal: string; options: RunOptions }) =>
  apiFetch<Run>(`/projects/${projectId}/runs`, { method: 'POST', body })
export const listRuns = (projectId: string) => apiFetch<Run[]>(`/projects/${projectId}/runs`)
export const getRun = (runId: string) => apiFetch<Run>(`/runs/${runId}`)
export const screenshotPath = (runId: string, index: number) => `/runs/${runId}/steps/${index}/screenshot`
export const testCaseScreenshotPath = (runId: string, index: number) => `/runs/${runId}/test-cases/${index}/screenshot`
export const listTestCases = (runId: string) => apiFetch<TestCase[]>(`/runs/${runId}/test-cases`)
export const listSteps = (runId: string) => apiFetch<RunStep[]>(`/runs/${runId}/steps`)
