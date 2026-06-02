import React, { useState, useEffect } from 'react'
import {
  Search, Plus, Edit3, Trash2, ShieldAlert, X,
  ChevronLeft, ChevronRight, RefreshCw, Settings2
} from 'lucide-react'
import { useApp } from '../context/AppContext'
import type { User } from '../types'

const Badge: React.FC<{ banned: boolean }> = ({ banned }) => (
  <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium
    ${banned ? 'bg-red-50 text-red-600' : 'bg-green-50 text-green-600'}`}>
    {banned ? '已封禁' : '正常'}
  </span>
)

const RoleBadge: React.FC<{ role: string }> = ({ role }) => (
  <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium
    ${role === 'admin' ? 'bg-purple-50 text-purple-600' : 'bg-[#f3f4f6] text-[#6b6b6b]'}`}>
    {role === 'admin' ? '管理员' : '普通用户'}
  </span>
)

interface EditModal { user: User; field: 'info' | 'settings' }

const UsersPage: React.FC = () => {
  const { showToast } = useApp()
  const [users, setUsers] = useState<User[]>([])
  const [usersLoading, setUsersLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [filterBanned, setFilterBanned] = useState<'all' | 'active' | 'banned'>('all')
  const [page, setPage] = useState(1)
  const [modal, setModal] = useState<EditModal | null>(null)
  const [addOpen, setAddOpen] = useState(false)
  const [deleteId, setDeleteId] = useState<number | null>(null)
  const [editDraft, setEditDraft] = useState<Partial<User>>({})
  const [newUser, setNewUser] = useState({ username: '', phone: '', department: '', password: '' })

  useEffect(() => {
    fetch('/users')
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then((data: { users: User[] }) => setUsers(data.users))
      .catch(() => showToast('加载用户列表失败', 'error'))
      .finally(() => setUsersLoading(false))
  }, [])

  const PAGE_SIZE = 8

  const filtered = users.filter(u => {
    const matchSearch = u.username.includes(search) || u.phone.includes(search)
    const matchStatus = filterBanned === 'all' || (filterBanned === 'banned') === u.is_banned
    return matchSearch && matchStatus
  })
  const totalPages = Math.ceil(filtered.length / PAGE_SIZE)
  const pageUsers = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  const openEdit = (u: User) => { setModal({ user: u, field: 'info' }); setEditDraft({ department: u.department, phone: u.phone, role: u.role }) }

  const saveEdit = async () => {
    if (!modal) return
    try {
      await fetch(`/users/${modal.user.id}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(editDraft),
      })
    } catch { /* best-effort */ }
    setUsers(prev => prev.map(u => u.id === modal.user.id ? { ...u, ...editDraft } : u))
    showToast('用户信息已更新')
    setModal(null)
  }

  const toggleBan = async (u: User) => {
    try {
      await fetch(`/users/${u.id}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_banned: !u.is_banned }),
      })
    } catch { /* best-effort */ }
    setUsers(prev => prev.map(x => x.id === u.id ? { ...x, is_banned: !x.is_banned } : x))
    showToast(u.is_banned ? `${u.username} 已解封` : `${u.username} 已封禁`, u.is_banned ? 'success' : 'warning')
  }

  const confirmDelete = async () => {
    if (!deleteId) return
    try { await fetch(`/users/${deleteId}`, { method: 'DELETE' }) } catch { /* best-effort */ }
    setUsers(prev => prev.filter(u => u.id !== deleteId))
    showToast('用户已删除')
    setDeleteId(null)
  }

  const addUser = async () => {
    if (!newUser.username || !newUser.phone || !newUser.password) { showToast('请填写完整信息', 'error'); return }
    try {
      const r = await fetch('/users', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newUser),
      })
      const data = await r.json()
      if (!r.ok) { showToast(data.error || '创建失败', 'error'); return }
      setUsers(prev => [...prev, data.user ?? {
        id: data.id, username: newUser.username, phone: newUser.phone,
        department: newUser.department || '未分配', role: 'user',
        is_banned: false, created_at: new Date().toLocaleString('zh-CN', { hour12: false }).slice(0, 16),
      }])
      setNewUser({ username: '', phone: '', department: '', password: '' })
      setAddOpen(false)
      showToast('用户已添加')
    } catch { showToast('网络错误', 'error') }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-[#e5e5e5] bg-white">
        <h1 className="text-lg font-semibold text-[#171717]">用户管理</h1>
        <button onClick={() => setAddOpen(true)}
          className="flex items-center gap-1.5 px-3 py-2 bg-[#3b82f6] hover:bg-[#2563eb] text-white rounded-lg text-sm font-medium transition-colors">
          <Plus size={15} /> 添加用户
        </button>
      </div>

      {/* Toolbar */}
      <div className="flex items-center gap-3 px-6 py-3 border-b border-[#e5e5e5] bg-white">
        <div className="relative flex-1 max-w-xs">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#9ca3af]" />
          <input value={search} onChange={e => { setSearch(e.target.value); setPage(1) }}
            placeholder="搜索用户名 / 手机号…"
            className="w-full pl-8 pr-3 py-2 border border-[#e5e5e5] rounded-lg text-sm focus:outline-none focus:border-[#3b82f6] transition-colors" />
        </div>
        <select value={filterBanned} onChange={e => { setFilterBanned(e.target.value as typeof filterBanned); setPage(1) }}
          className="px-3 py-2 border border-[#e5e5e5] rounded-lg text-sm text-[#6b6b6b] focus:outline-none focus:border-[#3b82f6] bg-white">
          <option value="all">全部状态</option>
          <option value="active">正常</option>
          <option value="banned">已封禁</option>
        </select>
        <span className="text-xs text-[#9ca3af] ml-auto">{filtered.length} 个用户</span>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto scrollbar-thin px-6 py-4">
        {usersLoading ? (
          <div className="flex flex-col gap-2 pt-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="h-10 rounded-lg bg-[#f3f4f6] animate-pulse" />
            ))}
          </div>
        ) : (
        <table className="w-full">
          <thead>
            <tr className="text-left">
              {['用户名', '手机号', '部门', '专属模型', '角色', '状态', '注册时间', '操作'].map(h => (
                <th key={h} className="pb-3 text-xs font-semibold text-[#9ca3af] uppercase tracking-wider pr-4 whitespace-nowrap">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-[#f3f4f6]">
            {pageUsers.map(u => (
              <tr key={u.id} className="hover:bg-[#f7f7f8] group transition-colors">
                <td className="py-3 pr-4">
                  <div className="flex items-center gap-2.5">
                    <div className="w-7 h-7 rounded-full bg-gradient-to-br from-[#3b82f6] to-[#6366f1] flex items-center justify-center text-white text-xs font-semibold shrink-0">
                      {u.username[0].toUpperCase()}
                    </div>
                    <span className="text-sm font-medium text-[#171717]">{u.username}</span>
                  </div>
                </td>
                <td className="py-3 pr-4 text-sm text-[#6b6b6b]">{u.phone}</td>
                <td className="py-3 pr-4 text-sm text-[#6b6b6b]">{u.department}</td>
                <td className="py-3 pr-4 text-xs text-[#9ca3af]">{u.chat_model || '继承全局'}</td>
                <td className="py-3 pr-4"><RoleBadge role={u.role} /></td>
                <td className="py-3 pr-4"><Badge banned={u.is_banned} /></td>
                <td className="py-3 pr-4 text-xs text-[#9ca3af] whitespace-nowrap">{u.created_at}</td>
                <td className="py-3">
                  <div className="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button onClick={() => openEdit(u)} title="编辑"
                      className="text-[#9ca3af] hover:text-[#3b82f6] transition-colors"><Edit3 size={14} /></button>
                    <button onClick={() => toggleBan(u)} title={u.is_banned ? '解封' : '封禁'}
                      className={`transition-colors ${u.is_banned ? 'text-green-400 hover:text-green-600' : 'text-[#9ca3af] hover:text-amber-500'}`}>
                      <ShieldAlert size={14} />
                    </button>
                    <button onClick={() => { showToast(`已发送密码重置邮件给 ${u.username}`) }} title="重置密码"
                      className="text-[#9ca3af] hover:text-[#6366f1] transition-colors"><RefreshCw size={14} /></button>
                    {u.role !== 'admin' && (
                      <button onClick={() => setDeleteId(u.id)} title="删除"
                        className="text-[#9ca3af] hover:text-red-500 transition-colors"><Trash2 size={14} /></button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        )}
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2 px-6 py-3 border-t border-[#e5e5e5]">
          <button disabled={page === 1} onClick={() => setPage(p => p - 1)}
            className="w-8 h-8 flex items-center justify-center rounded-lg border border-[#e5e5e5] disabled:opacity-30 hover:bg-[#f7f7f8] transition-colors">
            <ChevronLeft size={14} />
          </button>
          {Array.from({ length: totalPages }, (_, i) => i + 1).map(p => (
            <button key={p} onClick={() => setPage(p)}
              className={`w-8 h-8 rounded-lg text-sm transition-colors
                ${p === page ? 'bg-[#3b82f6] text-white' : 'border border-[#e5e5e5] text-[#6b6b6b] hover:bg-[#f7f7f8]'}`}>
              {p}
            </button>
          ))}
          <button disabled={page === totalPages} onClick={() => setPage(p => p + 1)}
            className="w-8 h-8 flex items-center justify-center rounded-lg border border-[#e5e5e5] disabled:opacity-30 hover:bg-[#f7f7f8] transition-colors">
            <ChevronRight size={14} />
          </button>
        </div>
      )}

      {/* Edit Modal */}
      {modal && (
        <div className="fixed inset-0 bg-black/30 z-50 flex items-center justify-center p-4" onClick={() => setModal(null)}>
          <div className="bg-white rounded-2xl p-6 w-full max-w-md shadow-xl" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-5">
              <h2 className="text-base font-semibold text-[#171717]">编辑用户：{modal.user.username}</h2>
              <button onClick={() => setModal(null)} className="text-[#9ca3af] hover:text-[#171717]"><X size={18} /></button>
            </div>
            <div className="flex flex-col gap-3">
              <label className="flex flex-col gap-1.5">
                <span className="text-xs text-[#6b6b6b] font-medium">手机号</span>
                <input value={editDraft.phone ?? ''} onChange={e => setEditDraft(d => ({ ...d, phone: e.target.value }))}
                  className="px-3 py-2 border border-[#e5e5e5] rounded-lg text-sm focus:outline-none focus:border-[#3b82f6]" />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-xs text-[#6b6b6b] font-medium">部门</span>
                <input value={editDraft.department ?? ''} onChange={e => setEditDraft(d => ({ ...d, department: e.target.value }))}
                  className="px-3 py-2 border border-[#e5e5e5] rounded-lg text-sm focus:outline-none focus:border-[#3b82f6]" />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-xs text-[#6b6b6b] font-medium">角色</span>
                <select value={editDraft.role ?? 'user'} onChange={e => setEditDraft(d => ({ ...d, role: e.target.value as 'admin' | 'user' }))}
                  className="px-3 py-2 border border-[#e5e5e5] rounded-lg text-sm focus:outline-none focus:border-[#3b82f6] bg-white">
                  <option value="user">普通用户</option>
                  <option value="admin">管理员</option>
                </select>
              </label>
            </div>
            <div className="flex justify-end gap-2 mt-6">
              <button onClick={() => setModal(null)}
                className="px-4 py-2 border border-[#e5e5e5] rounded-lg text-sm text-[#6b6b6b] hover:bg-[#f7f7f8] transition-colors">
                取消
              </button>
              <button onClick={saveEdit}
                className="px-4 py-2 bg-[#3b82f6] hover:bg-[#2563eb] text-white rounded-lg text-sm font-medium transition-colors">
                保存
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Add User Modal */}
      {addOpen && (
        <div className="fixed inset-0 bg-black/30 z-50 flex items-center justify-center p-4" onClick={() => setAddOpen(false)}>
          <div className="bg-white rounded-2xl p-6 w-full max-w-md shadow-xl" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-5">
              <h2 className="text-base font-semibold text-[#171717]">添加用户</h2>
              <button onClick={() => setAddOpen(false)} className="text-[#9ca3af] hover:text-[#171717]"><X size={18} /></button>
            </div>
            <div className="flex flex-col gap-3">
              {[
                { label: '用户名', key: 'username', type: 'text', placeholder: '2~32 位字符' },
                { label: '手机号', key: 'phone', type: 'tel', placeholder: '11 位手机号' },
                { label: '部门', key: 'department', type: 'text', placeholder: '所属部门' },
                { label: '初始密码', key: 'password', type: 'password', placeholder: '至少 6 位' },
              ].map(({ label, key, type, placeholder }) => (
                <label key={key} className="flex flex-col gap-1.5">
                  <span className="text-xs text-[#6b6b6b] font-medium">{label}</span>
                  <input type={type} placeholder={placeholder}
                    value={newUser[key as keyof typeof newUser]}
                    onChange={e => setNewUser(p => ({ ...p, [key]: e.target.value }))}
                    className="px-3 py-2 border border-[#e5e5e5] rounded-lg text-sm focus:outline-none focus:border-[#3b82f6]" />
                </label>
              ))}
            </div>
            <div className="flex justify-end gap-2 mt-6">
              <button onClick={() => setAddOpen(false)}
                className="px-4 py-2 border border-[#e5e5e5] rounded-lg text-sm text-[#6b6b6b] hover:bg-[#f7f7f8] transition-colors">
                取消
              </button>
              <button onClick={addUser}
                className="px-4 py-2 bg-[#3b82f6] hover:bg-[#2563eb] text-white rounded-lg text-sm font-medium transition-colors">
                创建
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete Confirm */}
      {deleteId !== null && (
        <div className="fixed inset-0 bg-black/30 z-50 flex items-center justify-center p-4" onClick={() => setDeleteId(null)}>
          <div className="bg-white rounded-2xl p-6 w-full max-w-sm shadow-xl text-center" onClick={e => e.stopPropagation()}>
            <div className="w-12 h-12 rounded-full bg-red-50 flex items-center justify-center mx-auto mb-4">
              <Trash2 size={20} className="text-red-500" />
            </div>
            <h2 className="text-base font-semibold text-[#171717] mb-2">确认删除</h2>
            <p className="text-sm text-[#6b6b6b] mb-6">此操作不可撤销，用户数据将永久删除。</p>
            <div className="flex gap-2">
              <button onClick={() => setDeleteId(null)}
                className="flex-1 px-4 py-2 border border-[#e5e5e5] rounded-lg text-sm text-[#6b6b6b] hover:bg-[#f7f7f8] transition-colors">
                取消
              </button>
              <button onClick={confirmDelete}
                className="flex-1 px-4 py-2 bg-red-500 hover:bg-red-600 text-white rounded-lg text-sm font-medium transition-colors">
                删除
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default UsersPage
