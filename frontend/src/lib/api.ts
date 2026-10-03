// Small fetch wrapper for the QA Pilot API (served under /api by the Vite proxy).
// The access token lives in memory; the refresh token in localStorage so a reload keeps you signed in.
// On a 401 the client refreshes once and retries the request.

const REFRESH_KEY = 'qa-pilot.refresh-token'

let accessToken: string | null = null
let refreshInFlight: Promise<boolean> | null = null
let onAuthLost: (() => void) | null = null

export type TokenPair = { access_token: string; refresh_token: string }

export const tokens = {
  set(pair: TokenPair) {
    accessToken = pair.access_token
    localStorage.setItem(REFRESH_KEY, pair.refresh_token)
  },
  clear() {
    accessToken = null
    localStorage.removeItem(REFRESH_KEY)
  },
  hasRefresh(): boolean {
    return localStorage.getItem(REFRESH_KEY) !== null
  },
}

/** Called when the session can't be refreshed any more (e.g. refresh token expired). */
export function setAuthLostHandler(handler: (() => void) | null) {
  onAuthLost = handler
}

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

type ValidationIssue = { msg: string; loc?: (string | number)[] }

function errorMessage(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return (detail as ValidationIssue[])
      .map((issue) => issue.msg.replace(/^Value error, /, ''))
      .join(' ')
  }
  return status >= 500 ? 'Something went wrong on the server. Please try again.' : `Request failed (${status})`
}

async function refreshAccessToken(): Promise<boolean> {
  const refreshToken = localStorage.getItem(REFRESH_KEY)
  if (!refreshToken) return false
  refreshInFlight ??= (async () => {
    try {
      const res = await fetch('/api/auth/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refreshToken }),
      })
      if (!res.ok) return false
      tokens.set(await res.json())
      return true
    } catch {
      return false
    } finally {
      refreshInFlight = null
    }
  })()
  return refreshInFlight
}

/**
 * fetch() against the API with the access token attached. On a 401 it refreshes the token once and retries;
 * if that fails too, the session is cleared. Use this for responses that aren't plain JSON (streams, images).
 */
export async function authorizedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const send = () =>
    fetch(`/api${path}`, {
      ...init,
      headers: { ...(init.headers as Record<string, string>), ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}) },
    })

  if (!accessToken) await refreshAccessToken()
  let res = await send()
  if (res.status === 401) {
    if (await refreshAccessToken()) {
      res = await send()
    } else {
      tokens.clear()
      onAuthLost?.()
    }
  }
  return res
}

type RequestOptions = { method?: string; body?: unknown; auth?: boolean }

export async function apiFetch<T>(path: string, { method = 'GET', body, auth = true }: RequestOptions = {}): Promise<T> {
  const init: RequestInit = {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  }
  const res = auth ? await authorizedFetch(path, init) : await fetch(`/api${path}`, init)

  if (res.status === 204) return undefined as T
  const data: unknown = await res.json().catch(() => null)
  if (!res.ok) throw new ApiError(res.status, errorMessage(data, res.status))
  return data as T
}
