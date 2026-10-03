import { createContext } from 'react'
import type { User } from '../api/auth'

export type AuthContextValue = {
  user: User | null
  /** True until we've checked whether a saved session is still valid. */
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  register: (name: string, email: string, password: string, inviteCode?: string) => Promise<void>
  logout: () => void
}

export const AuthContext = createContext<AuthContextValue | null>(null)
