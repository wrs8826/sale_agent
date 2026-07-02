import React, { useState, useRef, useEffect, useCallback } from 'react'
import {
  Send, Square, Trash2, Bot, User, AlertCircle, Star,
  Plus, MessageSquare, Pencil, X, Check, Copy, ChevronDown,
  ChevronLeft, ChevronRight,
  Circle, UserCircle, Paperclip, ClipboardList, Download,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useApp } from '../context/AppContext'
import type { ChatMessage } from '../types'

const genId = () => Math.random().toString(36).slice(2)

const _TIME_FMT: Intl.DateTimeFormatOptions = {
  year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', hour12: false,
}

const fmtTime = () => new Date().toLocaleString('zh-CN', _TIME_FMT)

// 把后端存储的 ISO 时间（UTC）格式化为本地 年/月/日 时:分；解析失败则返回空串
const fmtTimeFromIso = (iso?: string) => {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  return d.toLocaleString('zh-CN', _TIME_FMT)
}

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

// 每个并行对话标签的独立状态
interface TabState {
  tabId: string
  convId?: string
  convUserId?: number
  title: string
  sub: string
  input: string
  messages: MsgItem[]
  streaming: boolean
  thinking: boolean
  feedbackOpen: boolean
  feedbackExpanded: boolean
  feedbackComment: string
  rating: number
  attachment?: { filename: string }   // 回形针上传后待随下条消息发送的文件
  uploading?: boolean
}

const blankTab = (): TabState => ({
  tabId: genId(),
  convId: undefined,
  convUserId: undefined,
  title: 'Agent 对话',
  sub: '基于知识库回答业务问题',
  input: '',
  messages: [],
  streaming: false,
  thinking: false,
  feedbackOpen: false,
  feedbackExpanded: false,
  feedbackComment: '',
  rating: 0,
  attachment: undefined,
  uploading: false,
})

