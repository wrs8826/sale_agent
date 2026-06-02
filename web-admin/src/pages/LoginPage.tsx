import React, { useState } from 'react'
import { Bot, Eye, EyeOff, Loader2 } from 'lucide-react'
import { useApp } from '../context/AppContext'

const LoginPage: React.FC = () => {
  const { setAuth, showToast } = useApp()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPw, setShowPw] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!username.trim() || !password) { setError('请填写用户名和密码'); return }
    setError('')
    setLoading(true)
    try {
      const r = await fetch('/auth/admin-login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: username.trim(), password }),
      })
      const data = await r.json()
      if (!r.ok) { setError(data.error || '登录失败'); return }
      setAuth({ username: data.username, role: data.role })
      showToast(`欢迎回来，${data.username}`)
    } catch {
      setError('网络错误，请检查后端服务是否启动')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-[#f7f7f8] flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="flex flex-col items-center mb-8">
          <div className="w-12 h-12 rounded-2xl bg-[#3b82f6] flex items-center justify-center mb-3 shadow-md">
            <Bot size={24} className="text-white" />
          </div>
          <h1 className="text-xl font-semibold text-[#171717]">销售 Agent 管理后台</h1>
          <p className="text-sm text-[#9ca3af] mt-1">请使用管理员账号登录</p>
        </div>

        {/* Card */}
        <form onSubmit={submit} className="bg-white rounded-2xl border border-[#e5e5e5] p-6 shadow-sm flex flex-col gap-4">
          <div>
            <label className="block text-xs font-medium text-[#6b6b6b] mb-1.5">用户名</label>
            <input
              value={username}
              onChange={e => setUsername(e.target.value)}
              placeholder="admin"
              autoFocus
              className="w-full px-3 py-2.5 border border-[#e5e5e5] rounded-lg text-sm focus:outline-none focus:border-[#3b82f6] transition-colors"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-[#6b6b6b] mb-1.5">密码</label>
            <div className="relative">
              <input
                type={showPw ? 'text' : 'password'}
                value={password}
                onChange={e => setPassword(e.target.value)}
                placeholder="••••••••"
                className="w-full px-3 py-2.5 pr-10 border border-[#e5e5e5] rounded-lg text-sm focus:outline-none focus:border-[#3b82f6] transition-colors"
              />
              <button type="button" onClick={() => setShowPw(s => !s)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-[#9ca3af] hover:text-[#6b6b6b]">
                {showPw ? <EyeOff size={15} /> : <Eye size={15} />}
              </button>
            </div>
          </div>

          {error && (
            <p className="text-xs text-red-500 bg-red-50 border border-red-100 rounded-lg px-3 py-2">{error}</p>
          )}

          <button type="submit" disabled={loading}
            className="w-full py-2.5 bg-[#3b82f6] hover:bg-[#2563eb] disabled:opacity-50 text-white rounded-lg text-sm font-semibold transition-colors flex items-center justify-center gap-2 mt-1">
            {loading && <Loader2 size={15} className="animate-spin" />}
            {loading ? '登录中…' : '登录'}
          </button>
        </form>

        <p className="text-center text-xs text-[#9ca3af] mt-4">
          默认账号：admin / admin123
        </p>
      </div>
    </div>
  )
}

export default LoginPage
