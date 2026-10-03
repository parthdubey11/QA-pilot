export type HealthResponse = {
  status: 'ok' | 'degraded'
  database: 'ok' | 'unavailable'
  version: string
  registration_code_required?: boolean
  max_tests_limit?: number
}

export async function fetchHealth(): Promise<HealthResponse> {
  const res = await fetch('/api/health')
  if (!res.ok) {
    throw new Error(`Health check failed: HTTP ${res.status}`)
  }
  return (await res.json()) as HealthResponse
}
