import { apiFetch, type TokenPair } from '../lib/api'

export type User = {
  id: string
  email: string
  name: string
  created_at: string
}

export type AuthResponse = TokenPair & { user: User }

export function login(email: string, password: string) {
  return apiFetch<AuthResponse>('/auth/login', { method: 'POST', body: { email, password }, auth: false })
}

export function register(name: string, email: string, password: string, inviteCode = '') {
  return apiFetch<AuthResponse>('/auth/register', { method: 'POST', body: { name, email, password, invite_code: inviteCode }, auth: false })
}

export function fetchMe() {
  return apiFetch<User>('/auth/me')
}
