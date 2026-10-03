import { apiFetch } from '../lib/api'
import type { Run } from './runs'

export type Locators = {
  role: string | null
  name: string | null
  text: string | null
  css: string | null
  tag: string | null
  x: number | null
  y: number | null
}

export type SavedStep = {
  tool: 'goto' | 'click' | 'type' | 'select' | 'press' | 'back' | 'scroll' | 'wait'
  locators: Locators | null
  url: string | null
  text: string | null
  value: string | null
  key: string | null
  ms: number | null
  direction: string | null
  note: string
}

export type HealEvent = {
  at: string
  run_id: string
  step_index: number
  method: 'fallback-locator' | 'ai-healer'
  old: Locators
  new: Locators
  reason: string
}

export type SavedTest = {
  id: string
  project_id: string
  title: string
  start_path: string
  steps: SavedStep[]
  assertions: { kind: 'text' | 'url'; value: string }[]
  expected: string
  source_run_id: string
  last_result: 'passed' | 'failed' | 'never'
  last_run_id: string | null
  last_run_at: string | null
  replays: number
  heal_history: HealEvent[]
  created_at: string
  updated_at: string
}

export type Schedule = {
  id: string
  project_id: string
  name: string
  cron: string
  timezone: string
  enabled: boolean
  next_run_at: string | null
  last_triggered_at: string | null
  last_run_id: string | null
  last_skip_reason: string | null
  created_at: string
}

export type AppNotification = {
  id: string
  project_id: string
  run_id: string | null
  kind: 'new_failures' | 'run_failed'
  title: string
  body: string
  read: boolean
  created_at: string
}

export const savedTestKeys = {
  list: (projectId: string) => ['saved-tests', projectId] as const,
  schedules: (projectId: string) => ['schedules', projectId] as const,
  notifications: ['notifications'] as const,
}

export function describeLocators(l: Locators | null): string {
  if (!l) return ''
  if (l.role && l.name) return `${l.role} "${l.name}"`
  return l.text ?? l.css ?? `<${l.tag ?? '?'}>`
}

export function describeStep(s: SavedStep): string {
  const target = describeLocators(s.locators)
  switch (s.tool) {
    case 'goto': return `Open ${s.url}`
    case 'type': return `Type "${s.text}" into ${target}`
    case 'select': return `Select "${s.value}" in ${target}`
    case 'click': return `Click ${target}`
    case 'press': return `Press ${s.key}`
    case 'wait': return `Wait ${s.ms} ms`
    case 'scroll': return `Scroll ${s.direction ?? 'down'}`
    default: return 'Go back'
  }
}

export const listSavedTests = (projectId: string) => apiFetch<SavedTest[]>(`/projects/${projectId}/saved-tests`)
export const deleteSavedTest = (id: string) => apiFetch<void>(`/saved-tests/${id}`, { method: 'DELETE' })
export const replaySavedTests = (projectId: string, savedTestIds: string[] = []) =>
  apiFetch<Run>(`/projects/${projectId}/replays`, { method: 'POST', body: { saved_test_ids: savedTestIds } })
export const exportPath = (id: string) => `/saved-tests/${id}/export.spec.ts`

export const listSchedules = (projectId: string) => apiFetch<Schedule[]>(`/projects/${projectId}/schedules`)
export const createSchedule = (projectId: string, body: { name: string; cron: string; timezone: string; enabled: boolean }) =>
  apiFetch<Schedule>(`/projects/${projectId}/schedules`, { method: 'POST', body })
export const updateSchedule = (id: string, body: Partial<Pick<Schedule, 'name' | 'cron' | 'timezone' | 'enabled'>>) =>
  apiFetch<Schedule>(`/schedules/${id}`, { method: 'PATCH', body })
export const deleteSchedule = (id: string) => apiFetch<void>(`/schedules/${id}`, { method: 'DELETE' })

export const listNotifications = () => apiFetch<{ unread: number; items: AppNotification[] }>('/notifications')
export const markNotificationRead = (id: string) => apiFetch<void>(`/notifications/${id}/read`, { method: 'POST' })
export const markAllNotificationsRead = () => apiFetch<void>('/notifications/read-all', { method: 'POST' })
