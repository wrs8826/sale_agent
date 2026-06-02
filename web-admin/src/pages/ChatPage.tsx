import React, { useState, useRef, useEffect, useCallback } from 'react'
import {
  Send, Trash2, Bot, User, AlertCircle, Star,
  Plus, MessageSquare, Pencil, X, Check, Copy, ChevronDown,
  Circle, UserCircle,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import { useApp } from '../context/AppContext'
import type { ChatMessage } from '../types'

const genId = () => Math.random().toString(36).slice(2)

const fmtTime = () =>
  new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })

const fmtDate = (iso: string) => {
  const d = new Date(iso)
  const now = new Date()
  const diff = now.getTime() - d.getTime()
  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`
  if (diff < 86400000 * 7) return `${Math.floor(diff / 86400000)} 天前`
  return d.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
}

interface ConvMeta {
  id: string
  user_id?: number
  title: string
  updated_at: string
}

interface MsgItem extends ChatMessage {
  time?: string
}

const ChatPage: React.FC = () => {
  const { showToast, auth } = useApp()
  const myUserId = auth?.user_id

  const [messages, setMessages] = useState<MsgItem[]>([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [thinking, setThinking] = useState(false)
  const [convId, setConvId] = useState<string | undefined>()
  const [convUserId, setConvUserId] = useState<number | undefined>()   // 当前会话的归属用户
  const [convTitle, setConvTitle] = useState('Agent 对话')
  const [convSub, setConvSub] = useState('基于知识库回答业务问题')
  const [showInfo, setShowInfo] = useState(true)
  const [rating, setRating] = useState(0)
  const [feedbackOpen, setFeedbackOpen] = useState(false)
  const [feedbackExpanded, setFeedbackExpanded] = useState(false)
  const [feedbackComment, setFeedbackComment] = useState('')
  const [copied, setCopied] = useState<string | null>(null)

  // 只读模式：查看他人会话时为 true，禁止发消息
  const readOnly = convUserId !== undefined && myUserId !== undefined && convUserId !== myUserId

  // KB status
  const [kbStatus, setKbStatus] = useState<'checking' | 'ready' | 'empty' | 'error'>('checking')
  const [kbText, setKbText] = useState('检查知识库状态…')

  const bottomRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const abortRef = useRef<(() => void) | null>(null)

  // Conversation sidebar
  const [convList, setConvList] = useState<ConvMeta[]>([])
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameVal, setRenameVal] = useState('')
  const [userMap, setUserMap] = useState<Record<number, string>>({})

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, thinking])

  const checkKbStatus = useCallback(async () => {
    try {
      const r = await fetch('/files')
      if (!r.ok) { setKbStatus('error'); setKbText('无法获取知识库状态'); return }
      const files = await r.json()
      if (files.length) {
        setKbStatus('ready')
        setKbText(`知识库就绪（${files.length} 个文件）`)
      } else {
        setKbStatus('empty')
        setKbText('知识库为空，请先上传资料')
      }
    } catch {
      setKbStatus('error')
      setKbText('无法获取知识库状态')
    }
  }, [])

  const loadConvList = useCallback(async () => {
    try {
      const res = await fetch('/conversations')
      if (res.ok) {
        const data = await res.json()
        setConvList(data.items ?? [])
      }
    } catch { /* ignore */ }
  }, [])

  useEffect(() => {
    loadConvList()
    checkKbStatus()
    // 加载用户列表建立 id→username 映射（admin 专用）
    fetch('/users')
      .then(r => r.ok ? r.json() : Promise.reject())
      .then((data: { users: { id: number; username: string }[] }) => {
        const map: Record<number, string> = {}
        for (const u of data.users ?? []) map[u.id] = u.username
        setUserMap(map)
      })
      .catch(() => { /* 非 admin 时 403，静默忽略 */ })
  }, [loadConvList, checkKbStatus])

  const autoResize = () => {
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = Math.min(ta.scrollHeight, 180) + 'px'
  }

  const loadConversation = async (id: string) => {
    if (streaming) { showToast('请等待当前回复完成', 'error'); return }
    try {
      const res = await fetch(`/conversations/${id}`)
      if (!res.ok) throw new Error()
      const data = await res.json()
      const msgs: MsgItem[] = (data.messages ?? []).map((m: { role: string; content: string }) => ({
        id: genId(),
        role: m.role as 'user' | 'assistant',
        content: m.content,
        time: '',
      }))
      setMessages(msgs)
      setConvId(id)
      setConvUserId(data.user_id ?? undefined)
      setConvTitle(data.title || '对话')
      const msgCount = msgs.length
      const isOther = data.user_id !== undefined && myUserId !== undefined && data.user_id !== myUserId
      setConvSub(
        isOther
          ? `查看用户 #${data.user_id} 的对话 · 共 ${msgCount} 条消息${data.has_summary ? ' · 含历史摘要' : ''}`
          : `共 ${msgCount} 条消息${data.has_summary ? ' · 含历史摘要' : ''}`
      )
      setFeedbackOpen(false)
      setFeedbackExpanded(false)
      setRating(0)
      setFeedbackComment('')
    } catch {
      showToast('加载对话失败', 'error')
    }
  }

  const newConversation = () => {
    if (streaming) { showToast('请等待当前回复完成', 'error'); return }
    abortRef.current?.()
    setMessages([])
    setConvId(undefined)
    setConvUserId(undefined)
    setConvTitle('Agent 对话')
    setConvSub('基于知识库回答业务问题')
    setFeedbackOpen(false)
    setFeedbackExpanded(false)
    setRating(0)
    setFeedbackComment('')
    showToast('已开始新对话')
  }

  const deleteConv = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation()
    if (!confirm('确认删除此对话？')) return
    try {
      await fetch(`/conversations/${id}`, { method: 'DELETE' })
      if (id === convId) newConversation()
      setConvList(prev => prev.filter(c => c.id !== id))
      showToast('对话已删除')
    } catch {
      showToast('删除失败', 'error')
    }
  }

  const startRename = (id: string, title: string, e: React.MouseEvent) => {
    e.stopPropagation()
    setRenamingId(id)
    setRenameVal(title)
  }

  const commitRename = async (id: string) => {
    const title = renameVal.trim()
    if (!title) { setRenamingId(null); return }
    try {
      await fetch(`/conversations/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title }),
      })
      setConvList(prev => prev.map(c => c.id === id ? { ...c, title } : c))
      if (id === convId) setConvTitle(title)
    } catch {
      showToast('重命名失败', 'error')
    }
    setRenamingId(null)
  }

  const copyMessage = (id: string, text: string) => {
    navigator.clipboard.writeText(text)
    setCopied(id)
    setTimeout(() => setCopied(null), 1500)
  }

  const sendMessage = useCallback(async () => {
    const text = input.trim()
    if (!text || streaming || readOnly) return
    setInput('')
    if (textareaRef.current) textareaRef.current.style.height = 'auto'

    const userMsg: MsgItem = { id: genId(), role: 'user', content: text, time: fmtTime() }
    setMessages(prev => [...prev, userMsg])
    setThinking(true)
    setStreaming(true)

    let aborted = false
    const controller = new AbortController()
    abortRef.current = () => { aborted = true; controller.abort() }

    const assistantId = genId()
    let started = false

    try {
      const resp = await fetch('/agent/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, conversation_id: convId, top_k: 5 }),
        signal: controller.signal,
      })

      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)

      const reader = resp.body!.getReader()
      const dec = new TextDecoder()
      let buffer = ''
      let fullText = ''

      while (true) {
        const { value, done } = await reader.read()
        if (done || aborted) break
        buffer += dec.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''

        for (const line of lines) {
          if (!line.startsWith('data:')) continue
          const raw = line.slice(5).trim()
          if (!raw) continue
          try {
            const evt = JSON.parse(raw)
            if (evt.type === 'token') {
              if (!started) {
                started = true
                setThinking(false)
                setMessages(prev => [...prev, {
                  id: assistantId, role: 'assistant', content: '', streaming: true, time: fmtTime(),
                }])
              }
              fullText += evt.text
              setMessages(prev => prev.map(m =>
                m.id === assistantId ? { ...m, content: fullText } : m
              ))
            } else if (evt.type === 'done') {
              fullText = evt.full_text ?? fullText
              if (!started) {
                setThinking(false)
                setMessages(prev => [...prev, {
                  id: assistantId, role: 'assistant', content: fullText, time: fmtTime(),
                }])
              } else {
                setMessages(prev => prev.map(m =>
                  m.id === assistantId ? { ...m, content: fullText, streaming: false } : m
                ))
              }
              setFeedbackOpen(true)
              setFeedbackExpanded(false)
            } else if (evt.type === 'error') {
              setThinking(false)
              showToast(evt.message, 'error')
              if (!started) {
                setMessages(prev => [...prev, {
                  id: assistantId, role: 'assistant', content: `❌ ${evt.message}`, time: fmtTime(),
                }])
              } else {
                setMessages(prev => prev.map(m =>
                  m.id === assistantId ? { ...m, content: `❌ ${evt.message}`, streaming: false } : m
                ))
              }
            } else if (evt.type === 'conversation_saved') {
              const newId = evt.conversation_id
              setConvId(newId)
              if (evt.title) setConvTitle(evt.title)
              loadConvList()
            }
          } catch { /* ignore parse errors */ }
        }
      }
    } catch (e: unknown) {
      setThinking(false)
      if (!aborted) {
        const msg = e instanceof Error ? e.message : '连接失败'
        showToast(msg, 'error')
        if (!started) {
          setMessages(prev => [...prev, {
            id: assistantId, role: 'assistant', content: `❌ ${msg}`, time: fmtTime(),
          }])
        } else {
          setMessages(prev => prev.map(m =>
            m.id === assistantId ? { ...m, content: `❌ ${msg}`, streaming: false } : m
          ))
        }
      }
    } finally {
      setThinking(false)
      setStreaming(false)
      abortRef.current = null
      setMessages(prev => prev.map(m => m.id === assistantId ? { ...m, streaming: false } : m))
    }
  }, [input, streaming, convId, showToast, loadConvList])

  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage() }
  }

  const clearChat = () => {
    abortRef.current?.()
    setMessages([])
    setConvId(undefined)
    setConvUserId(undefined)
    setConvTitle('Agent 对话')
    setConvSub('基于知识库回答业务问题')
    setFeedbackOpen(false)
    setFeedbackExpanded(false)
    setRating(0)
    setFeedbackComment('')
    showToast('对话已清空')
  }

  const submitFeedback = async () => {
    if (!messages.length) return
    if (rating < 1) { showToast('请先选择评分星级', 'error'); return }
    const history = messages.map(m => ({ role: m.role, content: m.content }))
    try {
      const r = await fetch('/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rating, comment: feedbackComment, history, conversation_id: convId || '' }),
      })
      if (!r.ok) { showToast('提交失败', 'error'); return }
    } catch { showToast('提交失败', 'error'); return }
    showToast('感谢您的反馈！')
    setFeedbackExpanded(false)
    setFeedbackOpen(false)
  }

  // Group conversations by date
  const groupConvs = () => {
    const today = new Date(); today.setHours(0, 0, 0, 0)
    const yesterday = new Date(today); yesterday.setDate(today.getDate() - 1)
    const lastWeek = new Date(today); lastWeek.setDate(today.getDate() - 7)

    const groups: Record<string, ConvMeta[]> = { today: [], yesterday: [], week: [], earlier: [] }
    for (const c of convList) {
      const d = new Date(c.updated_at)
      if (d >= today) groups.today.push(c)
      else if (d >= yesterday) groups.yesterday.push(c)
      else if (d >= lastWeek) groups.week.push(c)
      else groups.earlier.push(c)
    }
    return groups
  }

  const kbDotColor = {
    checking: 'bg-gray-300',
    ready: 'bg-green-400',
    empty: 'bg-amber-400',
    error: 'bg-red-400',
  }[kbStatus]

  const groups = groupConvs()
  const groupLabels: [string, string][] = [
    ['today', '今天'], ['yesterday', '昨天'], ['week', '最近 7 天'], ['earlier', '更早'],
  ]

  const ConvItem = ({ conv }: { conv: ConvMeta }) => {
    const isOtherUser = conv.user_id !== undefined && myUserId !== undefined && conv.user_id !== myUserId
    const ownerName = conv.user_id !== undefined ? (userMap[conv.user_id] ?? `#${conv.user_id}`) : null

    return (
      <div
        onClick={() => loadConversation(conv.id)}
        className={`group mx-2 mb-0.5 px-2.5 py-2 rounded-lg cursor-pointer transition-colors relative
          ${conv.id === convId ? 'bg-[#eff6ff]' : 'hover:bg-[#f0f0f0]'}`}
      >
        {renamingId === conv.id ? (
          <div className="flex items-center gap-1" onClick={e => e.stopPropagation()}>
            <input
              autoFocus
              value={renameVal}
              onChange={e => setRenameVal(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') commitRename(conv.id); if (e.key === 'Escape') setRenamingId(null) }}
              className="flex-1 text-xs px-1.5 py-0.5 border border-[#3b82f6] rounded focus:outline-none min-w-0"
            />
            <button onClick={() => commitRename(conv.id)} className="text-[#3b82f6] hover:text-[#2563eb]">
              <Check size={12} />
            </button>
            <button onClick={() => setRenamingId(null)} className="text-[#9ca3af] hover:text-[#6b6b6b]">
              <X size={12} />
            </button>
          </div>
        ) : (
          <>
            <div className="flex items-start gap-1.5">
              <MessageSquare size={12} className={`mt-0.5 shrink-0 ${conv.id === convId ? 'text-[#3b82f6]' : 'text-[#9ca3af]'}`} />
              <span className={`text-xs leading-snug line-clamp-2 flex-1 min-w-0 ${conv.id === convId ? 'text-[#1d4ed8] font-medium' : 'text-[#374151]'}`}>
                {conv.title || '新对话'}
              </span>
            </div>
            <div className="flex items-center gap-1.5 mt-0.5 ml-[18px]">
              <p className="text-[10px] text-[#9ca3af]">{fmtDate(conv.updated_at)}</p>
              {isOtherUser && ownerName && (
                <span className="flex items-center gap-0.5 text-[10px] text-[#7c3aed] bg-[#f5f3ff] px-1 py-0 rounded">
                  <UserCircle size={9} />
                  {ownerName}
                </span>
              )}
            </div>
            <div className="absolute right-1.5 top-1.5 hidden group-hover:flex items-center gap-0.5">
              <button
                onClick={e => startRename(conv.id, conv.title, e)}
                className="p-0.5 rounded text-[#9ca3af] hover:text-[#3b82f6] hover:bg-white"
              >
                <Pencil size={11} />
              </button>
              <button
                onClick={e => deleteConv(conv.id, e)}
                className="p-0.5 rounded text-[#9ca3af] hover:text-red-500 hover:bg-white"
              >
                <X size={11} />
              </button>
            </div>
          </>
        )}
      </div>
    )
  }

  return (
    <div className="flex h-full overflow-hidden">
      {/* ── Conversation History Sidebar ── */}
      <aside className="w-56 flex-shrink-0 border-r border-[#e5e5e5] bg-[#fafafa] flex flex-col overflow-hidden">
        <div className="px-3 py-3 border-b border-[#e5e5e5]">
          <button
            onClick={newConversation}
            className="w-full flex items-center justify-center gap-1.5 py-2 px-3 bg-[#3b82f6] hover:bg-[#2563eb] text-white text-xs font-medium rounded-lg transition-colors"
          >
            <Plus size={13} /> 新对话
          </button>
        </div>
        <div className="flex-1 overflow-y-auto py-2 scrollbar-thin">
          {convList.length === 0 && (
            <p className="text-center text-xs text-[#9ca3af] mt-6 px-3 leading-relaxed">
              暂无对话历史<br />点击「新对话」开始
            </p>
          )}
          {groupLabels.map(([key, label]) => {
            const items = groups[key]
            if (!items.length) return null
            return (
              <div key={key}>
                <p className="text-[10px] font-semibold text-[#9ca3af] px-4 pt-2 pb-1 uppercase tracking-wide">{label}</p>
                {items.map(conv => <ConvItem key={conv.id} conv={conv} />)}
              </div>
            )
          })}
        </div>
      </aside>

      {/* ── Chat Area ── */}
      <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
        {/* Topbar */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-[#e5e5e5] bg-white">
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-lg font-semibold text-[#171717]">{convTitle}</h1>
              {readOnly && convUserId !== undefined && (
                <span className="flex items-center gap-1 text-xs text-[#7c3aed] bg-[#f5f3ff] border border-[#ede9fe] px-2 py-0.5 rounded-full">
                  <UserCircle size={13} />
                  {userMap[convUserId] ?? `用户 #${convUserId}`} 的对话
                </span>
              )}
            </div>
            <p className="text-xs text-[#9ca3af] mt-0.5">{convSub}</p>
          </div>
          <button onClick={clearChat} className="flex items-center gap-1.5 text-sm text-[#6b6b6b] hover:text-red-500 transition-colors">
            <Trash2 size={15} /> 清空对话
          </button>
        </div>

        {/* Info banner */}
        {showInfo && (
          <div className="mx-6 mt-4 flex items-center gap-3 bg-blue-50 border border-blue-100 rounded-xl px-4 py-3">
            <AlertCircle size={15} className="text-blue-400 shrink-0" />
            <p className="text-xs text-blue-600 flex-1">当前使用全局模型配置，可在「系统设置」中调整。管理员可查看完整会话历史。</p>
            <button onClick={() => setShowInfo(false)} className="text-blue-300 hover:text-blue-500 text-lg leading-none">×</button>
          </div>
        )}

        {/* Messages */}
        <div className="flex-1 overflow-y-auto scrollbar-thin px-6 py-5 flex flex-col gap-5">
          {messages.length === 0 && !thinking && (
            <div className="flex-1 flex flex-col items-center justify-center text-center py-20">
              <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-[#3b82f6] to-[#6366f1] flex items-center justify-center mb-4 shadow-lg">
                <Bot size={28} className="text-white" />
              </div>
              <p className="text-xl font-semibold text-[#171717]">有什么可以帮你的？</p>
              <p className="text-sm text-[#9ca3af] mt-2">基于知识库的 AI 销售助手，支持产品咨询、话术生成等</p>
            </div>
          )}

          {messages.map(msg => (
            <div key={msg.id} className={`flex gap-3 ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}>
              <div className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 mt-0.5
                ${msg.role === 'user' ? 'bg-[#f3f4f6]' : 'bg-gradient-to-br from-[#3b82f6] to-[#6366f1]'}`}>
                {msg.role === 'user'
                  ? <User size={15} className="text-[#6b6b6b]" />
                  : <Bot size={15} className="text-white" />}
              </div>
              <div className="flex flex-col gap-1 max-w-[75%]">
                <div className={`rounded-2xl px-4 py-3 text-sm ${msg.role === 'user'
                  ? 'bg-[#f3f4f6] text-[#171717]'
                  : 'bg-white border border-[#e5e5e5] text-[#171717]'}`}>
                  {msg.role === 'assistant'
                    ? <div className="prose-chat">
                        <ReactMarkdown>{msg.content || (msg.streaming ? '…' : '')}</ReactMarkdown>
                        {msg.streaming && <span className="inline-block w-1.5 h-4 bg-[#3b82f6] ml-0.5 animate-pulse rounded-sm" />}
                      </div>
                    : msg.content}
                </div>
                <div className={`flex items-center gap-2 px-1 ${msg.role === 'user' ? 'justify-end' : ''}`}>
                  {msg.time && <span className="text-[10px] text-[#9ca3af]">{msg.time}</span>}
                  {msg.role === 'assistant' && !msg.streaming && msg.content && (
                    <button
                      onClick={() => copyMessage(msg.id, msg.content)}
                      className="flex items-center gap-0.5 text-[10px] text-[#9ca3af] hover:text-[#6b6b6b] transition-colors"
                    >
                      <Copy size={10} />
                      {copied === msg.id ? '已复制' : '复制'}
                    </button>
                  )}
                </div>
              </div>
            </div>
          ))}

          {/* Thinking animation */}
          {thinking && (
            <div className="flex gap-3">
              <div className="w-8 h-8 rounded-full bg-gradient-to-br from-[#3b82f6] to-[#6366f1] flex items-center justify-center shrink-0 mt-0.5">
                <Bot size={15} className="text-white" />
              </div>
              <div className="bg-white border border-[#e5e5e5] rounded-2xl px-4 py-3">
                <div className="flex items-center gap-2 text-xs text-[#9ca3af]">
                  <span className="flex gap-1">
                    {[0, 1, 2].map(i => (
                      <span
                        key={i}
                        className="w-1.5 h-1.5 bg-[#9ca3af] rounded-full animate-bounce"
                        style={{ animationDelay: `${i * 0.15}s` }}
                      />
                    ))}
                  </span>
                  Agent 正在思考…
                </div>
              </div>
            </div>
          )}

          <div ref={bottomRef} />
        </div>

        {/* Feedback */}
        {feedbackOpen && messages.length > 0 && (
          <div className="mx-6 mb-2 border border-[#e5e5e5] rounded-xl bg-white overflow-hidden">
            <button
              onClick={() => setFeedbackExpanded(v => !v)}
              className="w-full flex items-center gap-2 px-4 py-2.5 text-xs text-[#6b6b6b] hover:bg-[#f7f7f8] transition-colors"
            >
              <ChevronDown size={13} className={`transition-transform ${feedbackExpanded ? 'rotate-180' : ''}`} />
              对本轮回答的反馈（选填）
            </button>
            {feedbackExpanded && (
              <div className="px-4 pb-4 border-t border-[#f0f0f0]">
                <div className="flex items-center gap-1 my-3">
                  {[1, 2, 3, 4, 5].map(s => (
                    <button key={s} onClick={() => setRating(s)}>
                      <Star size={18} className={s <= rating ? 'text-amber-400 fill-amber-400' : 'text-[#d1d5db]'} />
                    </button>
                  ))}
                </div>
                <div className="flex gap-2">
                  <input value={feedbackComment} onChange={e => setFeedbackComment(e.target.value)}
                    placeholder="补充说明（可选）：指出错误、提供正确答案"
                    className="flex-1 px-3 py-1.5 border border-[#e5e5e5] rounded-lg text-xs focus:outline-none focus:border-[#3b82f6]" />
                  <button onClick={submitFeedback}
                    className="px-3 py-1.5 bg-[#3b82f6] hover:bg-[#2563eb] text-white rounded-lg text-xs font-medium transition-colors">
                    提交
                  </button>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Input */}
        <div className="px-6 pb-5">
          {readOnly ? (
            /* 只读提示 — 查看他人会话时 */
            <div className="flex items-center gap-2.5 px-4 py-3 bg-amber-50 border border-amber-200 rounded-2xl">
              <AlertCircle size={15} className="text-amber-500 shrink-0" />
              <p className="text-xs text-amber-700 flex-1">
                正在查看其他用户的对话历史，只读模式 · 无法发送消息
              </p>
              <button
                onClick={newConversation}
                className="flex items-center gap-1 text-xs text-amber-700 font-medium hover:text-amber-900 transition-colors whitespace-nowrap"
              >
                <Plus size={12} /> 新建自己的对话
              </button>
            </div>
          ) : (
            <>
              {/* KB status */}
              <div className="flex items-center gap-1.5 mb-2">
                <Circle size={6} className={`fill-current ${kbDotColor.replace('bg-', 'text-')}`} />
                <span className="text-[11px] text-[#9ca3af]">{kbText}</span>
              </div>
              <div className="border border-[#e5e5e5] rounded-2xl bg-white shadow-sm overflow-hidden focus-within:border-[#3b82f6] transition-colors">
                <textarea
                  ref={textareaRef}
                  value={input}
                  onChange={e => { setInput(e.target.value); autoResize() }}
                  onKeyDown={handleKey}
                  placeholder="输入消息，Enter 发送，Shift+Enter 换行…"
                  rows={1}
                  className="w-full px-4 pt-3 pb-2 text-sm text-[#171717] resize-none focus:outline-none placeholder:text-[#9ca3af] bg-transparent"
                  style={{ minHeight: '44px', maxHeight: '180px' }}
                />
                <div className="flex items-center justify-end px-3 pb-3">
                  <button
                    onClick={sendMessage}
                    disabled={!input.trim() || streaming}
                    className="w-8 h-8 flex items-center justify-center bg-[#3b82f6] hover:bg-[#2563eb] disabled:opacity-30 text-white rounded-lg transition-all"
                  >
                    <Send size={14} />
                  </button>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

export default ChatPage
