import { useQuery } from '@tanstack/react-query'
import { useState, type FormEvent, type ReactNode } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/useAuth'
import { Alert, Button, TextField } from '../components/ui'
import { errorText } from '../lib/errors'
import { fetchHealth } from '../api/health'

function AuthShell({ title, children, footer }: { title: string; children: ReactNode; footer: ReactNode }) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-50 px-4 py-12">
      <div className="w-full max-w-sm">
        <p className="text-center text-2xl font-bold tracking-tight text-slate-900">QA Pilot</p>
        <p className="mt-1 text-center text-sm text-slate-600">An autonomous QA engineer for teams that don't have one.</p>
        <div className="mt-8 rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
          <h1 className="text-lg font-semibold text-slate-900">{title}</h1>
          {children}
        </div>
        <p className="mt-4 text-center text-sm text-slate-600">{footer}</p>
      </div>
    </main>
  )
}

function useAfterAuthPath(): string {
  const from = (useLocation().state as { from?: string } | null)?.from
  return from && from !== '/login' && from !== '/register' ? from : '/projects'
}

export function LoginPage() {
  const { user, login } = useAuth()
  const navigate = useNavigate()
  const next = useAfterAuthPath()
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  if (user) return <Navigate to={next} replace />

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    setError('')
    setSubmitting(true)
    try {
      await login(String(form.get('email')), String(form.get('password')))
      navigate(next, { replace: true })
    } catch (err) {
      setError(errorText(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <AuthShell
      title="Log in"
      footer={
        <>
          New to QA Pilot? <Link to="/register" className="font-medium text-indigo-600 hover:underline">Create an account</Link>
        </>
      }
    >
      <form onSubmit={onSubmit} className="mt-4 space-y-4">
        <TextField label="Email" name="email" type="email" autoComplete="email" required />
        <TextField label="Password" name="password" type="password" autoComplete="current-password" required />
        <Alert>{error}</Alert>
        <Button type="submit" disabled={submitting} className="w-full">
          {submitting ? 'Logging in…' : 'Log in'}
        </Button>
      </form>
    </AuthShell>
  )
}

export function RegisterPage() {
  const { user, register } = useAuth()
  const navigate = useNavigate()
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const health = useQuery({ queryKey: ['health'], queryFn: fetchHealth, retry: false })

  if (user) return <Navigate to="/projects" replace />

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    setError('')
    setSubmitting(true)
    try {
      await register(String(form.get('name')), String(form.get('email')), String(form.get('password')), String(form.get('invite_code') ?? ''))
      navigate('/projects', { replace: true })
    } catch (err) {
      setError(errorText(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <AuthShell
      title="Create your account"
      footer={
        <>
          Already have an account? <Link to="/login" className="font-medium text-indigo-600 hover:underline">Log in</Link>
        </>
      }
    >
      <form onSubmit={onSubmit} className="mt-4 space-y-4">
        <TextField label="Name" name="name" autoComplete="name" required maxLength={100} />
        <TextField label="Email" name="email" type="email" autoComplete="email" required />
        <TextField
          label="Password"
          name="password"
          type="password"
          autoComplete="new-password"
          required
          minLength={8}
          hint="At least 8 characters."
        />
        {health.data?.registration_code_required && (
          <TextField label="Invite code" name="invite_code" required autoComplete="off" hint="This QA Pilot server is invite-only." />
        )}
        <Alert>{error}</Alert>
        <Button type="submit" disabled={submitting} className="w-full">
          {submitting ? 'Creating account…' : 'Create account'}
        </Button>
      </form>
    </AuthShell>
  )
}
