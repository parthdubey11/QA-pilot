import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import { askQuestion, assistantKeys, listQuestions, type AssistantAnswer, type AssistantSource, type SourceKind } from '../api/assistant'
import { ProjectHeader } from '../components/ProjectHeader'
import { Alert, Button, Card } from '../components/ui'
import { errorText } from '../lib/errors'

const EXAMPLES = [
  'Which bugs keep coming back after being fixed?',
  'Were there accessibility problems on the checkout page?',
  'What did the self-healing change in our saved tests?',
]

const KIND_LABELS: Record<SourceKind, string> = { bug: 'Bug', test: 'Test result', a11y: 'Accessibility', heal: 'Repair' }

function sourceLink(source: AssistantSource, projectId: string): string | null {
  if (source.kind === 'bug') return `/bugs/${source.ref_id}`
  if (source.kind === 'heal') return `/projects/${projectId}/tests`
  return source.run_id ? `/runs/${source.run_id}/report` : null
}

/** "… negative quantities [2] …": each [n] becomes a link to source n of this answer. */
function withCitations(text: string, answerId: string, sources: AssistantSource[]): ReactNode[] {
  const known = new Set(sources.map((s) => s.n))
  return text.split(/(\[\d+\])/).map((part, i) => {
    const n = Number(part.match(/^\[(\d+)\]$/)?.[1])
    if (!known.has(n)) return part
    return (
      <a key={i} href={`#source-${answerId}-${n}`} className="font-semibold text-indigo-600 hover:underline" aria-label={`source ${n}`}>
        [{n}]
      </a>
    )
  })
}

function AnswerCard({ item, projectId }: { item: AssistantAnswer; projectId: string }) {
  return (
    <article className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm" aria-label={item.question}>
      <h3 className="font-semibold text-slate-900">{item.question}</h3>
      <p className="mt-2 whitespace-pre-line text-sm leading-relaxed text-slate-800">{withCitations(item.answer, item.id, item.sources)}</p>
      {item.sources.length > 0 && (
        <ol className="mt-4 space-y-2" aria-label="Sources">
          {item.sources.map((source) => {
            const to = sourceLink(source, projectId)
            return (
              <li key={source.n} id={`source-${item.id}-${source.n}`} className="rounded-md bg-slate-50 px-3 py-2 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-semibold text-slate-500">[{source.n}]</span>
                  <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-700 ring-1 ring-indigo-600/20">
                    {KIND_LABELS[source.kind]}
                  </span>
                  {to ? (
                    <Link to={to} className="font-medium text-indigo-600 hover:underline">{source.title}</Link>
                  ) : (
                    <span className="font-medium text-slate-800">{source.title}</span>
                  )}
                  <span className="text-xs text-slate-500">{Math.round(source.similarity * 100)}% match</span>
                </div>
              </li>
            )
          })}
        </ol>
      )}
      <details className="mt-3 text-xs text-slate-500">
        <summary className="cursor-pointer">
          How this was answered ({item.llm_calls} LLM call{item.llm_calls === 1 ? '' : 's'}) · {new Date(item.created_at).toLocaleString()}
        </summary>
        <ol className="mt-2 list-decimal space-y-0.5 pl-5">
          {item.trace.map((line, i) => <li key={i}>{line}</li>)}
        </ol>
      </details>
    </article>
  )
}

export function AskPage() {
  const projectId = useParams().projectId ?? ''
  const queryClient = useQueryClient()
  const [question, setQuestion] = useState('')
  const history = useQuery({ queryKey: assistantKeys.history(projectId), queryFn: () => listQuestions(projectId) })
  const ask = useMutation({
    mutationFn: (q: string) => askQuestion(projectId, q),
    onSuccess: (answer) => {
      queryClient.setQueryData<AssistantAnswer[]>(assistantKeys.history(projectId), (old) => [answer, ...(old ?? [])])
      setQuestion('')
    },
  })

  function submit(event: { preventDefault(): void }) {
    event.preventDefault()
    if (question.trim().length >= 3 && !ask.isPending) ask.mutate(question.trim())
  }

  return (
    <>
      <ProjectHeader projectId={projectId} tab="ask" />
      <Card
        title="Ask about this project's testing history"
        description="Answers come from this project's bugs, test results, accessibility issues and self-healing repairs. QA Pilot finds the most relevant ones by semantic search, checks which ones help, and cites them."
      >
        <form onSubmit={submit} className="space-y-3">
          <label htmlFor="ask-question" className="sr-only">Question</label>
          <textarea
            id="ask-question"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) submit(e)
            }}
            rows={2}
            maxLength={500}
            placeholder="e.g. Is the signup flow still broken?"
            className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/30"
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" disabled={ask.isPending || question.trim().length < 3}>
              {ask.isPending ? 'Searching…' : 'Ask'}
            </Button>
            {EXAMPLES.map((example) => (
              <button
                key={example}
                type="button"
                onClick={() => setQuestion(example)}
                className="rounded-full border border-slate-200 px-3 py-1 text-xs text-slate-600 hover:border-indigo-300 hover:text-indigo-700"
              >
                {example}
              </button>
            ))}
          </div>
          {ask.isPending && (
            <p role="status" className="text-sm text-slate-500">
              Searching the history and asking the LLM… on a free LLM tier this can take up to a minute.
            </p>
          )}
          <Alert>{ask.isError ? errorText(ask.error) : ''}</Alert>
        </form>
      </Card>

      <section className="mt-6 space-y-4" aria-label="Answers">
        {history.isPending && <p className="text-slate-500">Loading earlier questions…</p>}
        {history.isError && <Alert>{errorText(history.error)}</Alert>}
        {history.data?.length === 0 && <p className="text-sm text-slate-500">No questions yet.</p>}
        {history.data?.map((item) => <AnswerCard key={item.id} item={item} projectId={projectId} />)}
      </section>
    </>
  )
}
