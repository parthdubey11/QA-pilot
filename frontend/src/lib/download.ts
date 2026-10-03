import { ApiError, authorizedFetch } from './api'

/** Download an authenticated API file (a plain <a href> can't send the Authorization header). */
export async function downloadFile(path: string, fallbackName: string): Promise<void> {
  const res = await authorizedFetch(path)
  if (!res.ok) throw new ApiError(res.status, `Download failed (HTTP ${res.status})`)
  const disposition = res.headers.get('Content-Disposition') ?? ''
  const name = /filename="([^"]+)"/.exec(disposition)?.[1] ?? fallbackName
  const url = URL.createObjectURL(await res.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = name
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
