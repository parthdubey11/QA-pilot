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
  [`GET /api/projects/${PID}`]: () => ({ json: project() }),
}

function issue(id: string, overrides: Record<string, unknown>) {
  return {
    id, page_path: '/', page_url: 'http://demo-shop:8000/', rule_id: 'image-alt', source: 'axe', impact: 'critical',
    title: 'Images must have alternative text', description: 'Ensures <img> elements have alternate text',
    how_to_fix: 'Give every <img> an alt attribute.', wcag: ['1.1.1'],
    help_url: 'https://dequeuniversity.com/rules/axe/4.13/image-alt',
    nodes: [{ target: '.product-card img', html: '<img src="/static/img/shoes.svg">', summary: 'Element has no alt attribute' }],
    ...overrides,
  }
}

const audit = {
  audit: {
    run_id: 'r2', score: 58, issue_count: 3, llm_checks: true, created_at: '2026-09-30T00:00:00Z',
    pages: [
      { path: '/', url: 'http://demo-shop:8000/', title: 'Products', score: 71, issues: 2, same_as: [], error: null },
      { path: '/products/1', url: 'http://demo-shop:8000/products/1', title: 'Shoes', score: 45, issues: 1, same_as: ['/products/2'], error: null },
    ],
  },
  issues: [
    issue('1', {}),
    issue('2', { rule_id: 'focus-not-visible', source: 'keyboard', impact: 'serious', title: 'No visible focus indicator',
      description: 'Buttons look the same when focused.', how_to_fix: 'Add a :focus-visible outline.', wcag: ['2.4.7'], help_url: null,
      nodes: [{ target: 'button "Add to cart" (<button>)', html: '', summary: '' }] }),
    issue('3', { page_path: '/products/1', rule_id: 'focus-not-visible', source: 'keyboard', impact: 'serious',
      title: 'No visible focus indicator', description: 'Same.', how_to_fix: 'Add a :focus-visible outline.', wcag: ['2.4.7'], help_url: null }),
  ],
  history: [
    { run_id: 'r1', score: 40, issue_count: 5, created_at: '2026-09-29T00:00:00Z' },
    { run_id: 'r2', score: 58, issue_count: 3, created_at: '2026-09-30T00:00:00Z' },
  ],
}

describe('accessibility page', () => {
  it('shows the score, pages, issues grouped by type with how-to-fix, and by page', async () => {
    mockApi({ ...base, [`GET /api/projects/${PID}/accessibility`]: () => ({ json: audit }) })
    renderApp(`/projects/${PID}/accessibility`)
    const user = userEvent.setup()

    const score = await screen.findByRole('region', { name: 'Score' })
    expect(within(score).getByText('58')).toBeInTheDocument()
    expect(within(score).getByText('WCAG score: Poor')).toBeInTheDocument()
    expect(within(score).getByText(/3 issues on 2 page\(s\)/)).toBeInTheDocument()
    expect(within(score).getByRole('link', { name: /^Score 40 on / })).toHaveAttribute('href', `/projects/${PID}/accessibility?run=r1`)

    const pages = screen.getByRole('table')
    expect(within(pages).getByText('also covers /products/2')).toBeInTheDocument()
    expect(within(pages).getByText('45')).toBeInTheDocument()

    const byType = screen.getByRole('list', { name: 'Issues by type' })
    const groups = within(byType).getAllByRole('listitem').filter((li) => li.parentElement === byType)
    expect(groups).toHaveLength(2) // image-alt, focus-not-visible (2 pages merged into one type)
    expect(groups[1]).toHaveTextContent('Keyboard check · focus-not-visible · WCAG 2.4.7 · on 2 pages: /, /products/1')
    expect(groups[0]).toHaveTextContent('How to fix: Give every <img> an alt attribute.')
    expect(within(groups[0]).getByRole('link', { name: /Learn more/ })).toHaveAttribute('href', 'https://dequeuniversity.com/rules/axe/4.13/image-alt')
    await user.click(within(groups[0]).getByText('1 affected element on /'))
    expect(within(groups[0]).getByText('.product-card img')).toBeInTheDocument()

    await user.click(screen.getByLabelText('By page'))
    const byPage = screen.getByRole('list', { name: 'Issues by page' })
    expect(within(byPage).getByRole('heading', { name: '/products/1' })).toBeInTheDocument()
    expect(within(byPage).getByRole('heading', { name: '/' })).toBeInTheDocument()
  })

  it('invites a run when there is no audit yet', async () => {
    mockApi({ ...base, [`GET /api/projects/${PID}/accessibility`]: () => ({ json: { audit: null, issues: [], history: [] } }) })
    renderApp(`/projects/${PID}/accessibility`)

    expect(await screen.findByText('No accessibility audit yet')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Start a run with the audit' })).toHaveAttribute('href', `/projects/${PID}/runs/new`)
  })

  it('New run offers the accessibility audit option, on by default', async () => {
    mockApi(base)
    renderApp(`/projects/${PID}/runs/new`)

    expect(await screen.findByRole('checkbox', { name: /Accessibility audit/ })).toBeChecked()
  })
})
