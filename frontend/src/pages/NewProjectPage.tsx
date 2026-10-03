import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { createProject, projectKeys } from '../api/projects'
import { Alert, Button, TextField } from '../components/ui'
import { errorText } from '../lib/errors'

export function NewProjectPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [authorised, setAuthorised] = useState(false)
  const create = useMutation({
    mutationFn: createProject,
    onSuccess: (project) => {
      queryClient.invalidateQueries({ queryKey: projectKeys.all })
      navigate(`/projects/${project.id}/settings`)
    },
  })

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    create.mutate({
      name: String(form.get('name')).trim(),
      base_url: String(form.get('base_url')).trim(),
      authorised_testing_confirmed: authorised,
    })
  }

  return (
    <>
      <h1 className="text-2xl font-bold tracking-tight text-slate-900">New project</h1>
      <p className="mt-1 text-sm text-slate-600">A project is one website that QA Pilot tests.</p>

      <form onSubmit={onSubmit} className="mt-6 max-w-xl space-y-5 rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
        <TextField label="Project name" name="name" required maxLength={100} placeholder="My shop" />
        <TextField
          label="Base URL"
          name="base_url"
          type="url"
          required
          placeholder="https://example.com"
          hint="Where testing starts, e.g. your home page."
        />

        <div className="rounded-md border border-amber-200 bg-amber-50 p-4">
          <p className="text-sm font-semibold text-amber-900">Only test sites you're allowed to test</p>
          <p className="mt-1 text-sm text-amber-900">
            QA Pilot opens a real browser, clicks buttons and submits forms on this site. Only add websites that you
            own or have written permission to test. Testing someone else's site without permission may be illegal.
          </p>
          <label className="mt-3 flex items-start gap-2 text-sm font-medium text-amber-950">
            <input
              type="checkbox"
              name="authorised"
              checked={authorised}
              onChange={(e) => setAuthorised(e.target.checked)}
              className="mt-0.5 h-4 w-4 rounded border-amber-400"
            />
            I own this site or am authorised to test it.
          </label>
        </div>

        <Alert>{create.isError ? errorText(create.error) : ''}</Alert>
        <div className="flex gap-3">
          <Button type="submit" disabled={!authorised || create.isPending}>
            {create.isPending ? 'Creating…' : 'Create project'}
          </Button>
          <Button type="button" variant="secondary" onClick={() => navigate('/projects')}>
            Cancel
          </Button>
        </div>
      </form>
    </>
  )
}
