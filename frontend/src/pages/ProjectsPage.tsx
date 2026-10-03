import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { listProjects, projectKeys } from '../api/projects'
import { Alert } from '../components/ui'
import { errorText } from '../lib/errors'
import { ProjectAvatar } from '../components/ProjectHeader'

const linkButton =
  'inline-flex items-center rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600'

export function ProjectsPage() {
  const projects = useQuery({ queryKey: projectKeys.all, queryFn: listProjects })

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-2xl font-bold tracking-tight text-slate-900">Projects</h1>
        <Link to="/projects/new" className={linkButton}>
          New project
        </Link>
      </div>

      <div className="mt-6">
        {projects.isPending && <p className="text-slate-500">Loading projects…</p>}
        {projects.isError && <Alert>{errorText(projects.error)}</Alert>}
        {projects.data?.length === 0 && (
          <div className="rounded-lg border border-dashed border-slate-300 bg-white p-10 text-center">
            <h2 className="font-semibold text-slate-900">No projects yet</h2>
            <p className="mt-1 text-sm text-slate-600">Add the website you want QA Pilot to test.</p>
            <Link to="/projects/new" className={`${linkButton} mt-4`}>
              Create your first project
            </Link>
          </div>
        )}
        {projects.data && projects.data.length > 0 && (
          <ul className="grid gap-4 md:grid-cols-2">
            {projects.data.map((project) => (
              <li key={project.id} className="flex flex-col gap-4 overflow-hidden rounded-xl border border-slate-200 bg-white p-5 shadow-sm transition hover:-translate-y-0.5 hover:border-indigo-200 hover:shadow-md">
                <div className="flex min-w-0 items-center gap-3">
                  <ProjectAvatar name={project.name} size="md" />
                  <div className="min-w-0">
                  <h2 className="truncate font-semibold text-slate-900">
                    <Link to={`/projects/${project.id}`} className="hover:text-indigo-700 hover:underline">{project.name}</Link>
                  </h2>
                  <p className="truncate text-sm text-slate-600">{project.base_url}</p>
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-slate-100 pt-3">
                  {project.is_own_site && (
                    <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700">
                      My own site
                    </span>
                  )}
                  <Link
                    to={`/projects/${project.id}/runs/new`}
                    className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-semibold text-white shadow-sm hover:bg-indigo-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
                    aria-label={`New run for ${project.name}`}
                  >
                    New run
                  </Link>
                  <Link
                    to={`/projects/${project.id}/tests`}
                    className="text-sm font-medium text-indigo-600 hover:underline"
                    aria-label={`Saved tests of ${project.name}`}
                  >
                    Saved tests
                  </Link>
                  <Link
                    to={`/projects/${project.id}/accessibility`}
                    className="text-sm font-medium text-indigo-600 hover:underline"
                    aria-label={`Accessibility of ${project.name}`}
                  >
                    Accessibility
                  </Link>
                  <Link
                    to={`/projects/${project.id}/settings`}
                    className="text-sm font-medium text-indigo-600 hover:underline"
                    aria-label={`Settings for ${project.name}`}
                  >
                    Settings
                  </Link>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  )
}
