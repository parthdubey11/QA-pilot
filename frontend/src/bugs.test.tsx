import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { tokens } from './lib/api'
import { aliceUser, bug, health, mockApi, project, renderApp, run, step, testCase } from './test/utils'

const RID = '777777777777777777777777'
const BID = 'b00000000000000000000001'
const PNG = () => new Response(new Blob([new Uint8Array([137, 80, 78, 71])], { type: 'image/png' }))

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
  'GET /api/projects': () => ({ json: [project()] }),
}

const bugUrls = (fetchMock: ReturnType<typeof mockApi>) =>
  fetchMock.mock.calls.map(([url]) => String(url)).filter((url) => url.startsWith('/api/bugs?') || url === '/api/bugs')

describe('bugs page', () => {
  it('lists open bugs by default and filters by severity and status', async () => {
    const fetchMock = mockApi({
      ...base,
      'GET /api/bugs': () => ({ json: [bug(), bug({ id: 'b2', title: 'Cart total wrong', severity: 'critical', reopened: true, has_screenshot: false })] }),
      [`GET /api/bugs/${BID}/screenshot`]: () => ({ raw: PNG() }),
    })
    renderApp('/bugs')
    const user = userEvent.setup()

    const list = await screen.findByRole('list', { name: 'Bugs' })
    expect(within(list).getByRole('link', { name: 'Signup accepts duplicate email addresses' })).toHaveAttribute('href', `/bugs/${BID}`)
    expect(within(list).getAllByText(/Demo Shop · seen 2×/)).toHaveLength(2)
    expect(within(list).getByText(/reopened/)).toBeInTheDocument()
    // every bug row shows its screenshot (or says there is none)
    await waitFor(() => expect(list.querySelector(`img[src="blob:x"]`)).not.toBeNull())
    expect(within(list).getByText('No screenshot')).toBeInTheDocument()
    expect(bugUrls(fetchMock).at(-1)).toBe('/api/bugs?status=open')

    await user.click(screen.getByLabelText('high'))
    await user.click(screen.getByLabelText('critical'))
    await waitFor(() => expect(bugUrls(fetchMock).at(-1)).toBe('/api/bugs?severity=high&severity=critical&status=open'))

    await user.selectOptions(screen.getByLabelText('Status'), 'All')
    await waitFor(() => expect(bugUrls(fetchMock).at(-1)).toBe('/api/bugs?severity=high&severity=critical'))
    await user.selectOptions(screen.getByLabelText('Project'), 'Demo Shop')
    await waitFor(() => expect(bugUrls(fetchMock).at(-1)).toContain('project_id=66f7a1c2e4b0a1b2c3d4e5f6'))
  })

  it('bug detail shows the report and marks the bug fixed', async () => {
    const updates: unknown[] = []
    let current = bug()
    mockApi({
      ...base,
      [`GET /api/bugs/${BID}`]: () => ({ json: current }),
      [`GET /api/bugs/${BID}/screenshot`]: () => ({ raw: PNG() }),
      [`PATCH /api/bugs/${BID}`]: (body) => {
        updates.push(body)
        current = bug({ status: 'fixed' })
        return { json: current }
      },
    })
    renderApp(`/bugs/${BID}`)
    const user = userEvent.setup()

    expect(await screen.findByRole('heading', { name: 'Signup accepts duplicate email addresses' })).toBeInTheDocument()
    expect(screen.getByText('Sign up twice with qa@example.com')).toBeInTheDocument()
    expect(screen.getByText('Account created. You can now log in.')).toBeInTheDocument()
    expect(screen.getByText('Add a unique check on email')).toBeInTheDocument()
    expect(screen.getByText(/Seen 2× in 2 runs/)).toBeInTheDocument()
    expect(await screen.findByRole('img', { name: /Screenshot of: Signup accepts/ })).toHaveAttribute('src', 'blob:x')
    expect(screen.getAllByRole('link', { name: /Run report/ })[0]).toHaveAttribute('href', '/runs/888888888888888888888888/report')

    const actions = screen.getByRole('group', { name: 'Change status' })
    expect(within(actions).queryByRole('button', { name: 'Reopen' })).not.toBeInTheDocument()
    await user.click(within(actions).getByRole('button', { name: 'Mark fixed' }))

    expect(updates).toEqual([{ status: 'fixed' }])
    expect(await within(actions).findByRole('button', { name: 'Reopen' })).toBeInTheDocument()
    expect(within(actions).queryByRole('button', { name: 'Mark fixed' })).not.toBeInTheDocument()
  })
})

