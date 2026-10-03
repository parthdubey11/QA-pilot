import { apiFetch } from '../lib/api'

export type Project = {
  id: string
  name: string
  base_url: string
  is_own_site: boolean
  security_probes_enabled: boolean
  authorised_at: string
  created_at: string
  updated_at: string
}

export type ProjectCreate = {
  name: string
  base_url: string
  authorised_testing_confirmed: boolean
}

export type ProjectUpdate = Partial<Pick<Project, 'name' | 'base_url' | 'is_own_site' | 'security_probes_enabled'>>

/** A test-site login. The API never returns the password. */
export type Credential = {
  id: string
  label: string
  username: string
  created_at: string
  updated_at: string
}

export type CredentialCreate = { label: string; username: string; password: string }

export const projectKeys = {
  all: ['projects'] as const,
  detail: (id: string) => ['projects', id] as const,
  credentials: (id: string) => ['projects', id, 'credentials'] as const,
}

export const listProjects = () => apiFetch<Project[]>('/projects')
export const getProject = (id: string) => apiFetch<Project>(`/projects/${id}`)
export const createProject = (body: ProjectCreate) => apiFetch<Project>('/projects', { method: 'POST', body })
export const updateProject = (id: string, body: ProjectUpdate) =>
  apiFetch<Project>(`/projects/${id}`, { method: 'PATCH', body })
export const deleteProject = (id: string) => apiFetch<void>(`/projects/${id}`, { method: 'DELETE' })

export const listCredentials = (projectId: string) => apiFetch<Credential[]>(`/projects/${projectId}/credentials`)
export const createCredential = (projectId: string, body: CredentialCreate) =>
  apiFetch<Credential>(`/projects/${projectId}/credentials`, { method: 'POST', body })
export const deleteCredential = (projectId: string, credentialId: string) =>
  apiFetch<void>(`/projects/${projectId}/credentials/${credentialId}`, { method: 'DELETE' })
