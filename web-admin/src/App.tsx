import React, { Suspense, lazy } from 'react'
import { useApp } from './context/AppContext'
import Sidebar from './components/Sidebar'
import { ToastContainer } from './components/Toast'
import LoginPage from './pages/LoginPage'
import { Loader2 } from 'lucide-react'

const KnowledgePage = lazy(() => import('./pages/KnowledgePage'))
const ChatPage      = lazy(() => import('./pages/ChatPage'))
const UsersPage     = lazy(() => import('./pages/UsersPage'))
const SettingsPage  = lazy(() => import('./pages/SettingsPage'))
const PolicySkillPage = lazy(() => import('./pages/PolicySkillPage'))

const Spinner = () => (
  <div className="flex items-center justify-center h-full">
    <Loader2 size={22} className="animate-spin text-[#3b82f6]" />
  </div>
)

const App: React.FC = () => {
  const { currentPage, toasts, auth, authLoading } = useApp()

  // Full-screen spinner while checking session
  if (authLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-[#f7f7f8]">
        <Loader2 size={28} className="animate-spin text-[#3b82f6]" />
      </div>
    )
  }

  // Not logged in → show login page
  if (!auth) return <><LoginPage /><ToastContainer toasts={toasts} /></>

  // Non-admin somehow reached here
  if (auth.role !== 'admin') return (
    <div className="flex flex-col items-center justify-center min-h-screen gap-3">
      <p className="text-[#6b6b6b]">该账号无管理员权限</p>
      <button onClick={() => fetch('/auth/logout', { method: 'POST' }).then(() => window.location.reload())}
        className="px-4 py-2 text-sm bg-[#3b82f6] text-white rounded-lg">退出</button>
    </div>
  )

  const Page = () => {
    switch (currentPage) {
      case 'knowledge': return <KnowledgePage />
      case 'chat':      return <ChatPage />
      case 'policy':    return <PolicySkillPage />
      case 'users':     return <UsersPage />
      case 'settings':  return <SettingsPage />
    }
  }

  return (
    <div className="flex h-screen overflow-hidden bg-white">
      <Sidebar />
      <main className="ml-[260px] flex-1 flex flex-col overflow-hidden">
        <Suspense fallback={<Spinner />}>
          <Page />
        </Suspense>
      </main>
      <ToastContainer toasts={toasts} />
    </div>
  )
}

export default App
