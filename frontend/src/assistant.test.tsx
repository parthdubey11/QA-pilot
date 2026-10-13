import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { tokens } from './lib/api'
import { aliceUser, bug, health, mockApi, project, renderApp } from './test/utils'

const PID = '66f7a1c2e4b0a1b2c3d4e5f6'
const BID = 'b00000000000000000000001'

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
  [`GET /api/projects/${PID}`]: () => ({ json: project() }),
}

function answer(overrides: Record<string, unknown> = {}) {
  return {
    id: 'q1',
    question: 'Any cart bugs?',
    answer: 'The cart accepts a quantity of -1 [1]; it was marked fixed and came back [2].',
    sources: [
      { n: 1, kind: 'bug', ref_id: BID, title: 'Cart accepts a negative quantity', run_id: 'r1', similarity: 0.82, excerpt: '' },
      { n: 2, kind: 'test', ref_id: 't1', title: 'Cart rejects negative quantities', run_id: 'r2', similarity: 0.64, excerpt: '' },
    ],
    trace: ['Retrieved 8 document(s) for "Any cart bugs?"', 'Grader kept 2 of 8 document(s)', 'Answered from 2 document(s), citing 2'],
    llm_calls: 2,
    created_at: '2026-10-13T10:00:00Z',
    ...overrides,
  }
}

describe('ask history page', () => {
  it('shows earlier answers with linked citations and sources', async () => {
    mockApi({ ...base, [`GET /api/projects/${PID}/assistant`]: () => ({ json: [answer()] }) })
    renderApp(`/projects/${PID}/ask`)

    const card = await screen.findByRole('article', { name: 'Any cart bugs?' })
    expect(screen.getByRole('link', { name: 'Ask history' })).toHaveAttribute('aria-current', 'page')
    expect(within(card).getByRole('link', { name: 'source 1' })).toHaveAttribute('href', '#source-q1-1')
    const sources = within(card).getByRole('list', { name: 'Sources' })
    expect(within(sources).getByRole('link', { name: 'Cart accepts a negative quantity' })).toHaveAttribute('href', `/bugs/${BID}`)
    expect(within(sources).getByRole('link', { name: 'Cart rejects negative quantities' })).toHaveAttribute('href', '/runs/r2/report')
    expect(within(sources).getByText('82% match')).toBeInTheDocument()
    expect(within(card).getByText(/How this was answered \(2 LLM calls\)/)).toBeInTheDocument()
    expect(within(card).getByText('Grader kept 2 of 8 document(s)')).toBeInTheDocument()
  })

  it('asks a question and shows the new answer first', async () => {
    const posted: unknown[] = []
    mockApi({
      ...base,
      [`GET /api/projects/${PID}/assistant`]: () => ({ json: [answer({ id: 'old', question: 'Older question?' })] }),
      [`POST /api/projects/${PID}/assistant`]: (body) => {
        posted.push(body)
        return { status: 201, json: answer({ id: 'new', question: 'Is checkout accessible?' }) }
      },
    })
    renderApp(`/projects/${PID}/ask`)
    const user = userEvent.setup()
    await screen.findByRole('article', { name: 'Older question?' })

    await user.type(screen.getByLabelText('Question'), 'Is checkout accessible?')
    await user.click(screen.getByRole('button', { name: 'Ask' }))

    expect(await screen.findByRole('article', { name: 'Is checkout accessible?' })).toBeInTheDocument()
    expect(posted).toEqual([{ question: 'Is checkout accessible?' }])
    const answers = within(screen.getByRole('region', { name: 'Answers' })).getAllByRole('article')
    expect(answers.map((a) => a.getAttribute('aria-label'))).toEqual(['Is checkout accessible?', 'Older question?'])
    expect(screen.getByLabelText('Question')).toHaveValue('')
  })

  it('shows the error when the question cannot be answered', async () => {
    mockApi({
      ...base,
      [`GET /api/projects/${PID}/assistant`]: () => ({ json: [] }),
      [`POST /api/projects/${PID}/assistant`]: () => ({ status: 429, json: { detail: "You've asked today's 5 questions" } }),
    })
    renderApp(`/projects/${PID}/ask`)
    const user = userEvent.setup()
    expect(await screen.findByText('No questions yet.')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Which bugs keep coming back after being fixed?' }))
    await user.click(screen.getByRole('button', { name: 'Ask' }))
    expect(await screen.findByRole('alert')).toHaveTextContent("You've asked today's 5 questions")
  })
})

describe('similar bugs panel', () => {
  it('lists bugs found by semantic search on the bug page', async () => {
    mockApi({
      ...base,
      [`GET /api/bugs/${BID}`]: () => ({ json: bug() }),
      [`GET /api/bugs/${BID}/screenshot`]: () => ({ raw: new Response(new Blob([new Uint8Array([137, 80, 78, 71])])) }),
      [`GET /api/bugs/${BID}/similar`]: () => ({
        json: [{ id: 'b2', title: 'Same email can register twice', severity: 'high', status: 'fixed', occurrences: 3,
          last_seen_at: '2026-10-01T00:00:00Z', similarity: 0.71 }],
      }),
    })
    renderApp(`/bugs/${BID}`)

    const list = await screen.findByRole('list', { name: 'Similar bugs' })
    expect(within(list).getByRole('link', { name: 'Same email can register twice' })).toHaveAttribute('href', '/bugs/b2')
    expect(within(list).getByText(/71% similar · seen 3×/)).toBeInTheDocument()
  })

  it('is hidden when the knowledge base is unavailable', async () => {
    mockApi({
      ...base,
      [`GET /api/bugs/${BID}`]: () => ({ json: bug({ has_screenshot: false }) }),
      [`GET /api/bugs/${BID}/similar`]: () => ({ status: 503, json: { detail: 'down' } }),
    })
    renderApp(`/bugs/${BID}`)
    expect(await screen.findByText('No screenshot.')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText('Searching…')).not.toBeInTheDocument())
    expect(screen.queryByRole('heading', { name: 'Similar bugs' })).not.toBeInTheDocument()
  })
})
