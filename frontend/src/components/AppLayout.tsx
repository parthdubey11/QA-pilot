import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/useAuth'
import { listNotifications, savedTestKeys } from '../api/savedTests'
import { HealthStatus } from './HealthStatus'

const navLinkClass = ({ isActive }: { isActive: boolean }) =>
  `block rounded-md px-3 py-2 text-sm font-medium ${isActive ? 'bg-slate-800 text-white' : 'text-slate-300 hover:bg-slate-800 hover:text-white'}`

export function AppLayout() {
  const notifications = useQuery({ queryKey: savedTestKeys.notifications, queryFn: listNotifications, refetchInterval: 30000 })
  const unread = notifications.data?.unread ?? 0
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const [menuOpen, setMenuOpen] = useState(false) // small screens only; md+ always shows the sidebar

  return (
    <div className="min-h-screen bg-slate-50 md:flex">
      <aside className="bg-slate-900 md:fixed md:inset-y-0 md:flex md:w-60 md:flex-col md:px-4 md:py-5">
        <div className="flex items-center justify-between px-4 py-3 md:p-0">
          <p className="px-3 text-lg font-bold tracking-tight text-white">QA Pilot</p>
          <button
            type="button"
            onClick={() => setMenuOpen((open) => !open)}
            aria-expanded={menuOpen}
            aria-controls="app-menu"
            className="rounded-md border border-slate-700 px-3 py-1.5 text-sm font-medium text-slate-200 hover:bg-slate-800 md:hidden"
          >
            {menuOpen ? 'Close menu' : 'Menu'}
            {unread > 0 && !menuOpen && <span className="ml-2 rounded-full bg-rose-600 px-1.5 text-xs text-white">{unread}</span>}
          </button>
        </div>
        <div
          id="app-menu"
          className={`${menuOpen ? 'flex' : 'hidden'} flex-col px-4 pb-4 md:flex md:flex-1 md:p-0`}
          onClick={(e) => (e.target as HTMLElement).closest('a') && setMenuOpen(false)}
        >
        <nav aria-label="Main" className="mt-2 flex-1 md:mt-6">
          <ul className="space-y-1">
            <li>
              <NavLink to="/projects" end className={navLinkClass}>
                Projects
              </NavLink>
            </li>
            <li>
              <NavLink to="/projects/new" className={navLinkClass}>
                New project
              </NavLink>
            </li>
            <li>
              <NavLink to="/bugs" className={navLinkClass}>
                Bugs
              </NavLink>
            </li>
            <li>
              <NavLink to="/notifications" className={navLinkClass}>
                <span className="flex items-center justify-between">
                  Notifications{' '}
                  {unread > 0 && (
                    <span className="rounded-full bg-rose-600 px-2 text-xs font-semibold text-white">
                      {unread}{' '}<span className="sr-only">unread</span>
                    </span>
                  )}
                </span>
              </NavLink>
            </li>
          </ul>
        </nav>
        <div className="mt-6 space-y-4 border-t border-slate-800 px-3 pt-4">
          <HealthStatus />
          <div>
            <p className="truncate text-sm font-medium text-white">{user?.name}</p>
            <p className="truncate text-xs text-slate-400">{user?.email}</p>
            <button
              type="button"
              onClick={() => {
                logout()
                navigate('/login')
              }}
              className="mt-2 text-sm font-medium text-slate-300 underline-offset-2 hover:text-white hover:underline"
            >
              Log out
            </button>
          </div>
        </div>
        </div>
      </aside>
      <main className="flex-1 px-4 py-8 sm:px-8 md:ml-60">
        <div className="mx-auto max-w-6xl">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