describe('run report page', () => {
  it('shows summary, bugs, tests with expandable logs, and downloads the HTML report', async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const fetchMock = mockApi({
      ...base,
      [`GET /api/runs/${RID}`]: () => ({
        json: run({ status: 'completed', started_at: '2026-09-28T10:00:00Z', stats: { seconds: 544, llm_calls: 29, input_tokens: 46383, output_tokens: 3579 } }),
      }),
      [`GET /api/runs/${RID}/test-cases`]: () => ({
        json: [
          testCase(0, { title: 'Sign up with valid data', status: 'passed', reason: 'Account created' }),
          testCase(1, { title: 'Sign up with duplicate email', type: 'edge', status: 'failed', reason: 'Second signup accepted', bug_id: BID, has_final_screenshot: true }),
          testCase(2, { title: 'Checkout', status: 'blocked', reason: 'needs human' }),
        ],
      }),
      [`GET /api/runs/${RID}/steps`]: () => ({
        json: [
          step(0, { phase: 'plan', message: 'Planned 3 test cases' }),
          step(1, { phase: 'execute', test_case_index: 1, kind: 'thought', message: 'Sign up again with the same email' }),
          step(2, { phase: 'judge', test_case_index: 1, kind: 'thought', message: 'Verdict: FAIL — accepted twice' }),
        ],
      }),
      'GET /api/bugs': () => ({ json: [bug()] }),
      [`GET /api/runs/${RID}/test-cases/1/screenshot`]: () => ({ raw: PNG() }),
      [`GET /api/runs/${RID}/report.html`]: () => ({
        raw: new Response('<!doctype html><title>report</title>', {
          headers: { 'Content-Type': 'text/html', 'Content-Disposition': 'attachment; filename="qa-pilot-report-Demo-Shop-2026-09-28.html"' },
        }),
      }),
    })
    renderApp(`/runs/${RID}/report`)
    const user = userEvent.setup()

    expect(await screen.findByRole('heading', { name: 'Run report' })).toBeInTheDocument()
    expect(await screen.findByText('Second signup accepted')).toBeInTheDocument() // test cases loaded
    const summary = screen.getByRole('region', { name: 'Summary' })
    for (const [value, label] of [['3', 'tests'], ['1', 'passed'], ['1', 'failed'], ['1', 'blocked']]) {
      expect(within(summary).getByText(label).previousSibling).toHaveTextContent(value)
    }
    expect(await within(summary).findByText('bugs')).toBeInTheDocument()
    expect(screen.getByText(/9 min 4 s · 29 LLM calls · 50.0k tokens/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Signup accepts duplicate email addresses' })).toHaveAttribute('href', `/bugs/${BID}`)
    expect(fetchMock.mock.calls.some(([url]) => url === `/api/bugs?run_id=${RID}`)).toBe(true)

    const tests = screen.getByRole('list', { name: 'Test results' })
    expect(within(tests).getByText('Second signup accepted')).toBeInTheDocument()
    expect(within(tests).getByRole('link', { name: /View the bug report/ })).toHaveAttribute('href', `/bugs/${BID}`)
    await user.click(await within(tests).findByText('Agent log (2 steps) and final screenshot'))
    const log = within(tests).getByRole('list', { name: 'Agent log of test 2' })
    expect(within(log).getByText('Sign up again with the same email')).toBeInTheDocument()
    expect(await within(tests).findByRole('img', { name: 'Final screen of test 2' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Download report' }))
    await waitFor(() => expect(click).toHaveBeenCalledTimes(1))
    const link = click.mock.instances[0] as unknown as HTMLAnchorElement
    expect(link.download).toBe('qa-pilot-report-Demo-Shop-2026-09-28.html')
    const downloadCall = fetchMock.mock.calls.find(([url]) => url === `/api/runs/${RID}/report.html`)
    expect(downloadCall?.[1]?.headers).toMatchObject({ Authorization: 'Bearer access-1' })
    click.mockRestore()
  })
})
