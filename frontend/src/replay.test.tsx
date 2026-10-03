import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { tokens } from './lib/api'
import { aliceUser, health, mockApi, project, renderApp, run, sseResponse } from './test/utils'

const PID = '66f7a1c2e4b0a1b2c3d4e5f6'
const RID = '777777777777777777777777'

beforeEach(() => {
  tokens.set({ access_token: 'access-1', refresh_token: 'refresh-1' })
  vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() }))
})
afterEach(() => {
  tokens.clear()
  localStorage.clear()
  vi.unstubAllGlobals()
})

const base = {
  'GET /api/auth/me': () => ({ json: aliceUser }),
  'GET /api/health': () => ({ json: health }),
  [`GET /api/projects/${PID}`]: () => ({ json: project() }),
  'GET /api/notifications': () => ({ json: { unread: 0, items: [] } }),
}

const loc = (role: string, name: string, css: string) => ({ role, name, text: name, css, tag: role === 'button' ? 'button' : 'input', x: 100, y: 200 })

const savedTest = {
  id: 's1', project_id: PID, title: 'Sign up works', start_path: '/signup', expected: '',
  steps: [
    { tool: 'type', locators: loc('textbox', 'Email', 'input[name="email"]'), url: null, text: '{{cred:Shopper:username}}', value: null, key: null, ms: null, direction: null, note: 'Enter the email' },
    { tool: 'click', locators: loc('button', 'Register', '#register'), url: null, text: null, value: null, key: null, ms: null, direction: null, note: '' },
  ],
  assertions: [{ kind: 'text', value: 'Account created. You can now log in.' }],
  source_run_id: 'r0', last_result: 'passed', last_run_id: RID, last_run_at: '2026-09-30T00:00:00Z', replays: 2,
  heal_history: [{ at: '2026-09-30T00:00:00Z', run_id: RID, step_index: 1, method: 'ai-healer',
    old: loc('button', 'Create account', '#create-account'), new: loc('button', 'Register', '#register'),
    reason: "'Create account' was renamed to 'Register'" }],
  created_at: '2026-09-29T00:00:00Z', updated_at: '2026-09-30T00:00:00Z',
}

describe('saved tests', () => {
  it('lists saved tests with locators and heal history, replays all, and exports a Playwright script', async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const replays: unknown[] = []
    const fetchMock = mockApi({
      ...base,
      [`GET /api/projects/${PID}/saved-tests`]: () => ({ json: [savedTest] }),
      [`POST /api/projects/${PID}/replays`]: (body) => {
        replays.push(body)
        return { status: 201, json: run({ id: RID, kind: 'replay', goal: 'Replay 1 saved test' }) }
      },
      [`GET /api/runs/${RID}/stream`]: () => ({ raw: sseResponse([{ event: 'run', data: run({ kind: 'replay' }) }]) }),
      'GET /api/saved-tests/s1/export.spec.ts': () => ({
        raw: new Response('import { test } from "@playwright/test"', { headers: { 'Content-Disposition': 'attachment; filename="sign-up-works.spec.ts"' } }),
      }),
    })
    renderApp(`/projects/${PID}/tests`)
    const user = userEvent.setup()

    const list = await screen.findByRole('list', { name: 'Saved tests' })
    expect(within(list).getByText(/2 steps · starts at \/signup · replayed 2× · 1 heal/)).toBeInTheDocument()
    await user.click(within(list).getByText('Steps and locators'))
    expect(within(list).getByText(/Type "{{cred:Shopper:username}}" into textbox "Email"/)).toBeInTheDocument()
    expect(within(list).getByText('input[name="email"]')).toBeInTheDocument()
    expect(within(list).getAllByText('(100, 200) <input>')[0]).toBeInTheDocument()
    await user.click(within(list).getByText('Heal history (1)'))
    const heals = within(list).getByRole('list', { name: 'Heal history of Sign up works' })
    expect(heals).toHaveTextContent('Step 2: button "Create account" → button "Register"')
    expect(heals).toHaveTextContent("AI healer · 'Create account' was renamed to 'Register'")

    await user.click(screen.getByRole('button', { name: 'Export as Playwright script' }))
    await waitFor(() => expect(click).toHaveBeenCalled())
    expect((click.mock.instances[0] as unknown as HTMLAnchorElement).download).toBe('sign-up-works.spec.ts')

    await user.click(screen.getByLabelText('Select Sign up works'))
    await user.click(screen.getByRole('button', { name: 'Replay selected (1)' }))
    expect(await screen.findByRole('heading', { name: 'Run' })).toBeInTheDocument()
    expect(replays).toEqual([{ saved_test_ids: ['s1'] }])
    expect(fetchMock.mock.calls.some(([url]) => url === `/api/runs/${RID}/stream?after=-1`)).toBe(true)
    click.mockRestore()
  })
})

