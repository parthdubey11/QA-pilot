import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { tokens } from './lib/api'
import { aliceUser, health, mockApi, project, renderApp } from './test/utils'

const PID = '66f7a1c2e4b0a1b2c3d4e5f6'

beforeEach(() => tokens.set({ access_token: 'access-1', refresh_token: 'refresh-1' }))
afterEach(() => {
  tokens.clear()
  localStorage.clear()
  vi.unstubAllGlobals()
})

const base = {
  'GET /api/auth/me': () => ({ json: aliceUser }),
  'GET /api/health': () => ({ json: health }),
  'GET /api/notifications': () => ({ json: { unread: 0, items: [] } }),
  [`GET /api/projects/${PID}`]: () => ({ json: project() }),
}

const runPoint = (id: string, created_at: string, pass_rate: number | null, extra: Record<string, unknown> = {}) => ({
  id, created_at, kind: 'agent', trigger: 'manual', status: 'completed', goal: `Run ${id}`, tests: 4, passed: 2,
  failed: 1, blocked: 1, pass_rate, bugs_new: 0, a11y_score: null, seconds: 300, ...extra,
})

describe('project overview', () => {
  it('shows summary tiles, chart data tables and the last runs', { timeout: 30000 }, async () => {
    mockApi({
      ...base,
      [`GET /api/projects/${PID}/overview`]: () => ({
        json: {
          runs: [
            runPoint('r1', '2026-09-28T10:00:00Z', 50),
            runPoint('r2', '2026-09-29T10:00:00Z', 75, { kind: 'replay', trigger: 'schedule', passed: 3, failed: 1, blocked: 0 }),
            runPoint('r3', '2026-09-30T10:00:00Z', null, { status: 'running' }),
          ],
          open_bugs: { critical: 1, high: 2, medium: 0, low: 0 },
          a11y: [{ run_id: 'r1', created_at: '2026-09-28T10:00:00Z', score: 68, issues: 18 }],
          saved_tests: 3,
          schedules_enabled: 1,
        },
      }),
    })
    renderApp(`/projects/${PID}`)
    const user = userEvent.setup()

    const summary = await screen.findByRole('region', { name: 'Summary' }, { timeout: 5000 }) // page is lazy-loaded
    expect(within(summary).getByText('75%')).toBeInTheDocument()
    expect(within(summary).getByText('▲ from 50% the run before')).toBeInTheDocument()
    expect(within(summary).getAllByText('3')).toHaveLength(2) // open bugs (1 + 2) and saved tests
    expect(within(summary).getByText('68/100')).toBeInTheDocument()
    expect(within(summary).getByText('1 schedule on')).toBeInTheDocument()
    expect(within(summary).getByRole('link', { name: 'view bugs' })).toHaveAttribute('href', `/bugs?project=${PID}`)

    const passRate = screen.getByRole('region', { name: 'Pass rate per run' })
    await user.click(within(passRate).getByRole('button', { name: 'Show table' }))
    const rows = within(passRate).getAllByRole('row')
    expect(rows).toHaveLength(3) // header + 2 finished runs with verdicts (the running one is left out)
    expect(rows[2]).toHaveTextContent('replay75%3 / 1 / 0')

    const bugs = screen.getByRole('region', { name: 'Open bugs by severity' })
    expect(within(bugs).getByRole('img')).toHaveAccessibleName('Open bugs: 1 critical, 2 high, 0 medium, 0 low')

    const last = screen.getByRole('region', { name: 'Last runs' })
    const links = within(last).getAllByRole('link')
    expect(links[0]).toHaveTextContent('Run r3')
    expect(links[0]).toHaveAttribute('href', '/runs/r3') // still running: live view
    expect(links[1]).toHaveAttribute('href', '/runs/r2/report')
    expect(within(screen.getByRole('navigation', { name: 'Project' })).getByRole('link', { name: 'Saved tests' }))
      .toHaveAttribute('href', `/projects/${PID}/tests`)
  })

  it('invites the first run when there is no data', async () => {
    mockApi({
      ...base,
      [`GET /api/projects/${PID}/overview`]: () => ({
        json: { runs: [], open_bugs: { critical: 0, high: 0, medium: 0, low: 0 }, a11y: [], saved_tests: 0, schedules_enabled: 0 },
      }),
    })
    renderApp(`/projects/${PID}`)
    expect(await screen.findByRole('link', { name: 'Start the first run' }, { timeout: 5000 })).toHaveAttribute('href', `/projects/${PID}/runs/new`)
  })
})
