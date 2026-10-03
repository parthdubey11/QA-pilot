import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiFetch, tokens } from './lib/api'
import { aliceUser, authResponse, health, mockApi, project, renderApp } from './test/utils'

afterEach(() => {
  tokens.clear()
  localStorage.clear()
  vi.unstubAllGlobals()
})

async function logIn() {
  const user = userEvent.setup()
  await user.type(await screen.findByLabelText('Email'), 'alice@example.com')
  await user.type(screen.getByLabelText('Password'), 'correct-horse')
  await user.click(screen.getByRole('button', { name: 'Log in' }))
  return user
}

describe('auth', () => {
  it('sends signed-out users to the login page', async () => {
    mockApi({})
    renderApp('/projects')
    expect(await screen.findByRole('heading', { name: 'Log in' })).toBeInTheDocument()
  })

  it('logs in and shows the projects list', async () => {
    const fetchMock = mockApi({
      'POST /api/auth/login': () => ({ json: authResponse }),
      'GET /api/projects': () => ({ json: [project()] }),
      'GET /api/health': () => ({ json: health }),
    })
    renderApp('/projects')

    await logIn()

    expect(await screen.findByRole('heading', { name: 'Projects' })).toBeInTheDocument()
    expect(await screen.findByText('Demo Shop')).toBeInTheDocument()
    expect(screen.getByText('alice@example.com')).toBeInTheDocument()
    expect(await screen.findByText('API: OK')).toBeInTheDocument()
    const projectsCall = fetchMock.mock.calls.find(([url]) => url === '/api/projects')
    expect(projectsCall?.[1]?.headers).toMatchObject({ Authorization: 'Bearer access-1' })
  })

  it('shows the error from a failed login', async () => {
    mockApi({ 'POST /api/auth/login': () => ({ status: 401, json: { detail: 'Invalid email or password.' } }) })
    renderApp('/login')

    await logIn()

    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid email or password.')
  })

  it('shows validation messages from registration', async () => {
    mockApi({
      'POST /api/auth/register': () => ({
        status: 409,
        json: { detail: 'An account with this email already exists.' },
      }),
    })
    renderApp('/register')
    const user = userEvent.setup()

    await user.type(await screen.findByLabelText('Name'), 'Alice')
    await user.type(screen.getByLabelText('Email'), 'alice@example.com')
    await user.type(screen.getByLabelText('Password'), 'correct-horse')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('already exists')
  })

  it('refreshes an expired access token once and retries', async () => {
    tokens.set({ access_token: 'expired', refresh_token: 'refresh-1' })
    const fetchMock = mockApi({
      'GET /api/projects': [() => ({ status: 401, json: { detail: 'Invalid or expired token' } }), () => ({ json: [] })],
      'POST /api/auth/refresh': (body) => {
        expect(body).toEqual({ refresh_token: 'refresh-1' })
        return { json: { access_token: 'fresh', refresh_token: 'refresh-2' } }
      },
    })

    await expect(apiFetch('/projects')).resolves.toEqual([])

    const lastCall = fetchMock.mock.calls.at(-1)
    expect(lastCall?.[1]?.headers).toMatchObject({ Authorization: 'Bearer fresh' })
    expect(localStorage.getItem('qa-pilot.refresh-token')).toBe('refresh-2')
  })

  it('restores the session from a saved refresh token', async () => {
    localStorage.setItem('qa-pilot.refresh-token', 'refresh-1')
    mockApi({
      'POST /api/auth/refresh': () => ({ json: { access_token: 'fresh', refresh_token: 'refresh-2' } }),
      'GET /api/auth/me': () => ({ json: aliceUser }),
      'GET /api/projects': () => ({ json: [] }),
      'GET /api/health': () => ({ json: health }),
    })

    renderApp('/projects')

    expect(await screen.findByText('No projects yet')).toBeInTheDocument()
  })
})

