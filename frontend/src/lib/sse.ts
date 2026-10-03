// Minimal Server-Sent Events reader over fetch(). The browser's EventSource can't send an Authorization
// header, so we read the stream ourselves.

export type SseEvent = { event: string; data: string; id?: string }

/** Parse complete events out of `buffer`; returns them plus whatever incomplete text is left over. */
export function parseSseChunk(buffer: string): { events: SseEvent[]; rest: string } {
  const normalized = buffer.replace(/\r\n/g, '\n')
  const blocks = normalized.split('\n\n')
  const rest = blocks.pop() ?? ''
  const events: SseEvent[] = []
  for (const block of blocks) {
    let event = 'message'
    let id: string | undefined
    const data: string[] = []
    for (const line of block.split('\n')) {
      if (!line || line.startsWith(':')) continue // comment / keep-alive
      const colon = line.indexOf(':')
      const field = colon === -1 ? line : line.slice(0, colon)
      const value = colon === -1 ? '' : line.slice(colon + 1).replace(/^ /, '')
      if (field === 'event') event = value
      else if (field === 'data') data.push(value)
      else if (field === 'id') id = value
    }
    if (data.length) events.push({ event, data: data.join('\n'), id })
  }
  return { events, rest }
}

/** Read an SSE response body, calling onEvent for each event, until the stream closes or is aborted. */
export async function readSse(body: ReadableStream<Uint8Array>, onEvent: (e: SseEvent) => void): Promise<void> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const { events, rest } = parseSseChunk(buffer)
    buffer = rest
    events.forEach(onEvent)
  }
  const { events } = parseSseChunk(buffer + '\n\n')
  events.forEach(onEvent)
}
