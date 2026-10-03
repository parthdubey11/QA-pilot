import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { listNotifications, markAllNotificationsRead, markNotificationRead, savedTestKeys } from '../api/savedTests'
import { Alert, Button } from '../components/ui'
import { errorText } from '../lib/errors'

export function NotificationsPage() {
  const queryClient = useQueryClient()
  const notes = useQuery({ queryKey: savedTestKeys.notifications, queryFn: listNotifications })
  const refresh = () => queryClient.invalidateQueries({ queryKey: savedTestKeys.notifications })
  const readOne = useMutation({ mutationFn: markNotificationRead, onSuccess: refresh })
  const readAll = useMutation({ mutationFn: markAllNotificationsRead, onSuccess: refresh })

  return (
    <>
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-bold tracking-tight text-slate-900">Notifications</h1>
        {!!notes.data?.unread && (
          <Button variant="secondary" className="ml-auto" disabled={readAll.isPending} onClick={() => readAll.mutate()}>
            Mark all as read
          </Button>
        )}
      </div>
      <p className="mt-1 text-sm text-slate-600">Scheduled runs let you know here when a saved test that used to pass starts failing.</p>
      <div className="mt-6">
        {notes.isPending && <p className="text-slate-500">Loading…</p>}
        {notes.isError && <Alert>{errorText(notes.error)}</Alert>}
        {notes.data?.items.length === 0 && <p className="text-sm text-slate-600">Nothing yet.</p>}
        {notes.data && notes.data.items.length > 0 && (
          <ul className="divide-y divide-slate-200 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm" aria-label="Notifications">
            {notes.data.items.map((n) => (
              <li key={n.id} className={`px-4 py-3 ${n.read ? '' : 'bg-indigo-50/60'}`}>
                <div className="flex flex-wrap items-start gap-3">
                  <div className="min-w-0 flex-1">
                    <p className={`text-sm ${n.read ? 'text-slate-800' : 'font-semibold text-slate-900'}`}>
                      {!n.read && <span className="sr-only">Unread: </span>}{n.title}
                    </p>
                    <p className="text-sm text-slate-700">{n.body}</p>
                    <p className="text-xs text-slate-500">
                      {new Date(n.created_at).toLocaleString()}
                      {n.run_id && <> · <Link to={`/runs/${n.run_id}/report`} className="text-indigo-600 hover:underline">view report</Link></>}
                    </p>
                  </div>
                  {!n.read && (
                    <button type="button" onClick={() => readOne.mutate(n.id)} className="text-sm font-medium text-indigo-600 hover:underline"
                      aria-label={`Mark "${n.title}" as read`}>
                      Mark as read
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  )
}
