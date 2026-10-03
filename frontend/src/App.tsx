import { lazy, Suspense } from 'react'
import { Link, Navigate, Route, Routes } from 'react-router-dom'
import { RequireAuth } from './auth/RequireAuth'
import { AppLayout } from './components/AppLayout'
import { AccessibilityPage } from './pages/AccessibilityPage'
import { BugDetailPage } from './pages/BugDetailPage'
import { BugsPage } from './pages/BugsPage'
import { LoginPage, RegisterPage } from './pages/AuthPages'
import { NewProjectPage } from './pages/NewProjectPage'
import { NewRunPage } from './pages/NewRunPage'
import { NotificationsPage } from './pages/NotificationsPage'
import { ProjectSettingsPage } from './pages/ProjectSettingsPage'
import { ProjectsPage } from './pages/ProjectsPage'
import { RunPage } from './pages/RunPage'
import { RunReportPage } from './pages/RunReportPage'
import { SavedTestsPage } from './pages/SavedTestsPage'
import { SchedulesPage } from './pages/SchedulesPage'

// The charts library is only needed on the overview page: load it on demand to keep the main bundle small.
const ProjectOverviewPage = lazy(() => import('./pages/ProjectOverviewPage').then((m) => ({ default: m.ProjectOverviewPage })))

function NotFound() {
  return (
    <main className="p-8">
      <h1 className="text-2xl font-bold text-slate-900">Page not found</h1>
      <Link to="/" className="mt-2 inline-block text-indigo-600 hover:underline">
        Go to QA Pilot
      </Link>
    </main>
  )
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route element={<RequireAuth />}>
        <Route element={<AppLayout />}>
          <Route index element={<Navigate to="/projects" replace />} />
          <Route path="/projects" element={<ProjectsPage />} />
          <Route path="/projects/new" element={<NewProjectPage />} />
          <Route path="/projects/:projectId" element={<Suspense fallback={<p className="text-slate-500">Loading overview…</p>}><ProjectOverviewPage /></Suspense>} />
          <Route path="/projects/:projectId/settings" element={<ProjectSettingsPage />} />
          <Route path="/projects/:projectId/runs/new" element={<NewRunPage />} />
          <Route path="/projects/:projectId/accessibility" element={<AccessibilityPage />} />
          <Route path="/projects/:projectId/tests" element={<SavedTestsPage />} />
          <Route path="/projects/:projectId/schedules" element={<SchedulesPage />} />
          <Route path="/notifications" element={<NotificationsPage />} />
          <Route path="/runs/:runId" element={<RunPage />} />
          <Route path="/runs/:runId/report" element={<RunReportPage />} />
          <Route path="/bugs" element={<BugsPage />} />
          <Route path="/bugs/:bugId" element={<BugDetailPage />} />
        </Route>
      </Route>
      <Route path="*" element={<NotFound />} />
    </Routes>
  )
}
