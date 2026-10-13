import { apiFetch } from '../lib/api'

export type SourceKind = 'bug' | 'test' | 'a11y' | 'heal'

export type AssistantSource = {
  n: number
  kind: SourceKind
  ref_id: string
  title: string
  run_id: string | null
  similarity: number
  excerpt: string
}

export type AssistantAnswer = {
  id: string
  question: string
  answer: string
  sources: AssistantSource[]
  trace: string[]
  llm_calls: number
  created_at: string
}

export const assistantKeys = {
  history: (projectId: string) => ['assistant', projectId] as const,
}

export const listQuestions = (projectId: string) => apiFetch<AssistantAnswer[]>(`/projects/${projectId}/assistant`)

/** RAG over the project's bugs, test results, accessibility issues and repairs (2-4 LLM calls). */
export const askQuestion = (projectId: string, question: string) =>
  apiFetch<AssistantAnswer>(`/projects/${projectId}/assistant`, { method: 'POST', body: { question } })