describe('schedules', () => {
  it('creates a schedule from a preset, toggles it, and shows cron errors', async () => {
    const created: unknown[] = []
    const patched: unknown[] = []
    let schedules: Record<string, unknown>[] = []
    mockApi({
      ...base,
      [`GET /api/projects/${PID}/schedules`]: () => ({ json: schedules }),
      [`POST /api/projects/${PID}/schedules`]: (body) => {
        created.push(body)
        if ((body as { cron: string }).cron === '61 * * * *') return { status: 422, json: { detail: 'Invalid schedule: bad minute' } }
        schedules = [{ id: 'sc1', project_id: PID, name: 'Nightly', cron: '0 2 * * *', timezone: 'UTC', enabled: true,
          next_run_at: '2026-10-01T02:00:00Z', last_triggered_at: null, last_run_id: null, last_skip_reason: null, created_at: '' }]
        return { status: 201, json: schedules[0] }
      },
      'PATCH /api/schedules/sc1': (body) => {
        patched.push(body)
        schedules = [{ ...schedules[0], enabled: false, next_run_at: null }]
        return { json: schedules[0] }
      },
    })
    renderApp(`/projects/${PID}/schedules`)
    const user = userEvent.setup()

    expect(await screen.findByText('No schedules yet.')).toBeInTheDocument()
    await user.type(screen.getByLabelText('Name'), 'Nightly')
    expect(screen.getByLabelText(/Cron expression/)).toHaveValue('0 2 * * *') // default preset
    await user.click(screen.getByRole('button', { name: 'Create schedule' }))
    const list = await screen.findByRole('list', { name: 'Schedules' })
    expect(within(list).getByText(/Every day at 02:00 \(0 2 \* \* \*, UTC\) · next run/)).toBeInTheDocument()
    expect(created[0]).toEqual({ name: 'Nightly', cron: '0 2 * * *', timezone: 'UTC', enabled: true })

    await user.click(within(list).getByRole('checkbox', { name: /Enabled/ }))
    expect(patched).toEqual([{ enabled: false }])
    await waitFor(() => expect(within(list).getByRole('checkbox', { name: /Enabled/ })).not.toBeChecked())

    await user.type(screen.getByLabelText('Name'), 'Broken')
    await user.selectOptions(screen.getByLabelText('How often'), 'custom')
    await user.clear(screen.getByLabelText(/Cron expression/))
    await user.type(screen.getByLabelText(/Cron expression/), '61 * * * *')
    await user.click(screen.getByRole('button', { name: 'Create schedule' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid schedule: bad minute')
  })
})

describe('notifications', () => {
  it('shows the unread count in the sidebar and marks notifications read', async () => {
    let unread = true
    const note = () => ({ id: 'n1', project_id: PID, run_id: RID, kind: 'new_failures', read: !unread,
      title: '1 new failure(s) in Demo Shop', body: 'These saved tests passed before and failed in the scheduled run: Sign up works',
      created_at: '2026-09-30T02:00:00Z' })
    mockApi({
      ...base,
      'GET /api/notifications': () => ({ json: { unread: unread ? 1 : 0, items: [note()] } }),
      'POST /api/notifications/n1/read': () => {
        unread = false
        return { status: 204 }
      },
    })
    renderApp('/notifications')
    const user = userEvent.setup()

    const nav = await screen.findByRole('navigation', { name: 'Main' })
    await screen.findByRole('list', { name: 'Notifications' })
    const list = screen.getByRole('list', { name: 'Notifications' })
    expect(await within(nav).findByRole('link', { name: 'Notifications 1 unread' })).toBeInTheDocument()
    expect(within(list).getByText('1 new failure(s) in Demo Shop')).toBeInTheDocument()
    expect(within(list).getByRole('link', { name: 'view report' })).toHaveAttribute('href', `/runs/${RID}/report`)

    await user.click(within(list).getByRole('button', { name: 'Mark "1 new failure(s) in Demo Shop" as read' }))
    await waitFor(() => expect(within(nav).getByRole('link', { name: 'Notifications' })).toBeInTheDocument())
  })
})
