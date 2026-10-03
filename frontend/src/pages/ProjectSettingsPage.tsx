import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  createCredential,
  deleteCredential,
  deleteProject,
  getProject,
  listCredentials,
  projectKeys,
  updateProject,
  type Project,
  type ProjectUpdate,
} from '../api/projects'
import { listRuns, runKeys } from '../api/runs'
import { RunStatusBadge } from '../components/RunStatusBadge'
import { ApiError } from '../lib/api'
import { Alert, Button, Card, TextField } from '../components/ui'
import { errorText } from '../lib/errors'
import { ProjectHeader } from '../components/ProjectHeader'

export function ProjectSettingsPage() {
  const projectId = useParams().projectId ?? ''
  const project = useQuery({
    queryKey: projectKeys.detail(projectId),
    queryFn: () => getProject(projectId),
    enabled: projectId !== '',
  })

  if (projectId === '' || (project.error instanceof ApiError && project.error.status === 404)) {
    return (
      <>
        <h1 className="text-2xl font-bold tracking-tight text-slate-900">Project not found</h1>
        <p className="mt-2 text-slate-600">
          It may have been deleted, or it belongs to someone else. <Link to="/projects" className="text-indigo-600 hover:underline">Back to projects</Link>
        </p>
      </>
    )
  }
  if (project.isPending) return <p className="text-slate-500">Loading project…</p>
  if (project.isError) return <Alert>{errorText(project.error)}</Alert>

  return (
    <>
      <ProjectHeader projectId={projectId} tab="settings" title="Settings" />
      <h2 className="text-lg font-semibold text-slate-900">Project settings</h2>
      <div className="mt-6 space-y-6">
        <RunsSection projectId={project.data.id} />
        <GeneralSection project={project.data} />
        <SafetySection project={project.data} />
        <CredentialsSection projectId={project.data.id} />
        <DangerSection project={project.data} />
      </div>
    </>
  )
}

