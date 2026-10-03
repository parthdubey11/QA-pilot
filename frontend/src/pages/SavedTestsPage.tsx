import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  deleteSavedTest,
  describeLocators,
  describeStep,
  exportPath,
  listSavedTests,
  replaySavedTests,
  savedTestKeys,
  type SavedTest,
} from '../api/savedTests'
import { Alert, Button } from '../components/ui'
import { downloadFile } from '../lib/download'
import { errorText } from '../lib/errors'
import { ProjectHeader } from '../components/ProjectHeader'

const resultStyle = {
  passed: 'bg-emerald-100 text-emerald-800',
  failed: 'bg-rose-100 text-rose-800',
  never: 'bg-slate-100 text-slate-700',
}

function LocatorList({ step }: { step: SavedTest['steps'][number] }) {
  const l = step.locators
  if (!l) return null
  const rows: [string, string | null][] = [
    ['role + name', l.role && l.name ? `${l.role} "${l.name}"` : null],
    ['text', l.text],
    ['CSS', l.css],
    ['position', l.x !== null && l.y !== null ? `(${l.x}, ${l.y}) <${l.tag}>` : null],
  ]
  return (
    <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 text-xs">
      {rows.filter(([, v]) => v).map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-slate-500">{k}</dt>
          <dd className="break-all font-mono text-slate-800">{v}</dd>
        </div>
      ))}
    </dl>
  )
}

function SavedTestItem({
  test,
  selected,
  onToggle,
  onDelete,
}: {
  test: SavedTest
  selected: boolean
  onToggle: () => void
  onDelete: () => void
}) {
  const download = useMutation({ mutationFn: () => downloadFile(exportPath(test.id), 'saved-test.spec.ts') })
  return (
    <li className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-start gap-3">
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggle}
          aria-label={`Select ${test.title}`}
          className="mt-1 h-4 w-4 rounded border-slate-300"
        />
        <div className="min-w-0 flex-1">
          <h2 className="font-semibold text-slate-900">{test.title}</h2>
          <p className="text-xs text-slate-500">
            {test.steps.length} steps · starts at {test.start_path} · replayed {test.replays}× ·{' '}
            {test.heal_history.length} heal{test.heal_history.length === 1 ? '' : 's'}
            {test.last_run_id && (
              <>
                {' · '}
                <Link to={`/runs/${test.last_run_id}`} className="text-indigo-600 hover:underline">last run</Link>
              </>
            )}
          </p>
        </div>
        <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold capitalize ${resultStyle[test.last_result]}`}>
          {test.last_result === 'never' ? 'not run' : test.last_result}
        </span>
        <Button variant="secondary" onClick={() => download.mutate()} disabled={download.isPending} className="!px-3 !py-1.5">
          Export as Playwright script
        </Button>
        <button type="button" onClick={onDelete} className="text-sm font-medium text-rose-700 hover:underline" aria-label={`Delete ${test.title}`}>
          Delete
        </button>
      </div>
      <Alert>{download.isError ? errorText(download.error) : ''}</Alert>
      <details className="mt-3">
        <summary className="cursor-pointer text-sm font-medium text-indigo-600">Steps and locators</summary>
        <ol className="mt-2 space-y-2">
          {test.steps.map((step, i) => (
            <li key={i} className="rounded bg-slate-50 p-2 text-sm">
              <span className="font-medium text-slate-900">{i + 1}. {describeStep(step)}</span>
              {step.note && <span className="block text-xs italic text-slate-600">{step.note}</span>}
              <LocatorList step={step} />
            </li>
          ))}
        </ol>
        {test.assertions.length > 0 && (
          <p className="mt-2 text-sm text-slate-700">
            <span className="font-semibold">Checks: </span>
            {test.assertions.map((a) => (a.kind === 'url' ? `ends on ${a.value}` : `"${a.value}" is shown`)).join('; ')}
          </p>
        )}
      </details>
      <details className="mt-2">
        <summary className="cursor-pointer text-sm font-medium text-indigo-600">Heal history ({test.heal_history.length})</summary>
        {test.heal_history.length === 0 ? (
          <p className="mt-2 text-sm text-slate-600">No repairs yet.</p>
        ) : (
          <ol className="mt-2 space-y-2" aria-label={`Heal history of ${test.title}`}>
            {[...test.heal_history].reverse().map((h, i) => (
              <li key={i} className="rounded border border-amber-200 bg-amber-50 p-2 text-sm">
                <span className="font-medium text-slate-900">
                  Step {h.step_index + 1}: {describeLocators(h.old)} → {describeLocators(h.new)}
                </span>
                <span className="block text-xs text-slate-700">
                  {h.method === 'ai-healer' ? 'AI healer' : 'Fallback locator'} · {h.reason} ·{' '}
                  {new Date(h.at).toLocaleString()} ·{' '}
                  <Link to={`/runs/${h.run_id}`} className="text-indigo-600 hover:underline">run</Link>
                </span>
              </li>
            ))}
          </ol>
        )}
      </details>
    </li>
  )
}

export function SavedTestsPage() {
  const projectId = useParams().projectId ?? ''
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<string[]>([])
  const tests = useQuery({ queryKey: savedTestKeys.list(projectId), queryFn: () => listSavedTests(projectId) })
  const replay = useMutation({
    mutationFn: (ids: string[]) => replaySavedTests(projectId, ids),
    onSuccess: (run) => navigate(`/runs/${run.id}`),
  })
  const remove = useMutation({
    mutationFn: deleteSavedTest,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: savedTestKeys.list(projectId) }),
  })

  const toggle = (id: string) => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]))

  return (
    <>
      <ProjectHeader projectId={projectId} tab="tests" title="Saved tests" />
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold text-slate-900">Saved tests</h2>
        <span className="ml-auto flex gap-2">
          {selected.length > 0 && (
            <Button variant="secondary" disabled={replay.isPending} onClick={() => replay.mutate(selected)}>
              Replay selected ({selected.length})
            </Button>
          )}
          <Button disabled={replay.isPending || !tests.data?.length} onClick={() => replay.mutate([])}>
            {replay.isPending ? 'Starting…' : 'Replay all'}
          </Button>
        </span>
      </div>
      <p className="mt-1 text-sm text-slate-600">
        Tests that passed are saved with several locators per element and replayed without the AI. If the page changed,
        the healer repairs the step and records it in the heal history.
      </p>
      <div className="mt-3"><Alert>{replay.isError ? errorText(replay.error) : remove.isError ? errorText(remove.error) : ''}</Alert></div>

      {tests.isPending && <p className="mt-6 text-slate-500">Loading saved tests…</p>}
      {tests.isError && <Alert>{errorText(tests.error)}</Alert>}
      {tests.data?.length === 0 && (
        <p className="mt-6 rounded-lg border border-dashed border-slate-300 bg-white p-8 text-center text-sm text-slate-600">
          No saved tests yet. Tests that pass in a <Link to={`/projects/${projectId}/runs/new`} className="text-indigo-600 hover:underline">run</Link> are saved here automatically.
        </p>
      )}
      {tests.data && tests.data.length > 0 && (
        <ul className="mt-6 space-y-3" aria-label="Saved tests">
          {tests.data.map((t) => (
            <SavedTestItem key={t.id} test={t} selected={selected.includes(t.id)} onToggle={() => toggle(t.id)}
              onDelete={() => remove.mutate(t.id)} />
          ))}
        </ul>
      )}
    </>
  )
}
