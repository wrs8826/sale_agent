import React, { createContext, useContext, useState, useCallback, useEffect } from 'react'
import type { Page, ToastItem, ToastType, AuthInfo } from '../types'

interface AppCtx {
  currentPage: Page
  setCurrentPage: (p: Page) => void
  toasts: ToastItem[]
  showToast: (msg: string, type?: ToastType) => void
  auth: AuthInfo | null
  authLoading: boolean
  setAuth: (a: AuthInfo | null) => void
}

const Ctx = createContext<AppCtx | null>(null)

export const useApp = () => {
  const c = useContext(Ctx)
  if (!c) throw new Error('useApp outside AppProvider')
  return c
}

export const AppProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [currentPage, setCurrentPage] = useState<Page>('knowledge')
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const [auth, setAuth] = useState<AuthInfo | null>(null)
  const [authLoading, setAuthLoading] = useState(true)

  // Check session on mount
  useEffect(() => {
    fetch('/auth/me')
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then((data: AuthInfo) => setAuth(data))
      .catch(() => setAuth(null))
      .finally(() => setAuthLoading(false))
  }, [])

  const showToast = useCallback((msg: string, type: ToastType = 'success') => {
    const id = Math.random().toString(36).slice(2)
    setToasts(prev => [...prev, { id, message: msg, type }])
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 3500)
  }, [])

  return (
    <Ctx.Provider value={{ currentPage, setCurrentPage, toasts, showToast, auth, authLoading, setAuth }}>
      {children}
    </Ctx.Provider>
  )
}