describe('projects', () => {
  function signedIn(handlers: Parameters<typeof mockApi>[0]) {
    tokens.set({ access_token: 'access-1', refresh_token: 'refresh-1' })
    return mockApi({
      'GET /api/auth/me': () => ({ json: aliceUser }),
      'GET /api/health': () => ({ json: health }),
      ...handlers,
    })
  }

  it('requires the authorised-testing checkbox before creating a project', async () => {
    const created: unknown[] = []
    signedIn({
      'POST /api/projects': (body) => {
        created.push(body)
        return { status: 201, json: project() }
      },
      'GET /api/projects/66f7a1c2e4b0a1b2c3d4e5f6': () => ({ json: project() }),
      'GET /api/projects/66f7a1c2e4b0a1b2c3d4e5f6/credentials': () => ({ json: [] }),
    })
    renderApp('/projects/new')
    const user = userEvent.setup()

    await user.type(await screen.findByLabelText('Project name'), 'Demo Shop')
    await user.type(screen.getByLabelText(/Base URL/), 'http://localhost:8080')
    const submit = screen.getByRole('button', { name: 'Create project' })
    expect(submit).toBeDisabled()

    await user.click(screen.getByLabelText('I own this site or am authorised to test it.'))
    expect(submit).toBeEnabled()
    await user.click(submit)

    expect(await screen.findByRole('heading', { name: 'Project settings' })).toBeInTheDocument()
    expect(created).toEqual([
      { name: 'Demo Shop', base_url: 'http://localhost:8080', authorised_testing_confirmed: true },
    ])
  })

  it('settings page hides passwords and locks security probes until "my own site"', async () => {
    signedIn({
      'GET /api/projects/66f7a1c2e4b0a1b2c3d4e5f6': () => ({ json: project() }),
      'GET /api/projects/66f7a1c2e4b0a1b2c3d4e5f6/credentials': () => ({
        json: [{ id: '66f7a1c2e4b0a1b2c3d4e5f1', label: 'Test shopper', username: 'demo@shop.test', created_at: '', updated_at: '' }],
      }),
    })
    renderApp('/projects/66f7a1c2e4b0a1b2c3d4e5f6/settings')

    const table = await screen.findByRole('table')
    expect(within(table).getByText('demo@shop.test')).toBeInTheDocument()
    expect(within(table).getByLabelText('Password hidden')).toHaveTextContent('••••••••')
    expect(screen.getByRole('checkbox', { name: /Enable security probes/ })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: /This is my own site/ })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Delete project' })).toBeDisabled()
  })

  it("shows 'not found' for a project you can't access", async () => {
    signedIn({ 'GET /api/projects/66f7a1c2e4b0a1b2c3d4e5ff': () => ({ status: 404, json: { detail: 'Project not found' } }) })
    renderApp('/projects/66f7a1c2e4b0a1b2c3d4e5ff/settings')

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Project not found' })).toBeInTheDocument())
  })
})

describe('public server limits', () => {
  it('asks for an invite code when the server requires one and sends it', async () => {
    const fetchMock = mockApi({
      'GET /api/health': () => ({ json: { ...health, registration_code_required: true } }),
      'POST /api/auth/register': () => ({ json: authResponse }),
      'GET /api/projects': () => ({ json: [] }),
    })
    renderApp('/register')
    const user = userEvent.setup()
    const code = await screen.findByLabelText('Invite code')
    await user.type(screen.getByLabelText('Name'), 'Alice')
    await user.type(screen.getByLabelText('Email'), 'alice@example.com')
    await user.type(screen.getByLabelText('Password'), 'correct-horse')
    await user.type(code, 'letmein')
    await user.click(screen.getByRole('button', { name: 'Create account' }))
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/api/auth/register'))
      expect(JSON.parse(String((call?.[1] as RequestInit).body))).toMatchObject({ invite_code: 'letmein' })
    })
  })

  it('hides the invite code field on open servers', async () => {
    mockApi({ 'GET /api/health': () => ({ json: health }) })
    renderApp('/register')
    expect(await screen.findByLabelText('Name')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByLabelText('Invite code')).not.toBeInTheDocument())
  })
})
