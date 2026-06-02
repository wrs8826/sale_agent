import React from 'react'
import { BookOpen, MessageSquare, Users, Settings, LogOut, Bot } from 'lucide-react'
import { useApp } from '../context/AppContext'
import type { Page } from '../types'

const items: { page: Page; icon: React.ReactNode; label: string }[] = [
  { page: 'knowledge', icon: <BookOpen size={18} />,      label: '知识库管理' },
  { page: 'chat',      icon: <MessageSquare size={18} />, label: 'Agent 对话' },
  { page: 'users',     icon: <Users size={18} />,         label: '用户管理' },
  { page: 'settings',  icon: <Settings size={18} />,      label: '系统设置' },
]

const Sidebar: React.FC = () => {
  const { currentPage, setCurrentPage, setAuth } = useApp()

  return (
    <aside className="w-[260px] shrink-0 h-screen flex flex-col bg-[#f7f7f8] border-r border-[#e5e5e5] fixed left-0 top-0 bottom-0 z-10">
      {/* Logo */}
      <div className="flex items-center gap-3 px-5 py-5 border-b border-[#e5e5e5]">
        <div className="w-8 h-8 rounded-lg bg-[#3b82f6] flex items-center justify-center shrink-0">
          <Bot size={18} className="text-white" />
        </div>
        <div>
          <div className="text-sm font-semibold text-[#171717]">销售 Agent</div>
          <div className="text-[11px] text-[#9ca3af]">管理后台</div>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-4 flex flex-col gap-0.5">
        {items.map(({ page, icon, label }) => {
          const active = currentPage === page
          return (
            <button
              key={page}
              onClick={() => setCurrentPage(page)}
              className={`
                w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium
                transition-all duration-150 text-left
                ${active
                  ? 'bg-white text-[#3b82f6] shadow-sm border border-[#e5e5e5]'
                  : 'text-[#6b6b6b] hover:bg-white/60 hover:text-[#171717]'}
              `}
            >
              <span className={active ? 'text-[#3b82f6]' : 'text-[#9ca3af]'}>{icon}</span>
              {label}
            </button>
          )
        })}
      </nav>

      {/* Logout */}
      <div className="px-3 pb-5 border-t border-[#e5e5e5] pt-3">
        <button
          onClick={() => fetch('/auth/logout', { method: 'POST' }).then(() => setAuth(null))}
          className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm text-[#6b6b6b] hover:bg-white/60 hover:text-[#171717] transition-all duration-150"
        >
          <LogOut size={18} className="text-[#9ca3af]" />
          退出登录
        </button>
      </div>
    </aside>
  )
}

export default Sidebar
