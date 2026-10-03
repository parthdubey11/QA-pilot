import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { fetchHealth } from '../api/health'
import { getProject, projectKeys } from '../api/projects'
import { createRun, runKeys } from '../api/runs'
import { Alert, Button } from '../components/ui'
import { errorText } from '../lib/errors'
import { ProjectHeader } from '../components/ProjectHeader'

const DEFAULT_GOAL = ''

export function NewRunPage() {
  const projectId = useParams().projectId ?? ''
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const project = useQuery({ queryKey: projectKeys.detail(projectId), queryFn: () => getProject(projectId) })
  const [goal, setGoal] = useState(DEFAULT_GOAL)
  const [accessibility, setAccessibility] = useState(true)
  const [mobile, setMobile] = useState(false)
  const health = useQuery({ queryKey: ['health'], queryFn: fetchHealth, retry: false })
  const limit = health.data?.max_tests_limit ?? 15
  const [maxTests, setMaxTests] = useState(8)
  const tests = Math.min(maxTests, limit)
  const start = useMutation({
    mutationFn: () =>
      createRun(projectId, { goal: goal.trim(), options: { accessibility, mobile_viewport: mobile, max_tests: tests } }),
    onSuccess: (run) => {
      queryClient.invalidateQueries({ queryKey: runKeys.forProject(projectId) })
      navigate(`/runs/${run.id}`)
    },
  })

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    start.mutate()
  }

  return (
    <>
      <ProjectHeader projectId={projectId} tab={null} title="New run" />
      <h2 className="text-lg font-semibold text-slate-900">Start a new run</h2>
      {project.data && <p className="mt-1 text-sm text-slate-600">QA Pilot will test {project.data.base_url}</p>}

      <form onSubmit={onSubmit} className="mt-6 max-w-2xl space-y-5 rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex flex-col gap-1">
          <label htmlFor="goal" className="text-sm font-medium text-slate-700">What should QA Pilot test?</label>
          <textarea
            id="goal"
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            rows={4}
            maxLength={2000}
            required
            placeholder="e.g. test signup, login and cart"
            aria-describedby="goal-hint"
            className="rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/30"
          />
          <p id="goal-hint" className="text-xs text-slate-500">
            Plain English. QA Pilot explores the site, writes test cases (happy paths and edge cases) for this goal,
            runs each one in a real browser and judges the result. Add test logins in the project settings first if
            the flows need an account.
          </p>
        </div>

        <fieldset className="space-y-2">
          <legend className="text-sm font-medium text-slate-700">Options</legend>
          <label className="flex items-center gap-2 text-sm text-slate-800">
            <input type="checkbox" checked={accessibility} onChange={(e) => setAccessibility(e.target.checked)} className="h-4 w-4 rounded border-slate-300" />
            <span>
              Accessibility audit
              <span className="block text-xs text-slate-500">WCAG checks with axe-core, keyboard-only navigation and alt-text quality on every page</span>
            </span>
          </label>
          <label className="flex items-center gap-2 text-sm text-slate-800">
            <input type="checkbox" checked={mobile} onChange={(e) => setMobile(e.target.checked)} className="h-4 w-4 rounded border-slate-300" />
            Mobile viewport (390 × 844)
          </label>
          <label className="flex items-center gap-2 text-sm text-slate-800">
            Max tests
            <input
              type="number"
              min={1}
              max={limit}
              value={tests}
              onChange={(e) => setMaxTests(Math.min(limit, Math.max(1, Number(e.target.value) || 1)))}
              className="w-20 rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
            {limit < 15 && <span className="text-xs text-slate-500">(this server allows up to {limit})</span>}
          </label>
        </fieldset>

        <Alert>{start.isError ? errorText(start.error) : ''}</Alert>
        <div className="flex gap-3">
          <Button type="submit" disabled={start.isPending || !goal.trim()}>
            {start.isPending ? 'Starting…' : 'Start run'}
          </Button>
          <Button type="button" variant="secondary" onClick={() => navigate(-1)}>
            Cancel
          </Button>
        </div>
      </form>
    </>
  )
}
