import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { tokens } from './lib/api'
import { parseSseChunk } from './lib/sse'
import { aliceUser, health, mockApi, project, renderApp, run, sseResponse, step, testCase } from './test/utils'

const PID = '66f7a1c2e4b0a1b2c3d4e5f6'
const RID = '777777777777777777777777'

beforeEach(() => {
  tokens.set({ access_token: 'access-1', refresh_token: 'refresh-1' })
  vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:shot'), revokeObjectURL: vi.fn() }))
})

afterEach(() => {
  tokens.clear()
  localStorage.clear()
  vi.unstubAllGlobals()
})

const base = {
  'GET /api/auth/me': () => ({ json: aliceUser }),
  'GET /api/health': () => ({ json: health }),
}

describe('SSE parser', () => {
  it('parses complete events and keeps the incomplete tail', () => {
    const { events, rest } = parseSseChunk(
      'event: step\nid: 3\ndata: {"a":1}\n\n: keep-alive\n\nevent: end\ndata: {}\n\nevent: st',
    )
    expect(events).toEqual([
      { event: 'step', id: '3', data: '{"a":1}' },
      { event: 'end', id: undefined, data: '{}' },
    ])
    expect(rest).toBe('event: st')
  })
})

describe('runs', () => {
  it('projects list has a New run button per project', async () => {
    mockApi({ ...base, 'GET /api/projects': () => ({ json: [project()] }) })
    renderApp('/projects')

    const link = await screen.findByRole('link', { name: 'New run for Demo Shop' })
    expect(link).toHaveAttribute('href', `/projects/${PID}/runs/new`)
  })

  it('starts a run with the chosen options and opens the run page', async () => {
    const created: unknown[] = []
    mockApi({
      ...base,
      [`GET /api/projects/${PID}`]: () => ({ json: project() }),
      [`POST /api/projects/${PID}/runs`]: (body) => {
        created.push(body)
        return { status: 201, json: run() }
      },
      [`GET /api/runs/${RID}/stream`]: () => ({ raw: sseResponse([{ event: 'run', data: run() }]) }),
    })
    renderApp(`/projects/${PID}/runs/new`)
    const user = userEvent.setup()

    const goal = await screen.findByLabelText('What should QA Pilot test?')
    await user.clear(goal)
    await user.type(goal, 'Test signup and login')
    await user.click(screen.getByLabelText('Mobile viewport (390 × 844)'))
    await user.click(screen.getByRole('button', { name: 'Start run' }))

    expect(await screen.findByRole('heading', { name: 'Run' })).toBeInTheDocument()
    expect(created).toEqual([
      { goal: 'Test signup and login', options: { accessibility: true, mobile_viewport: true, max_tests: 8 } },
    ])
  })

  it('shows the error when a run is already in progress', async () => {
    mockApi({
      ...base,
      [`GET /api/projects/${PID}`]: () => ({ json: project() }),
      [`POST /api/projects/${PID}/runs`]: () => ({
        status: 409,
        json: { detail: 'This project already has a run in progress. Wait for it to finish.' },
      }),
    })
    renderApp(`/projects/${PID}/runs/new`)
    const user = userEvent.setup()

    expect(await screen.findByRole('button', { name: 'Start run' })).toBeDisabled() // no goal yet
    await user.type(screen.getByLabelText('What should QA Pilot test?'), 'test signup, login and cart')
    await user.click(screen.getByRole('button', { name: 'Start run' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('already has a run in progress')
  })

  it('run page shows the live step feed and the latest screenshot', async () => {
    const fetchMock = mockApi({
      ...base,
      [`GET /api/runs/${RID}/stream`]: () => ({
        raw: sseResponse([
          { event: 'run', data: run({ status: 'running' }) },
          { event: 'step', id: 0, data: step(0, { message: 'Starting run: Smoke test' }) },
          { event: 'step', id: 1, data: step(1, { kind: 'action', message: 'Opened http://demo-shop:8000/ (HTTP 200)' }) },
          {
            event: 'step',
            id: 2,
            data: step(2, { kind: 'observation', message: 'Snapshot: 42 elements', snapshot: 'URL: http://demo-shop:8000/\n[1] link "Products"' }),
          },
          { event: 'step', id: 3, data: step(3, { kind: 'observation', message: 'Screenshot of the page', has_screenshot: true, url: 'http://demo-shop:8000/' }) },
          { event: 'run', data: run({ status: 'completed' }) },
          { event: 'end', data: { status: 'completed' } },
        ]),
      }),
      [`GET /api/runs/${RID}/steps/3/screenshot`]: () => ({
        raw: new Response(new Blob([new Uint8Array([137, 80, 78, 71])], { type: 'image/png' })),
      }),
    })
    renderApp(`/runs/${RID}`)

    const feed = await screen.findByRole('list', { name: 'Step feed' })
    expect(await within(feed).findByText('Screenshot of the page')).toBeInTheDocument()
    expect(within(feed).getAllByRole('listitem').filter((li) => li.textContent)).toHaveLength(4)
    expect(within(feed).getByText('Opened http://demo-shop:8000/ (HTTP 200)')).toBeInTheDocument()
    expect(within(feed).getByText(/\[1\] link "Products"/)).toBeInTheDocument()
    expect(await screen.findByText('Completed')).toBeInTheDocument()
    const img = await screen.findByRole('img', { name: 'Screenshot from step 4' })
    expect(img).toHaveAttribute('src', 'blob:shot')
    expect(screen.getByRole('link', { name: 'Run again' })).toHaveAttribute('href', `/projects/${PID}/runs/new`)
    const streamCall = fetchMock.mock.calls.find(([url]) => String(url).includes('/stream'))
    expect(streamCall?.[0]).toBe(`/api/runs/${RID}/stream?after=-1`)
    expect(streamCall?.[1]?.headers).toMatchObject({ Authorization: 'Bearer access-1' })
  })

  it('shows test cases with live verdicts and filters the feed by test', async () => {
    mockApi({
      ...base,
      [`GET /api/runs/${RID}/stream`]: () => ({
        raw: sseResponse([
          { event: 'run', data: run({ status: 'running' }) },
          { event: 'step', id: 0, data: step(0, { phase: 'explore', kind: 'observation', message: 'Explored / — "Products"' }) },
          { event: 'test_case', data: testCase(0, { title: 'Sign up with valid details', status: 'running' }) },
          { event: 'test_case', data: testCase(1, { title: 'Duplicate email is rejected', type: 'edge' }) },
          { event: 'step', id: 1, data: step(1, { phase: 'execute', test_case_index: 0, kind: 'thought', message: 'Fill in the email' }) },
          { event: 'step', id: 2, data: step(2, { phase: 'execute', test_case_index: 1, kind: 'thought', message: 'Sign up again' }) },
          { event: 'test_case', data: testCase(0, { title: 'Sign up with valid details', status: 'passed', reason: 'Account created' }) },
          { event: 'test_case', data: testCase(1, { title: 'Duplicate email is rejected', type: 'edge', status: 'failed', reason: 'Second signup was accepted' }) },
          { event: 'run', data: run({ status: 'completed', stats: { pages: 5, tests: 2, passed: 1, failed: 1, blocked: 0, errors: 0, llm_calls: 14, input_tokens: 21000, output_tokens: 900, seconds: 95 } }) },
          { event: 'end', data: { status: 'completed' } },
        ]),
      }),
    })
    renderApp(`/runs/${RID}`)
    const user = userEvent.setup()

    const tests = await screen.findByRole('list', { name: 'Test cases' })
    const second = await within(tests).findByRole('button', { name: /Duplicate email is rejected/ })
    expect(second).toHaveTextContent('failed')
    expect(second).not.toHaveTextContent('Second signup was accepted') // the judge's reason shows once selected
    expect(within(tests).getByRole('button', { name: /Sign up with valid details/ })).toHaveTextContent('passed')
    expect(await screen.findByText(/5 pages explored · 2 tests · 1 passed · 1 failed · 0 blocked · 14 LLM calls · 21.9k tokens · 95 s/)).toBeInTheDocument()

    const feed = screen.getByRole('list', { name: 'Step feed' })
    expect(within(feed).getByText('Test 1')).toBeInTheDocument()
    expect(within(feed).getByText('Explore')).toBeInTheDocument()
    await user.click(second)
    expect(second).toHaveTextContent('Second signup was accepted')
    expect(screen.getByRole('heading', { name: /Steps of test 2/ })).toBeInTheDocument()
    expect(within(feed).getByText('Sign up again')).toBeInTheDocument()
    expect(within(feed).queryByText('Fill in the email')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Show all steps' }))
    expect(within(feed).getByText('Fill in the email')).toBeInTheDocument()
  })

  it("shows 'run not found' for someone else's run", async () => {
    mockApi({ ...base, [`GET /api/runs/${RID}/stream`]: () => ({ status: 404, json: { detail: 'Run not found' } }) })
    renderApp(`/runs/${RID}`)

    expect(await screen.findByRole('heading', { name: 'Run not found' })).toBeInTheDocument()
  })
})

describe('run workspace', () => {
  it('filters the feed, resizes with the keyboard, remembers the size and goes full screen', async () => {
    mockApi({
      ...base,
      [`GET /api/runs/${RID}/stream`]: () => ({
        raw: sseResponse([
          { event: 'run', data: run({ status: 'running' }) },
          { event: 'step', id: 0, data: step(0, { kind: 'thought', message: 'I should open the cart' }) },
          { event: 'step', id: 1, data: step(1, { kind: 'action', message: 'click [7]' }) },
          { event: 'step', id: 2, data: step(2, { kind: 'error', message: 'Element [9] is gone' }) },
        ]),
      }),
    })
    renderApp(`/runs/${RID}`)
    const user = userEvent.setup()

    const feed = await screen.findByRole('list', { name: 'Step feed' })
    expect(await within(feed).findByText('Element [9] is gone')).toBeInTheDocument()
    expect(within(feed).getByText('Thinking')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Thinking' }))
    expect(within(feed).getByText('I should open the cart')).toBeInTheDocument()
    expect(within(feed).queryByText('click [7]')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Errors' }))
    expect(within(feed).getAllByRole('listitem')).toHaveLength(1)
    await user.click(screen.getByRole('button', { name: 'All' }))
    expect(within(feed).getAllByRole('listitem')).toHaveLength(3)

    const handle = screen.getByRole('separator', { name: 'Resize activity panel' })
    const before = Number(handle.getAttribute('aria-valuenow'))
    handle.focus()
    await user.keyboard('{ArrowUp}') // starts filling the window, so shrink it
    const after = Number(handle.getAttribute('aria-valuenow'))
    expect(after).toBe(before - 40)
    expect(localStorage.getItem('qa-pilot.run.height')).toBe(String(after))

    await user.click(screen.getByRole('button', { name: 'Full screen' }))
    expect(screen.queryByRole('separator', { name: 'Resize activity panel' })).not.toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(screen.getByRole('separator', { name: 'Resize activity panel' })).toBeInTheDocument()
  })

  it('project pages share a header with tabs marking the current page', async () => {
    mockApi({
      ...base,
      'GET /api/notifications': () => ({ json: { unread: 0, items: [] } }),
      [`GET /api/projects/${PID}`]: () => ({ json: project() }),
      [`GET /api/projects/${PID}/schedules`]: () => ({ json: [] }),
    })
    renderApp(`/projects/${PID}/schedules`)
    const tabs = await screen.findByRole('navigation', { name: 'Project' })
    expect(within(tabs).getByRole('link', { name: 'Schedules' })).toHaveAttribute('aria-current', 'page')
    expect(within(tabs).getByRole('link', { name: 'Overview' })).toHaveAttribute('href', `/projects/${PID}`)
    const crumbs = screen.getByRole('navigation', { name: 'Breadcrumb' })
    expect(within(crumbs).getByRole('link', { name: 'Projects' })).toHaveAttribute('href', '/projects')
    expect(await within(crumbs).findByRole('link', { name: 'Demo Shop' })).toHaveAttribute('href', `/projects/${PID}`)
    expect(screen.getByRole('link', { name: 'New run' })).toHaveAttribute('href', `/projects/${PID}/runs/new`)
  })
})
