import { useQuery } from '@tanstack/react-query'
import { fetchHealth } from '../api/health'

function Dot({ ok }: { ok: boolean }) {
  return <span aria-hidden="true" className={`h-2 w-2 rounded-full ${ok ? 'bg-emerald-500' : 'bg-rose-500'}`} />
}

/** Compact API/database status, shown at the bottom of the sidebar. */
export function HealthStatus() {
  const health = useQuery({ queryKey: ['health'], queryFn: fetchHealth, refetchInterval: 15000, retry: false })

  if (health.isPending) return <p className="text-xs text-slate-400">Checking status…</p>
  const apiOk = !health.isError
  const dbOk = health.data?.database === 'ok'
  return (
    <ul className="space-y-1 text-xs text-slate-300" aria-label="System status">
      <li className="flex items-center gap-2">
        <Dot ok={apiOk} />
        API: {apiOk ? 'OK' : 'UNREACHABLE'}
      </li>
      {health.data && (
        <li className="flex items-center gap-2">
          <Dot ok={dbOk} />
          Database: {dbOk ? 'OK' : 'UNAVAILABLE'}
        </li>
      )}
    </ul>
  )
}
