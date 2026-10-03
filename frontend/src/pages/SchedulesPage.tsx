import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import { createSchedule, deleteSchedule, listSchedules, savedTestKeys, updateSchedule, type Schedule } from '../api/savedTests'
import { Alert, Button, Card, TextField } from '../components/ui'
import { errorText } from '../lib/errors'
import { ProjectHeader } from '../components/ProjectHeader'

const PRESETS: { label: string; cron: string }[] = [
  { label: 'Every hour', cron: '0 * * * *' },
  { label: 'Every day at 02:00', cron: '0 2 * * *' },
  { label: 'Weekdays at 09:00', cron: '0 9 * * 1-5' },
  { label: 'Every Monday at 08:00', cron: '0 8 * * 1' },
]

function describeCron(cron: string): string {
  return PRESETS.find((p) => p.cron === cron)?.label ?? `Cron: ${cron}`
}

function ScheduleRow({ schedule, projectId }: { schedule: Schedule; projectId: string }) {
  const queryClient = useQueryClient()
  const refresh = () => queryClient.invalidateQueries({ queryKey: savedTestKeys.schedules(projectId) })
  const toggle = useMutation({ mutationFn: (enabled: boolean) => updateSchedule(schedule.id, { enabled }), onSuccess: refresh })
  const remove = useMutation({ mutationFn: () => deleteSchedule(schedule.id), onSuccess: refresh })

  return (
    <li className="flex flex-wrap items-center gap-4 px-4 py-3">
      <div className="min-w-0 flex-1">
        <p className="font-medium text-slate-900">{schedule.name}</p>
        <p className="text-xs text-slate-600">
          {describeCron(schedule.cron)} ({schedule.cron}, {schedule.timezone})
          {schedule.next_run_at && <> · next run {new Date(schedule.next_run_at).toLocaleString()}</>}
        </p>
        {schedule.last_triggered_at && (
          <p className="text-xs text-slate-500">
            Last triggered {new Date(schedule.last_triggered_at).toLocaleString()}
            {schedule.last_run_id && !schedule.last_skip_reason && (
              <> · <Link to={`/runs/${schedule.last_run_id}/report`} className="text-indigo-600 hover:underline">report</Link></>
            )}
            {schedule.last_skip_reason && <> · {schedule.last_skip_reason}</>}
          </p>
        )}
        <Alert>{toggle.isError ? errorText(toggle.error) : remove.isError ? errorText(remove.error) : ''}</Alert>
      </div>
      <label className="flex items-center gap-2 text-sm text-slate-800">
        <input
          type="checkbox"
          checked={schedule.enabled}
          disabled={toggle.isPending}
          onChange={(e) => toggle.mutate(e.target.checked)}
          className="h-4 w-4 rounded border-slate-300"
        />
        Enabled<span className="sr-only">: {schedule.name}</span>
      </label>
      <button type="button" onClick={() => remove.mutate()} disabled={remove.isPending}
        className="text-sm font-medium text-rose-700 hover:underline" aria-label={`Delete schedule ${schedule.name}`}>
        Delete
      </button>
    </li>
  )
}

export function SchedulesPage() {
  const projectId = useParams().projectId ?? ''
  const queryClient = useQueryClient()
  const schedules = useQuery({ queryKey: savedTestKeys.schedules(projectId), queryFn: () => listSchedules(projectId) })
  const [cron, setCron] = useState(PRESETS[1].cron)
  const create = useMutation({
    mutationFn: (body: { name: string; cron: string; timezone: string; enabled: boolean }) => createSchedule(projectId, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: savedTestKeys.schedules(projectId) }),
  })

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const formEl = event.currentTarget
    const form = new FormData(formEl)
    create.mutate(
      { name: String(form.get('name')).trim(), cron: cron.trim(), timezone: String(form.get('timezone')).trim() || 'UTC', enabled: true },
      { onSuccess: () => formEl.reset() },
    )
  }

  return (
    <>
      <ProjectHeader projectId={projectId} tab="schedules" title="Schedules" />
      <h2 className="text-lg font-semibold text-slate-900">Schedules</h2>
      <p className="mt-1 text-sm text-slate-600">
        Scheduled runs replay this project's <Link to={`/projects/${projectId}/tests`} className="text-indigo-600 hover:underline">saved tests</Link>.
        When a saved test that used to pass fails, you get a notification in QA Pilot.
      </p>

      <div className="mt-6 space-y-6">
        <Card title="Your schedules">
          {schedules.isPending && <p className="text-sm text-slate-500">Loading…</p>}
          {schedules.isError && <Alert>{errorText(schedules.error)}</Alert>}
          {schedules.data?.length === 0 && <p className="text-sm text-slate-600">No schedules yet.</p>}
          {schedules.data && schedules.data.length > 0 && (
            <ul className="-mx-4 divide-y divide-slate-100" aria-label="Schedules">
              {schedules.data.map((s) => <ScheduleRow key={s.id} schedule={s} projectId={projectId} />)}
            </ul>
          )}
        </Card>

        <Card title="New schedule">
          <form onSubmit={onSubmit} className="max-w-xl space-y-4">
            <TextField label="Name" name="name" required maxLength={100} placeholder="Nightly regression" />
            <div className="flex flex-col gap-1">
              <label htmlFor="preset" className="text-sm font-medium text-slate-700">How often</label>
              <select
                id="preset"
                value={PRESETS.some((p) => p.cron === cron) ? cron : 'custom'}
                onChange={(e) => e.target.value !== 'custom' && setCron(e.target.value)}
                className="rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm"
              >
                {PRESETS.map((p) => <option key={p.cron} value={p.cron}>{p.label}</option>)}
                <option value="custom">Custom (cron)</option>
              </select>
            </div>
            <TextField
              label="Cron expression"
              name="cron"
              value={cron}
              onChange={(e) => setCron(e.target.value)}
              required
              hint="5 fields: minute hour day-of-month month day-of-week, e.g. 30 6 * * 1-5"
            />
            <TextField label="Time zone" name="timezone" defaultValue="UTC" hint="e.g. UTC or Asia/Kolkata" />
            <Alert>{create.isError ? errorText(create.error) : ''}</Alert>
            <Button type="submit" disabled={create.isPending}>{create.isPending ? 'Saving…' : 'Create schedule'}</Button>
          </form>
        </Card>
      </div>
    </>
  )
}
