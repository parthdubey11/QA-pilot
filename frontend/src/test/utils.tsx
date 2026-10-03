import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'
import App from '../App'
import { AuthProvider } from '../auth/AuthContext'

type Reply = { status?: number; json?: unknown; raw?: Response }
type Handler = (body: unknown) => Reply | Promise<Reply>

/**
 * Stub global fetch with handlers keyed by "METHOD /api/path". Returns the mock so tests can inspect calls.
 * A handler given as an array is used once per call, in order (the last one repeats).
 */
export function mockApi(handlers: Record<string, Handler | Handler[]>) {
  const used: Record<string, number> = {}
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? 'GET'} ${url.split('?')[0]}`
    const entry = handlers[key]
    if (!entry) return new Response(JSON.stringify({ detail: `No mock for ${key}` }), { status: 404 })
    const list = Array.isArray(entry) ? entry : [entry]
    const index = Math.min(used[key] ?? 0, list.length - 1)
    used[key] = (used[key] ?? 0) + 1
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    const { status = 200, json, raw } = await list[index](body)
    if (raw) return raw
    return new Response(status === 204 ? null : JSON.stringify(json ?? {}), { status })
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

export function renderApp(route: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[route]}>
        <AuthProvider>
          <App />
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

export const aliceUser = { id: '66f7a1c2e4b0a1b2c3d4e5f0', email: 'alice@example.com', name: 'Alice', created_at: '2026-09-28T00:00:00Z' }
export const authResponse = { user: aliceUser, access_token: 'access-1', refresh_token: 'refresh-1', token_type: 'bearer', expires_in: 900 }
export const health = { status: 'ok', database: 'ok', version: '0.1.0' }

export function project(overrides: Record<string, unknown> = {}) {
  return {
    id: '66f7a1c2e4b0a1b2c3d4e5f6',
    name: 'Demo Shop',
    base_url: 'http://localhost:8080/',
    is_own_site: false,
    security_probes_enabled: false,
    authorised_at: '2026-09-28T00:00:00Z',
    created_at: '2026-09-28T00:00:00Z',
    updated_at: '2026-09-28T00:00:00Z',
    ...overrides,
  }
}

/** A text/event-stream Response that sends the given events, then closes. */
export function sseResponse(events: { event: string; data: unknown; id?: number }[]) {
  const text = events
    .map((e) => `event: ${e.event}
${e.id !== undefined ? `id: ${e.id}
` : ''}data: ${JSON.stringify(e.data)}

`)
    .join('')
  const bytes = new TextEncoder().encode(text)
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      // Split in two chunks mid-event to exercise the parser's buffering.
      const mid = Math.floor(bytes.length / 2)
      controller.enqueue(bytes.slice(0, mid))
      controller.enqueue(bytes.slice(mid))
      controller.close()
    },
  })
  return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}

export function run(overrides: Record<string, unknown> = {}) {
  return {
    id: '777777777777777777777777',
    project_id: '66f7a1c2e4b0a1b2c3d4e5f6',
    project_name: 'Demo Shop',
    goal: 'Smoke test the home page',
    options: { accessibility: true, mobile_viewport: false, max_tests: 15 },
    status: 'queued',
    error: null,
    stats: {},
    created_at: '2026-09-28T00:00:00Z',
    started_at: null,
    finished_at: null,
    ...overrides,
  }
}

export function step(index: number, overrides: Record<string, unknown> = {}) {
  return {
    index,
    kind: 'info',
    phase: null,
    test_case_index: null,
    message: `step ${index}`,
    action: null,
    url: null,
    snapshot: null,
    has_screenshot: false,
    created_at: '2026-09-28T00:00:00Z',
    ...overrides,
  }
}

export function testCase(index: number, overrides: Record<string, unknown> = {}) {
  return {
    index,
    title: `Test ${index + 1}`,
    type: 'happy',
    start_path: '/',
    steps: ['Open the page'],
    expected: 'It works',
    status: 'pending',
    reason: null,
    steps_used: 0,
    final_url: null,
    has_final_screenshot: false,
    bug_id: null,
    started_at: null,
    finished_at: null,
    updated_at: '2026-09-28T00:00:00Z',
    ...overrides,
  }
}

export function bug(overrides: Record<string, unknown> = {}) {
  return {
    id: 'b00000000000000000000001',
    project_id: '66f7a1c2e4b0a1b2c3d4e5f6',
    project_name: 'Demo Shop',
    title: 'Signup accepts duplicate email addresses',
    severity: 'high',
    status: 'open',
    steps: ['Open /signup', 'Sign up twice with qa@example.com'],
    expected: 'The second signup is rejected',
    actual: 'Account created. You can now log in.',
    suggested_fix: 'Add a unique check on email',
    url: 'http://demo-shop:8000/signup',
    has_screenshot: true,
    occurrences: 2,
    run_ids: ['777777777777777777777777', '888888888888888888888888'],
    first_run_id: '777777777777777777777777',
    first_test_title: 'Sign up with duplicate email',
    reopened: false,
    first_seen_at: '2026-09-28T00:00:00Z',
    last_seen_at: '2026-09-29T00:00:00Z',
    updated_at: '2026-09-29T00:00:00Z',
    ...overrides,
  }
}
