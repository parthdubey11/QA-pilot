import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import * as authApi from '../api/auth'
import { setAuthLostHandler, tokens } from '../lib/api'
import { AuthContext } from './context'

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const [user, setUser] = useState<authApi.User | null>(null)
  const [loading, setLoading] = useState(tokens.hasRefresh())

  const logout = useCallback(() => {
    tokens.clear()
    setUser(null)
    queryClient.clear()
  }, [queryClient])

  useEffect(() => {
    setAuthLostHandler(logout)
    return () => setAuthLostHandler(null)
  }, [logout])

  useEffect(() => {
    if (!tokens.hasRefresh()) return
    authApi
      .fetchMe()
      .then(setUser)
      .catch(() => tokens.clear())
      .finally(() => setLoading(false))
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    const res = await authApi.login(email, password)
    tokens.set(res)
    setUser(res.user)
  }, [])

  const register = useCallback(async (name: string, email: string, password: string, inviteCode = '') => {
    const res = await authApi.register(name, email, password, inviteCode)
    tokens.set(res)
    setUser(res.user)
  }, [])

  const value = useMemo(() => ({ user, loading, login, register, logout }), [user, loading, login, register, logout])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