function RunsSection({ projectId }: { projectId: string }) {
  const runs = useQuery({ queryKey: runKeys.forProject(projectId), queryFn: () => listRuns(projectId) })

  return (
    <Card title="Recent runs">
      {runs.isPending && <p className="text-sm text-slate-500">Loading…</p>}
      {runs.isError && <Alert>{errorText(runs.error)}</Alert>}
      {runs.data?.length === 0 && <p className="text-sm text-slate-600">No runs yet. Start one with <strong>New run</strong>.</p>}
      {runs.data && runs.data.length > 0 && (
        <ul className="divide-y divide-slate-100">
          {runs.data.slice(0, 5).map((run) => (
            <li key={run.id} className="flex items-center justify-between gap-3 py-2">
              <Link to={`/runs/${run.id}`} className="min-w-0 truncate text-sm font-medium text-indigo-600 hover:underline">
                {run.goal}
              </Link>
              <span className="flex shrink-0 items-center gap-3">
                <time dateTime={run.created_at} className="text-xs text-slate-500">{new Date(run.created_at).toLocaleString()}</time>
                <RunStatusBadge status={run.status} />
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function useUpdateProject(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (changes: ProjectUpdate) => updateProject(projectId, changes),
    onSuccess: (updated) => {
      queryClient.setQueryData(projectKeys.detail(projectId), updated)
      queryClient.invalidateQueries({ queryKey: projectKeys.all, exact: true })
    },
  })
}

function GeneralSection({ project }: { project: Project }) {
  const update = useUpdateProject(project.id)

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    update.mutate({ name: String(form.get('name')).trim(), base_url: String(form.get('base_url')).trim() })
  }

  return (
    <Card title="General">
      <form onSubmit={onSubmit} className="max-w-xl space-y-4">
        <TextField label="Project name" name="name" defaultValue={project.name} required maxLength={100} />
        <TextField label="Base URL" name="base_url" type="url" defaultValue={project.base_url} required />
        <Alert>{update.isError ? errorText(update.error) : ''}</Alert>
        <Alert tone="success">{update.isSuccess ? 'Changes saved.' : ''}</Alert>
        <Button type="submit" disabled={update.isPending}>
          {update.isPending ? 'Saving…' : 'Save changes'}
        </Button>
      </form>
    </Card>
  )
}

function SafetySection({ project }: { project: Project }) {
  const update = useUpdateProject(project.id)
  // Show the new state immediately; it's dropped once the server answers (or reverts if it refuses).
  const [draft, setDraft] = useState<ProjectUpdate | null>(null)
  const shown = { ...project, ...draft }
  if (draft?.is_own_site === false) shown.security_probes_enabled = false // mirrors the API rule

  function change(changes: ProjectUpdate) {
    setDraft(changes)
    update.mutate(changes, { onSettled: () => setDraft(null) })
  }

  return (
    <Card
      title="Testing safety"
      description="Security-style probes (XSS and SQL injection strings) are off by default. They can only be turned on for a site you've marked as your own."
    >
      <div className="space-y-3">
        <label className="flex items-start gap-2 text-sm text-slate-800">
          <input
            type="checkbox"
            checked={shown.is_own_site}
            disabled={update.isPending}
            onChange={(e) => change({ is_own_site: e.target.checked })}
            className="mt-0.5 h-4 w-4 rounded border-slate-300"
          />
          <span>
            <span className="font-medium">This is my own site</span>
            <span className="block text-slate-600">I built or operate it, not just have permission to test it.</span>
          </span>
        </label>
        <label className={`flex items-start gap-2 text-sm ${shown.is_own_site ? 'text-slate-800' : 'text-slate-400'}`}>
          <input
            type="checkbox"
            checked={shown.security_probes_enabled}
            disabled={!shown.is_own_site || update.isPending}
            onChange={(e) => change({ security_probes_enabled: e.target.checked })}
            className="mt-0.5 h-4 w-4 rounded border-slate-300"
          />
          <span>
            <span className="font-medium">Enable security probes</span>
            <span className="block">Lets the agents try XSS/SQLi strings in forms.</span>
          </span>
        </label>
        <Alert>{update.isError ? errorText(update.error) : ''}</Alert>
      </div>
    </Card>
  )
}

function CredentialsSection({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient()
  const credentials = useQuery({ queryKey: projectKeys.credentials(projectId), queryFn: () => listCredentials(projectId) })
  const refresh = () => queryClient.invalidateQueries({ queryKey: projectKeys.credentials(projectId) })
  const add = useMutation({
    mutationFn: (body: { label: string; username: string; password: string }) => createCredential(projectId, body),
    onSuccess: refresh,
  })
  const remove = useMutation({ mutationFn: (id: string) => deleteCredential(projectId, id), onSuccess: refresh })

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const formEl = event.currentTarget
    const form = new FormData(formEl)
    add.mutate(
      { label: String(form.get('label')).trim(), username: String(form.get('username')), password: String(form.get('password')) },
      { onSuccess: () => formEl.reset() },
    )
  }

  return (
    <Card
      title="Test credentials"
      description="Logins the agents can use on your site. Passwords are encrypted, never shown again, and never sent to the AI model."
    >
      {credentials.isPending && <p className="text-sm text-slate-500">Loading…</p>}
      {credentials.isError && <Alert>{errorText(credentials.error)}</Alert>}
      {credentials.data?.length === 0 && <p className="text-sm text-slate-600">No credentials yet.</p>}
      {credentials.data && credentials.data.length > 0 && (
        <table className="w-full text-left text-sm">
          <thead className="text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th scope="col" className="py-2 font-semibold">Label</th>
              <th scope="col" className="py-2 font-semibold">Username</th>
              <th scope="col" className="py-2 font-semibold">Password</th>
              <th scope="col" className="py-2"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {credentials.data.map((c) => (
              <tr key={c.id}>
                <td className="py-2 font-medium text-slate-900">{c.label}</td>
                <td className="py-2 text-slate-700">{c.username}</td>
                <td className="py-2 text-slate-500" aria-label="Password hidden">••••••••</td>
                <td className="py-2 text-right">
                  <button
                    type="button"
                    onClick={() => remove.mutate(c.id)}
                    disabled={remove.isPending}
                    aria-label={`Delete credential ${c.label}`}
                    className="text-sm font-medium text-rose-600 hover:underline disabled:text-rose-300"
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <Alert>{remove.isError ? errorText(remove.error) : ''}</Alert>

      <form onSubmit={onSubmit} className="mt-6 grid gap-4 border-t border-slate-100 pt-4 sm:grid-cols-3">
        <TextField label="Label" name="label" required maxLength={100} placeholder="Test shopper" />
        <TextField label="Username or email" name="username" required autoComplete="off" />
        <TextField label="Password" name="password" type="password" required autoComplete="new-password" />
        <div className="sm:col-span-3">
          <Alert>{add.isError ? errorText(add.error) : ''}</Alert>
          <Button type="submit" variant="secondary" disabled={add.isPending} className="mt-2">
            {add.isPending ? 'Adding…' : 'Add credential'}
          </Button>
        </div>
      </form>
    </Card>
  )
}

function DangerSection({ project }: { project: Project }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [confirmText, setConfirmText] = useState('')
  const remove = useMutation({
    mutationFn: () => deleteProject(project.id),
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: projectKeys.detail(project.id) })
      queryClient.invalidateQueries({ queryKey: projectKeys.all })
      navigate('/projects')
    },
  })

  return (
    <Card title="Delete project" description="Deletes the project and its saved credentials. This can't be undone.">
      <div className="max-w-xl space-y-3">
        <TextField
          label={`Type "${project.name}" to confirm`}
          value={confirmText}
          onChange={(e) => setConfirmText(e.target.value)}
          autoComplete="off"
        />
        <Alert>{remove.isError ? errorText(remove.error) : ''}</Alert>
        <Button variant="danger" disabled={confirmText !== project.name || remove.isPending} onClick={() => remove.mutate()}>
          {remove.isPending ? 'Deleting…' : 'Delete project'}
        </Button>
      </div>
    </Card>
  )
}
