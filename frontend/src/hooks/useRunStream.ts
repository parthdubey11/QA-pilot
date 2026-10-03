import { useEffect, useState } from 'react'
import type { Run, RunStep, TestCase } from '../api/runs'
import { authorizedFetch } from '../lib/api'
import { readSse } from '../lib/sse'

export type StreamState = {
  run: Run | null
  steps: RunStep[]
  testCases: TestCase[]
  /** 'live' while connected, 'ended' after the run finished, 'reconnecting' after a dropped connection. */
  connection: 'connecting' | 'live' | 'reconnecting' | 'ended' | 'error'
  error: string | null
}

const RETRY_MS = 2000

/**
 * Follow a run's step feed over SSE (GET /runs/:id/stream), resuming after the last step on reconnect.
 * State starts empty; remount (key={runId}) to follow a different run.
 */
export function useRunStream(runId: string): StreamState {
  const [state, setState] = useState<StreamState>({
    run: null,
    steps: [],
    testCases: [],
    connection: 'connecting',
    error: null,
  })

  useEffect(() => {
    const controller = new AbortController()
    let lastIndex = -1
    let ended = false

    async function connect(): Promise<void> {
      while (!ended && !controller.signal.aborted) {
        try {
          const res = await authorizedFetch(`/runs/${runId}/stream?after=${lastIndex}`, {
            headers: { Accept: 'text/event-stream' },
            signal: controller.signal,
          })
          if (res.status === 404) {
            setState((s) => ({ ...s, connection: 'error', error: 'Run not found' }))
            return
          }
          if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`)
          setState((s) => ({ ...s, connection: 'live', error: null }))
          await readSse(res.body, (event) => {
            const data = JSON.parse(event.data)
            if (event.event === 'step') {
              const step = data as RunStep
              if (step.index <= lastIndex) return
              lastIndex = step.index
              setState((s) => ({ ...s, steps: [...s.steps, step] }))
            } else if (event.event === 'test_case') {
              const tc = data as TestCase
              setState((s) => ({
                ...s,
                testCases: [...s.testCases.filter((t) => t.index !== tc.index), tc].sort((a, b) => a.index - b.index),
              }))
            } else if (event.event === 'run') {
              setState((s) => ({ ...s, run: data as Run }))
            } else if (event.event === 'end') {
              ended = true
              setState((s) => ({ ...s, connection: 'ended' }))
            }
          })
        } catch (err) {
          if (controller.signal.aborted) return
          setState((s) => ({ ...s, connection: 'reconnecting', error: err instanceof Error ? err.message : String(err) }))
        }
        if (!ended && !controller.signal.aborted) {
          setState((s) => (s.connection === 'live' ? { ...s, connection: 'reconnecting' } : s))
          await new Promise((resolve) => setTimeout(resolve, RETRY_MS))
        }
      }
    }

    void connect()
    return () => controller.abort()
  }, [runId])

  return state
}