const ChatPage: React.FC = () => {
  const { showToast, auth } = useApp()
  const myUserId = auth?.user_id

  // 多对话并行：每个 tab 维护独立的消息流/状态，互不阻塞
  const [tabs, setTabs] = useState<Record<string, TabState>>(() => {
    const t = blankTab()
    return { [t.tabId]: t }
  })
  const [tabOrder, setTabOrder] = useState<string[]>(() => Object.keys(tabs))
  const [activeTabId, setActiveTabId] = useState<string>(() => tabOrder[0])

  const [showInfo, setShowInfo] = useState(true)
  const [copied, setCopied] = useState<string | null>(null)

  // 会话侧栏抽拉：收起/展开状态记忆到 localStorage
  const [convSidebarCollapsed, setConvSidebarCollapsed] = useState<boolean>(
    () => localStorage.getItem('convSidebarCollapsed') === '1'
  )
  useEffect(() => {
    localStorage.setItem('convSidebarCollapsed', convSidebarCollapsed ? '1' : '0')
  }, [convSidebarCollapsed])

  // KB status
  const [kbStatus, setKbStatus] = useState<'checking' | 'ready' | 'empty' | 'error'>('checking')
  const [kbText, setKbText] = useState('检查知识库状态…')

  const bottomRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  // 每个 tab 一个中断函数，后台流不受标签切换影响
  const abortRefs = useRef<Record<string, () => void>>({})

  // Conversation sidebar
  const [convList, setConvList] = useState<ConvMeta[]>([])
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameVal, setRenameVal] = useState('')
  const [userMap, setUserMap] = useState<Record<number, string>>({})

  const activeTab = tabs[activeTabId]
  const readOnly = !!activeTab?.convUserId && myUserId !== undefined && activeTab.convUserId !== myUserId

  const updateTab = useCallback((tabId: string, updater: (t: TabState) => TabState) => {
    setTabs(prev => {
      const cur = prev[tabId]
      if (!cur) return prev
      return { ...prev, [tabId]: updater(cur) }
    })
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [activeTab?.messages, activeTab?.thinking])

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

  // 打开一个会话：若已在某个标签中打开则直接切换，否则新建标签加载
  const loadConversation = async (id: string) => {
    const existing = Object.values(tabs).find(t => t.convId === id)
    if (existing) { setActiveTabId(existing.tabId); return }

    try {
      const res = await fetch(`/conversations/${id}`)
      if (!res.ok) throw new Error()
      const data = await res.json()
      const msgs: MsgItem[] = (data.messages ?? []).map((m: { role: string; content: string; ts?: string; name?: string; args?: Record<string, unknown> }) => ({
        id: genId(),
        role: m.role as 'user' | 'assistant' | 'tool',
        content: m.content,
        name: m.name,
        args: m.args,
        time: fmtTimeFromIso(m.ts),
      }))
      const msgCount = msgs.length
      const isOther = data.user_id !== undefined && myUserId !== undefined && data.user_id !== myUserId
      const tab: TabState = {
        ...blankTab(),
        convId: id,
        convUserId: data.user_id ?? undefined,
        title: data.title || '对话',
        sub: isOther
          ? `查看用户 #${data.user_id} 的对话 · 共 ${msgCount} 条消息${data.has_summary ? ' · 含历史摘要' : ''}`
          : `共 ${msgCount} 条消息${data.has_summary ? ' · 含历史摘要' : ''}`,
        messages: msgs,
      }
      setTabs(prev => ({ ...prev, [tab.tabId]: tab }))
      setTabOrder(prev => [...prev, tab.tabId])
      setActiveTabId(tab.tabId)
    } catch {
      showToast('加载对话失败', 'error')
    }
  }

  const newConversation = () => {
    const tab = blankTab()
    setTabs(prev => ({ ...prev, [tab.tabId]: tab }))
    setTabOrder(prev => [...prev, tab.tabId])
    setActiveTabId(tab.tabId)
    showToast('已开始新对话')
  }

  // 关闭一个并行对话标签（中断其流式请求）
  const closeTab = (tabId: string, e?: React.MouseEvent) => {
    e?.stopPropagation()
    abortRefs.current[tabId]?.()
    delete abortRefs.current[tabId]
    setTabs(prev => {
      const next = { ...prev }
      delete next[tabId]
      return next
    })
    setTabOrder(prev => {
      const next = prev.filter(id => id !== tabId)
      if (activeTabId === tabId) {
        setActiveTabId(next[next.length - 1] ?? '')
        if (next.length === 0) {
          const fresh = blankTab()
          setTabs(p => ({ ...p, [fresh.tabId]: fresh }))
          return [fresh.tabId]
        }
      }
      return next
    })
  }

  const deleteConv = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation()
    if (!confirm('确认删除此对话？')) return
    try {
      await fetch(`/conversations/${id}`, { method: 'DELETE' })
      const openTab = Object.values(tabs).find(t => t.convId === id)
      if (openTab) closeTab(openTab.tabId)
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
      setTabs(prev => {
        const next = { ...prev }
        for (const t of Object.values(next)) {
          if (t.convId === id) next[t.tabId] = { ...t, title }
        }
        return next
      })
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

  // 回形针上传：上传到知识库（/upload），随下条消息让助手用 read_document 读取
  const ATTACH_EXTS = ['.txt', '.md', '.rst', '.html', '.pdf', '.docx']
  const handleAttach = useCallback(async (file: File) => {
    const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase()
    if (!ATTACH_EXTS.includes(ext)) {
      showToast('仅支持 .txt .md .rst .html .pdf .docx', 'error')
      return
    }
    const tabId = activeTabId
    updateTab(tabId, t => ({ ...t, uploading: true }))
    try {
      const fd = new FormData()
      fd.append('file', file)
      const r = await fetch('/upload', { method: 'POST', body: fd })
      const data = await r.json().catch(() => ({}))
      if (!r.ok || !data.ok) {
        showToast(data.error || '上传失败', 'error')
        return
      }
      updateTab(tabId, t => ({ ...t, attachment: { filename: data.filename } }))
      checkKbStatus()
      showToast(`已上传：${data.filename}`, 'success')
    } catch (e) {
      showToast(`上传出错：${e instanceof Error ? e.message : String(e)}`, 'error')
    } finally {
      updateTab(tabId, t => ({ ...t, uploading: false }))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTabId, updateTab, showToast])

  // 发送消息：绑定到具体 tabId，后台流式更新该 tab，与当前激活标签无关
  const sendMessage = useCallback(async (tabId: string) => {
    const tab = tabs[tabId]
    if (!tab) return
    const typed = tab.input.trim()
    const attachment = tab.attachment
    const isReadOnly = !!tab.convUserId && myUserId !== undefined && tab.convUserId !== myUserId
    if ((!typed && !attachment) || tab.streaming || isReadOnly) return

    // 组装实际发送的消息：带附件时附上文件名，引导助手用 read_document 读取
    let text: string
    if (attachment) {
      const fn = attachment.filename
      text = typed
        ? `${typed}\n\n📎 附件文件：${fn}（如需文件内容，请用 read_document 读取）`
        : `请阅读并总结我上传的文件：${fn}`
    } else {
      text = typed
    }

    const sendTime = fmtTime()
    const userMsg: MsgItem = { id: genId(), role: 'user', content: text, time: sendTime }
    updateTab(tabId, t => ({
      ...t,
      input: '',
      attachment: undefined,   // 附件已并入本条消息
      messages: [...t.messages, userMsg],
      thinking: true,
      streaming: true,
    }))
    if (tabId === activeTabId && textareaRef.current) textareaRef.current.style.height = 'auto'

    let aborted = false
    const controller = new AbortController()
    abortRefs.current[tabId] = () => { aborted = true; controller.abort() }

    const assistantId = genId()
    let started = false

    try {
      const resp = await fetch('/agent/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, conversation_id: tab.convId, top_k: 5 }),
        signal: controller.signal,
      })

      if (resp.status === 409) {
        // 该会话已有生成/压缩在进行（多为另一设备/标签页或管理端）；本轮视为未发送，回滚用户气泡
        const errData = await resp.json().catch(() => ({}))
        showToast(errData.error || '该对话正在生成中，请先中断当前回答', 'error')
        updateTab(tabId, t => ({ ...t, thinking: false, messages: t.messages.filter(m => m.id !== userMsg.id) }))
        return
      }
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
            if (evt.type === 'plan_start') {
              // 先列执行方案：提前建占位助手消息承载方案
              if (!started) {
                started = true
                updateTab(tabId, t => ({
                  ...t,
                  thinking: false,
                  messages: [...t.messages, { id: assistantId, role: 'assistant', content: '', plan: '', streaming: true, time: sendTime }],
                }))
              }
            } else if (evt.type === 'plan_token') {
              const txt = evt.text || ''
              updateTab(tabId, t => ({
                ...t,
                messages: t.messages.map(m => m.id === assistantId ? { ...m, plan: (m.plan || '') + txt } : m),
              }))
            } else if (evt.type === 'plan_end') {
              const finalPlan = (evt.plan || '').trim()
              updateTab(tabId, t => ({
                ...t,
                messages: t.messages.map(m => m.id === assistantId ? { ...m, plan: finalPlan || undefined } : m),
              }))
            } else if (evt.type === 'tool_start' || evt.type === 'tool_end') {
              // 工具执行实时清单：每个工具事件刷新一次，[ ]/[✅]/[❌]
              if (!started) {
                started = true
                updateTab(tabId, t => ({
                  ...t,
                  thinking: false,
                  messages: [...t.messages, { id: assistantId, role: 'assistant', content: '', streaming: true, time: sendTime }],
                }))
              }
              updateTab(tabId, t => ({
                ...t,
                thinking: false,
                messages: t.messages.map(m => {
                  if (m.id !== assistantId) return m
                  const tools = [...(m.tools || [])]
                  if (evt.type === 'tool_start') {
                    tools.push({ name: evt.name, status: 'running' })
                  } else {
                    const status: 'ok' | 'error' = evt.error ? 'error' : 'ok'
                    let idx = -1
                    for (let i = tools.length - 1; i >= 0; i--) {
                      if (tools[i].name === evt.name && tools[i].status === 'running') { idx = i; break }
                    }
                    if (idx >= 0) tools[idx] = { ...tools[idx], status }
                    else tools.push({ name: evt.name, status })
                  }
                  return { ...m, tools }
                }),
              }))
            } else if (evt.type === 'download') {
              // 确定性下载：按真实工具结果记录下载信息，渲染下载按钮（不依赖模型转述链接）
              if (!started) {
                started = true
                updateTab(tabId, t => ({
                  ...t,
                  thinking: false,
                  messages: [...t.messages, { id: assistantId, role: 'assistant', content: '', streaming: true, time: sendTime }],
                }))
              }
              updateTab(tabId, t => ({
                ...t,
                messages: t.messages.map(m => m.id === assistantId ? { ...m, download: { url: evt.url, filename: evt.filename } } : m),
              }))
            } else if (evt.type === 'token') {
              if (!started) {
                started = true
                updateTab(tabId, t => ({
                  ...t,
                  thinking: false,
                  messages: [...t.messages, { id: assistantId, role: 'assistant', content: '', streaming: true, time: sendTime }],
                }))
              }
              fullText += evt.text
              updateTab(tabId, t => ({
                ...t,
                messages: t.messages.map(m => m.id === assistantId ? { ...m, content: fullText } : m),
              }))
            } else if (evt.type === 'done') {
              fullText = evt.full_text ?? fullText
              if (!started) {
                updateTab(tabId, t => ({
                  ...t,
                  thinking: false,
                  messages: [...t.messages, { id: assistantId, role: 'assistant', content: fullText, time: sendTime }],
                  feedbackOpen: true,
                  feedbackExpanded: false,
                }))
              } else {
                updateTab(tabId, t => ({
                  ...t,
                  messages: t.messages.map(m => m.id === assistantId ? { ...m, content: fullText, streaming: false } : m),
                  feedbackOpen: true,
                  feedbackExpanded: false,
                }))
              }
            } else if (evt.type === 'error') {
              showToast(evt.message, 'error')
              if (!started) {
                updateTab(tabId, t => ({
                  ...t,
                  thinking: false,
                  messages: [...t.messages, { id: assistantId, role: 'assistant', content: `❌ ${evt.message}`, time: sendTime }],
                }))
              } else {
                updateTab(tabId, t => ({
                  ...t,
                  thinking: false,
                  messages: t.messages.map(m => m.id === assistantId ? { ...m, content: `❌ ${evt.message}`, streaming: false } : m),
                }))
              }
            } else if (evt.type === 'conversation_saved') {
              const newId = evt.conversation_id
              updateTab(tabId, t => ({ ...t, convId: newId, title: evt.title || t.title }))
              loadConvList()
            }
          } catch { /* ignore parse errors */ }
        }
      }
    } catch (e: unknown) {
      if (!aborted) {
        const msg = e instanceof Error ? e.message : '连接失败'
        showToast(msg, 'error')
        if (!started) {
          updateTab(tabId, t => ({
            ...t,
            thinking: false,
            messages: [...t.messages, { id: assistantId, role: 'assistant', content: `❌ ${msg}`, time: sendTime }],
          }))
        } else {
          updateTab(tabId, t => ({
            ...t,
            thinking: false,
            messages: t.messages.map(m => m.id === assistantId ? { ...m, content: `❌ ${msg}`, streaming: false } : m),
          }))
        }
      }
    } finally {
      delete abortRefs.current[tabId]
      updateTab(tabId, t => ({
        ...t,
        thinking: false,
        streaming: false,
        messages: t.messages.map(m => m.id === assistantId
          ? { ...m, streaming: false, content: aborted && m.content ? `${m.content}\n\n*[已停止]*` : m.content }
          : m),
      }))
    }
  }, [tabs, activeTabId, showToast, loadConvList, updateTab, myUserId])

  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (activeTab.streaming) return
      sendMessage(activeTabId)
    }
  }

  const clearChat = () => {
    abortRefs.current[activeTabId]?.()
    delete abortRefs.current[activeTabId]
    const fresh = blankTab()
    fresh.tabId = activeTabId // 保留标签位置
    setTabs(prev => ({ ...prev, [activeTabId]: fresh }))
    showToast('对话已清空')
  }

  const submitFeedback = async () => {
    const tab = tabs[activeTabId]
    if (!tab || !tab.messages.length) return
    if (tab.rating < 1) { showToast('请先选择评分星级', 'error'); return }
    const history = tab.messages.map(m => ({ role: m.role, content: m.content }))
    try {
      const r = await fetch('/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rating: tab.rating, comment: tab.feedbackComment, history, conversation_id: tab.convId || '' }),
      })
      if (!r.ok) { showToast('提交失败', 'error'); return }
    } catch { showToast('提交失败', 'error'); return }
    showToast('感谢您的反馈！')
    updateTab(activeTabId, t => ({ ...t, feedbackExpanded: false, feedbackOpen: false }))
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
    const isOpen = Object.values(tabs).some(t => t.convId === conv.id)

    return (
      <div
        onClick={() => loadConversation(conv.id)}
        className={`group mx-2 mb-0.5 px-2.5 py-2 rounded-lg cursor-pointer transition-colors relative
          ${isOpen ? 'bg-[#eff6ff]' : 'hover:bg-[#f0f0f0]'}`}
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
              <MessageSquare size={12} className={`mt-0.5 shrink-0 ${isOpen ? 'text-[#3b82f6]' : 'text-[#9ca3af]'}`} />
              <span className={`text-xs leading-snug line-clamp-2 flex-1 min-w-0 ${isOpen ? 'text-[#1d4ed8] font-medium' : 'text-[#374151]'}`}>
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

  if (!activeTab) return null

  return (
    <div className="flex h-full overflow-hidden">
      {/* ── Conversation History Sidebar（抽拉式：wrapper 不裁剪，拉手露在边框外） ── */}
      <div className="relative flex-shrink-0 flex">
        <aside
          className={`border-r border-[#e5e5e5] bg-[#fafafa] flex flex-col overflow-hidden transition-[width] duration-200 ${
            convSidebarCollapsed ? 'w-0 border-r-0' : 'w-56'
          }`}
        >
          <div className="px-3 py-3 border-b border-[#e5e5e5] w-56">
            <button
              onClick={newConversation}
              className="w-full flex items-center justify-center gap-1.5 py-2 px-3 bg-[#3b82f6] hover:bg-[#2563eb] text-white text-xs font-medium rounded-lg transition-colors"
            >
              <Plus size={13} /> 新对话
            </button>
          </div>
          <div className="flex-1 overflow-y-auto py-2 scrollbar-thin w-56">
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
        <button
          onClick={() => setConvSidebarCollapsed(v => !v)}
          title={convSidebarCollapsed ? '展开对话历史' : '收起对话历史'}
          aria-label={convSidebarCollapsed ? '展开对话历史' : '收起对话历史'}
          className="absolute top-1/2 -right-[13px] -translate-y-1/2 w-[22px] h-11 bg-white border border-[#e5e5e5] border-l-0 rounded-r-lg flex items-center justify-center text-[#9ca3af] hover:text-[#3b82f6] hover:bg-[#f0f5ff] transition-colors z-10"
        >
          {convSidebarCollapsed ? <ChevronRight size={13} /> : <ChevronLeft size={13} />}
        </button>
      </div>

      {/* ── Chat Area ── */}
      <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
        {/* 并行对话标签栏 */}
        <div className="flex items-center gap-1 px-3 pt-2 border-b border-[#e5e5e5] bg-white overflow-x-auto scrollbar-thin">
          {tabOrder.map(tid => {
            const t = tabs[tid]
            if (!t) return null
            return (
              <div
                key={tid}
                onClick={() => setActiveTabId(tid)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-t-lg text-xs cursor-pointer max-w-[160px] shrink-0 border-b-2 transition-colors
                  ${tid === activeTabId ? 'border-[#3b82f6] text-[#1d4ed8] bg-[#eff6ff] font-medium' : 'border-transparent text-[#6b6b6b] hover:bg-[#f5f5f5]'}`}
              >
                {t.streaming && <Circle size={6} className="fill-current text-[#3b82f6] animate-pulse shrink-0" />}
                <span className="truncate">{t.title}</span>
                {tabOrder.length > 1 && (
                  <button onClick={e => closeTab(tid, e)} className="text-[#9ca3af] hover:text-red-500 shrink-0">
                    <X size={11} />
                  </button>
                )}
              </div>
            )
          })}
          <button
            onClick={newConversation}
            title="新建并行对话"
            className="flex items-center justify-center w-6 h-6 mb-1 rounded text-[#9ca3af] hover:text-[#3b82f6] hover:bg-[#f0f5ff] shrink-0"
          >
            <Plus size={13} />
          </button>
        </div>

        {/* Topbar */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-[#e5e5e5] bg-white">
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-lg font-semibold text-[#171717]">{activeTab.title}</h1>
              {readOnly && activeTab.convUserId !== undefined && (
                <span className="flex items-center gap-1 text-xs text-[#7c3aed] bg-[#f5f3ff] border border-[#ede9fe] px-2 py-0.5 rounded-full">
                  <UserCircle size={13} />
                  {userMap[activeTab.convUserId] ?? `用户 #${activeTab.convUserId}`} 的对话
                </span>
              )}
            </div>
            <p className="text-xs text-[#9ca3af] mt-0.5">{activeTab.sub}</p>
          </div>
          <button onClick={clearChat} className="flex items-center gap-1.5 text-sm text-[#6b6b6b] hover:text-red-500 transition-colors">
            <Trash2 size={15} /> 清空对话
          </button>
        </div>

        {/* Info banner */}
        {showInfo && (
          <div className="mx-6 mt-4 flex items-center gap-3 bg-blue-50 border border-blue-100 rounded-xl px-4 py-3">
            <AlertCircle size={15} className="text-blue-400 shrink-0" />
            <p className="text-xs text-blue-600 flex-1">当前使用全局模型配置，可在「系统设置」中调整。管理员可查看完整会话历史，并可同时打开多个对话并行对话。</p>
            <button onClick={() => setShowInfo(false)} className="text-blue-300 hover:text-blue-500 text-lg leading-none">×</button>
          </div>
        )}

        {/* Messages */}
        <div className="flex-1 overflow-y-auto scrollbar-thin px-6 py-5 flex flex-col gap-5">
          {activeTab.messages.length === 0 && !activeTab.thinking && (
            <div className="flex-1 flex flex-col items-center justify-center text-center py-20">
              <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-[#3b82f6] to-[#6366f1] flex items-center justify-center mb-4 shadow-lg">
                <Bot size={28} className="text-white" />
              </div>
              <p className="text-xl font-semibold text-[#171717]">有什么可以帮你的？</p>
              <p className="text-sm text-[#9ca3af] mt-2">基于知识库的 AI 销售助手，支持产品咨询、话术生成等</p>
            </div>
          )}

          {(() => {
            // 把连续的 role=tool 消息合并成一个新版执行清单（与 live 一致），不再用旧版工具卡片
            type RItem =
              | { kind: 'tools'; id: string; tools: { name: string; status: 'ok' | 'error' }[]; downloads: { url: string; filename: string }[] }
              | { kind: 'msg'; msg: MsgItem }
            const items: RItem[] = []
            for (let i = 0; i < activeTab.messages.length; i++) {
              const mm = activeTab.messages[i]
              if (mm.role === 'tool') {
                const tools: { name: string; status: 'ok' | 'error' }[] = []
                const downloads: { url: string; filename: string }[] = []
                const startId = mm.id
                while (i < activeTab.messages.length && activeTab.messages[i].role === 'tool') {
                  const tm = activeTab.messages[i]
                  tools.push({ name: tm.name || '工具', status: /\[工具执行失败\]/.test(tm.content || '') ? 'error' : 'ok' })
                  if (tm.name === 'generate_word_document') {
                    const dmt = (tm.content || '').match(/\/download\/[^\s)\]]+\.docx/)
                    if (dmt) downloads.push({ url: dmt[0], filename: decodeURIComponent(dmt[0].split('/').pop() || '文档') })
                  }
                  i++
                }
                i--
                items.push({ kind: 'tools', id: startId, tools, downloads })
              } else {
                items.push({ kind: 'msg', msg: mm })
              }
            }
            return items.map((it) => {
              if (it.kind === 'tools') {
                return (
                  <div key={it.id} className="self-start max-w-[78%] ml-11 flex flex-col gap-2">
                    <details className="bg-[#f9fafb] border border-[#e5e7eb] rounded-xl">
                      <summary className="cursor-pointer select-none px-3 py-2 text-xs font-semibold text-[#374151]">
                        🔧 执行过程（{it.tools.length}/{it.tools.length}）
                      </summary>
                      <div className="px-3 pb-2 font-mono text-[12.5px] leading-[1.9]">
                        {it.tools.map((s, idx) => (
                          <div key={idx} className={`truncate ${s.status === 'ok' ? 'text-[#16a34a]' : 'text-[#dc2626]'}`}>
                            {s.status === 'ok' ? '[✅]' : '[❌]'} {s.name}
                          </div>
                        ))}
                      </div>
                    </details>
                    {it.downloads.map((d, idx) => (
                      <a key={idx} href={d.url} download={d.filename}
                        className="self-start inline-flex items-center gap-2 px-3.5 py-2 rounded-lg bg-[#2563eb] hover:bg-[#1d4ed8] text-white text-[13px] font-semibold no-underline">
                        <Download size={15} className="shrink-0" />
                        <span className="truncate">下载 {d.filename}</span>
                      </a>
                    ))}
                  </div>
                )
              }
              const msg = it.msg
              return (
            <div key={msg.id} className={`flex gap-3 ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}>
              <div className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 mt-0.5
                ${msg.role === 'user' ? 'bg-[#f3f4f6]' : 'bg-gradient-to-br from-[#3b82f6] to-[#6366f1]'}`}>
                {msg.role === 'user'
                  ? <User size={15} className="text-[#6b6b6b]" />
                  : <Bot size={15} className="text-white" />}
              </div>
              <div className="flex flex-col gap-1 max-w-[75%]">
                {msg.role === 'assistant' && msg.plan && (
                  /* 执行方案卡片（先列方案再执行）：折叠，置于回答气泡之前 */
                  <details open={!!msg.streaming} className="self-start bg-[#f5f7ff] border border-[#dbe3ff] rounded-xl">
                    <summary className="cursor-pointer select-none px-3 py-2 flex items-center gap-2 text-xs font-medium text-[#4060c0]">
                      <ClipboardList size={13} className="shrink-0" />
                      <span>📋 执行方案</span>
                    </summary>
                    <div className="prose-chat px-3 pb-2 text-[12.5px] leading-relaxed text-[#475569]">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.plan}</ReactMarkdown>
                    </div>
                  </details>
                )}
                {msg.role === 'assistant' && msg.tools && msg.tools.length > 0 && (
                  /* 工具执行实时清单（可折叠）：[ ]/[✅]/[❌]，运行中默认展开 */
                  <details open={!!msg.streaming} className="self-start bg-[#f9fafb] border border-[#e5e7eb] rounded-xl">
                    <summary className="cursor-pointer select-none px-3 py-2 text-xs font-semibold text-[#374151]">
                      🔧 执行过程（{msg.tools.filter(s => s.status !== 'running').length}/{msg.tools.length}）
                    </summary>
                    <div className="px-3 pb-2 font-mono text-[12.5px] leading-[1.9]">
                      {msg.tools.map((s, i) => (
                        <div key={i} className={`truncate ${s.status === 'ok' ? 'text-[#16a34a]' : s.status === 'error' ? 'text-[#dc2626]' : 'text-[#6b7280]'}`}>
                          {s.status === 'ok' ? '[✅]' : s.status === 'error' ? '[❌]' : '[ ]'} {s.name}
                        </div>
                      ))}
                    </div>
                  </details>
                )}
                <div className={`rounded-2xl px-4 py-3 text-sm ${msg.role === 'user'
                  ? 'bg-[#f3f4f6] text-[#171717]'
                  : 'bg-white border border-[#e5e5e5] text-[#171717]'}`}>
                  {msg.role === 'assistant'
                    ? <div className="prose-chat">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content || (msg.streaming ? '…' : '')}</ReactMarkdown>
                        {msg.streaming && <span className="inline-block w-1.5 h-4 bg-[#3b82f6] ml-0.5 animate-pulse rounded-sm" />}
                      </div>
                    : msg.content}
                </div>
                {msg.role === 'assistant' && msg.download && (
                  /* 确定性下载按钮：href 指向真实工具结果，不依赖模型转述链接 */
                  <a href={msg.download.url} download={msg.download.filename}
                    className="self-start inline-flex items-center gap-2 px-3.5 py-2 rounded-lg bg-[#2563eb] hover:bg-[#1d4ed8] text-white text-[13px] font-semibold no-underline">
                    <Download size={15} className="shrink-0" />
                    <span className="truncate">下载 {msg.download.filename}</span>
                  </a>
                )}
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
              )
            })
          })()}

          {/* Thinking animation */}
          {activeTab.thinking && (
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
        {activeTab.feedbackOpen && activeTab.messages.length > 0 && (
          <div className="mx-6 mb-2 border border-[#e5e5e5] rounded-xl bg-white overflow-hidden">
            <button
              onClick={() => updateTab(activeTabId, t => ({ ...t, feedbackExpanded: !t.feedbackExpanded }))}
              className="w-full flex items-center gap-2 px-4 py-2.5 text-xs text-[#6b6b6b] hover:bg-[#f7f7f8] transition-colors"
            >
              <ChevronDown size={13} className={`transition-transform ${activeTab.feedbackExpanded ? 'rotate-180' : ''}`} />
              对本轮回答的反馈（选填）
            </button>
            {activeTab.feedbackExpanded && (
              <div className="px-4 pb-4 border-t border-[#f0f0f0]">
                <div className="flex items-center gap-1 my-3">
                  {[1, 2, 3, 4, 5].map(s => (
                    <button key={s} onClick={() => updateTab(activeTabId, t => ({ ...t, rating: s }))}>
                      <Star size={18} className={s <= activeTab.rating ? 'text-amber-400 fill-amber-400' : 'text-[#d1d5db]'} />
                    </button>
                  ))}
                </div>
                <div className="flex gap-2">
                  <input value={activeTab.feedbackComment} onChange={e => updateTab(activeTabId, t => ({ ...t, feedbackComment: e.target.value }))}
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
              {/* 附件 chip */}
              {activeTab.attachment && (
                <div className="flex items-center gap-1.5 w-fit max-w-full mb-2 px-2.5 py-1.5 bg-indigo-50 border border-indigo-200 rounded-lg text-xs text-indigo-800">
                  <Paperclip size={12} className="shrink-0" />
                  <span className="truncate max-w-[360px]">{activeTab.attachment.filename}</span>
                  <button
                    onClick={() => updateTab(activeTabId, t => ({ ...t, attachment: undefined }))}
                    title="移除附件"
                    className="shrink-0 text-indigo-500 hover:text-indigo-700"
                  >
                    <X size={13} />
                  </button>
                </div>
              )}
              <input
                ref={fileInputRef}
                type="file"
                accept=".txt,.md,.rst,.html,.pdf,.docx"
                className="hidden"
                onChange={e => { const f = e.target.files?.[0]; e.target.value = ''; if (f) handleAttach(f) }}
              />
              <div className="border border-[#e5e5e5] rounded-2xl bg-white shadow-sm overflow-hidden focus-within:border-[#3b82f6] transition-colors">
                <textarea
                  ref={textareaRef}
                  value={activeTab.input}
                  onChange={e => { updateTab(activeTabId, t => ({ ...t, input: e.target.value })); autoResize() }}
                  onKeyDown={handleKey}
                  placeholder="输入消息，Enter 发送，Shift+Enter 换行…（可新建标签并行进行多个对话）"
                  rows={1}
                  className="w-full px-4 pt-3 pb-2 text-sm text-[#171717] resize-none focus:outline-none placeholder:text-[#9ca3af] bg-transparent"
                  style={{ minHeight: '44px', maxHeight: '180px' }}
                />
                <div className="flex items-center justify-between px-3 pb-3">
                  <button
                    onClick={() => { if (!activeTab.uploading) fileInputRef.current?.click() }}
                    disabled={!!activeTab.uploading}
                    title="上传文件（PDF / Word / 文本）"
                    className="w-8 h-8 flex items-center justify-center text-[#6b7280] rounded-lg transition-colors hover:bg-[#eef2f7] hover:text-[#3b82f6] disabled:opacity-40 disabled:cursor-wait"
                  >
                    <Paperclip size={16} />
                  </button>
                  <button
                    onClick={() => {
                      if (activeTab.streaming) { abortRefs.current[activeTabId]?.(); return }
                      sendMessage(activeTabId)
                    }}
                    disabled={!activeTab.streaming && !activeTab.input.trim() && !activeTab.attachment}
                    title={activeTab.streaming ? '停止生成' : '发送'}
                    className={`w-8 h-8 flex items-center justify-center text-white rounded-lg transition-all disabled:opacity-30 ${activeTab.streaming ? 'bg-red-500 hover:bg-red-600' : 'bg-[#3b82f6] hover:bg-[#2563eb]'}`}
                  >
                    {activeTab.streaming ? <Square size={12} className="fill-current" /> : <Send size={14} />}
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
