import { useEffect, useState } from 'react'
import { authorizedFetch } from '../lib/api'

/** An <img> for an API URL that needs the Authorization header (plain <img src> can't send it).
 * Give it key={path} so a new image starts from the loading state. */
export function AuthImage({ path, alt, className }: { path: string; alt: string; className?: string }) {
  const [src, setSrc] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let objectUrl: string | null = null
    let cancelled = false
    authorizedFetch(path)
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const blob = await res.blob()
        if (cancelled) return
        objectUrl = URL.createObjectURL(blob)
        setSrc(objectUrl)
      })
      .catch(() => !cancelled && setFailed(true))
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [path])

  if (failed) return <p className="p-4 text-sm text-slate-500">Couldn't load the screenshot.</p>
  if (!src) return <div className="aspect-[16/10] animate-pulse rounded bg-slate-100" aria-label="Loading screenshot" />
  return <img src={src} alt={alt} className={className} />
}
