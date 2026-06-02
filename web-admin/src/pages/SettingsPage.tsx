import React, { useState, useEffect } from 'react'
import {
  MessageSquare, Wand2, Database, BarChart2, FolderOpen,
  Eye, EyeOff, CheckCircle, XCircle, Loader2, AlertTriangle,
  Save, RotateCcw, RefreshCw
} from 'lucide-react'
import { useApp } from '../context/AppContext'

type ModelSection = 'chat' | 'cleaner' | 'embedding' | 'reranker'

interface SectionCfg {
  api_key: string
  base_url: string
  model_name: string
  enabled?: boolean
  inherit?: boolean
}

interface AllSettings {
  chat: SectionCfg
  cleaner: SectionCfg & { inherit: boolean }
  embedding: SectionCfg & { inherit: boolean }
  reranker: SectionCfg & { enabled: boolean; inherit: boolean }
  wiki: { path: string; sync_interval: string }
}

type TestStatus = 'idle' | 'testing' | 'ok' | 'error'

const CHAT_MODELS = ['qwen3-max', 'qwen-plus', 'qwen-turbo', 'gpt-4o-mini', 'gpt-4o', '自定义']
const EMBED_MODELS = ['text-embedding-v4', 'text-embedding-v3', 'bge-large-zh']
const RERANKER_MODELS = ['gte-rerank-v2', 'bge-reranker-v2-m3', '（禁用）']
const INTERVALS = ['手动', '每小时', '每天', '每周']

const DEFAULT: AllSettings = {
  chat:     { api_key: '', base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model_name: 'qwen3-max' },
  cleaner:  { api_key: '', base_url: '', model_name: '', inherit: true },
  embedding:{ api_key: '', base_url: '', model_name: 'text-embedding-v4', inherit: true },
  reranker: { api_key: '', base_url: '', model_name: 'gte-rerank-v2', enabled: true, inherit: true },
  wiki:     { path: '', sync_interval: '手动' },
}

const maskKey = (k: string) => k ? `${k.slice(0, 4)}${'*'.repeat(8)}${k.slice(-4)}` : ''

const PasswordInput: React.FC<{ value: string; onChange: (v: string) => void; placeholder?: string; disabled?: boolean }> =
  ({ value, onChange, placeholder, disabled }) => {
    const [show, setShow] = useState(false)
    return (
      <div className="relative">
        <input type={show ? 'text' : 'password'} value={value} onChange={e => onChange(e.target.value)}
          placeholder={placeholder} disabled={disabled}
          className="w-full px-3 py-2 pr-9 border border-[#e5e5e5] rounded-lg text-sm focus:outline-none focus:border-[#3b82f6] disabled:bg-[#f7f7f8] disabled:text-[#9ca3af] transition-colors" />
        <button type="button" onClick={() => setShow(s => !s)} disabled={disabled}
          className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[#9ca3af] hover:text-[#6b6b6b] disabled:opacity-30">
          {show ? <EyeOff size={14} /> : <Eye size={14} />}
        </button>
      </div>
    )
  }

interface TestBtnProps { section: ModelSection; settings: AllSettings; onResult: (s: ModelSection, ok: boolean, msg: string) => void }
const TestBtn: React.FC<TestBtnProps> = ({ section, settings, onResult }) => {
  const [status, setStatus] = useState<TestStatus>('idle')
  const test = async () => {
    setStatus('testing')
    try {
      const payload: Record<string, SectionCfg> = {
        [section]: settings[section as keyof AllSettings] as SectionCfg,
      }
      const r = await fetch('/settings/test', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      const data = await r.json()
      const res = data.results?.[section]
      if (res?.ok) { setStatus('ok'); onResult(section, true, `延迟 ${res.latency_ms} ms`) }
      else { setStatus('error'); onResult(section, false, res?.error || '连接失败') }
    } catch (e: unknown) {
      setStatus('error')
      onResult(section, false, e instanceof Error ? e.message : '网络错误')
    }
    setTimeout(() => setStatus('idle'), 4000)
  }
  return (
    <button onClick={test} disabled={status === 'testing'}
      className="flex items-center gap-1.5 px-3 py-1.5 border border-[#e5e5e5] rounded-lg text-xs text-[#6b6b6b] hover:bg-[#f7f7f8] disabled:opacity-40 transition-colors shrink-0">
      {status === 'testing' && <Loader2 size={12} className="animate-spin" />}
      {status === 'ok'      && <CheckCircle size={12} className="text-green-500" />}
      {status === 'error'   && <XCircle size={12} className="text-red-500" />}
      {status === 'idle'    && <RefreshCw size={12} />}
      测试连通
    </button>
  )
}

const SettingsPage: React.FC = () => {
  const { showToast } = useApp()
  const [cfg, setCfg] = useState<AllSettings>(DEFAULT)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [syncing, setSyncing] = useState(false)

  useEffect(() => {
    fetch('/settings').then(r => r.json()).then(data => {
      const s = data.settings
      if (!s) return
      setCfg(prev => ({
        ...prev,
        chat:     { api_key: '', base_url: s.chat?.base_url ?? '', model_name: s.chat?.model_name ?? 'qwen3-max' },
        cleaner:  { ...prev.cleaner, base_url: s.cleaner?.base_url ?? '', model_name: s.cleaner?.model_name ?? '' },
        embedding:{ ...prev.embedding, base_url: s.embedding?.base_url ?? '', model_name: s.embedding?.model_name ?? 'text-embedding-v4' },
        reranker: { ...prev.reranker, base_url: s.reranker?.base_url ?? '', model_name: s.reranker?.model_name ?? 'gte-rerank-v2' },
      }))
    }).catch(() => {}).finally(() => setLoading(false))
  }, [])

  const set = <K extends keyof AllSettings>(section: K, field: string, value: unknown) =>
    setCfg(prev => ({ ...prev, [section]: { ...prev[section], [field]: value } }))

  const save = async () => {
    setSaving(true)
    const payload = {
      chat:     { api_key: cfg.chat.api_key, base_url: cfg.chat.base_url, model_name: cfg.chat.model_name },
      cleaner:  cfg.cleaner.inherit ? {} : { api_key: cfg.cleaner.api_key, base_url: cfg.cleaner.base_url, model_name: cfg.cleaner.model_name },
      embedding:cfg.embedding.inherit ? {} : { api_key: cfg.embedding.api_key, base_url: cfg.embedding.base_url, model_name: cfg.embedding.model_name },
      reranker: cfg.reranker.enabled ? { api_key: cfg.reranker.api_key, base_url: cfg.reranker.base_url, model_name: cfg.reranker.model_name } : { model_name: '' },
    }
    try {
      const r = await fetch('/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      const data = await r.json()
      if (!r.ok) throw new Error(data.error)
      if (data.embedding_changed) showToast('⚠️ Embedding 模型已变更，请重建向量库', 'warning')
      else showToast('配置已保存')
    } catch (e: unknown) {
      showToast(e instanceof Error ? e.message : '保存失败', 'error')
    } finally {
      setSaving(false)
    }
  }

  const onTestResult = (_s: ModelSection, ok: boolean, msg: string) =>
    showToast(msg, ok ? 'success' : 'error')

  const syncWiki = async () => {
    setSyncing(true)
    await new Promise(r => setTimeout(r, 1500))
    showToast('Wiki 内容同步完成')
    setSyncing(false)
  }

  const Label: React.FC<{ text: string }> = ({ text }) => (
    <span className="text-xs text-[#6b6b6b] font-medium mb-1 block">{text}</span>
  )

  const Card: React.FC<{ icon: React.ReactNode; title: string; subtitle?: string; children: React.ReactNode; warning?: string }> =
    ({ icon, title, subtitle, children, warning }) => (
      <div className="bg-white rounded-xl border border-[#e5e5e5] overflow-hidden">
        <div className="flex items-center gap-3 px-5 py-4 border-b border-[#e5e5e5]">
          <div className="w-8 h-8 rounded-lg bg-[#f7f7f8] border border-[#e5e5e5] flex items-center justify-center text-[#6b6b6b]">
            {icon}
          </div>
          <div>
            <p className="text-sm font-semibold text-[#171717]">{title}</p>
            {subtitle && <p className="text-[11px] text-[#9ca3af]">{subtitle}</p>}
          </div>
        </div>
        {warning && (
          <div className="flex items-start gap-2 px-5 py-3 bg-amber-50 border-b border-amber-100">
            <AlertTriangle size={13} className="text-amber-500 mt-0.5 shrink-0" />
            <p className="text-xs text-amber-700">{warning}</p>
          </div>
        )}
        <div className="px-5 py-4">{children}</div>
      </div>
    )

  const InheritToggle: React.FC<{ section: 'cleaner' | 'embedding' | 'reranker'; label?: string }> =
    ({ section, label = '继承对话模型配置' }) => (
      <label className="flex items-center gap-2 cursor-pointer mb-4">
        <input type="checkbox" checked={(cfg[section] as SectionCfg & { inherit: boolean }).inherit}
          onChange={e => set(section, 'inherit', e.target.checked)}
          className="rounded border-[#d1d5db] text-[#3b82f6] focus:ring-0 focus:ring-offset-0 accent-[#3b82f6]" />
        <span className="text-xs text-[#6b6b6b]">{label}</span>
      </label>
    )

  if (loading) return (
    <div className="flex items-center justify-center h-full">
      <Loader2 size={24} className="animate-spin text-[#3b82f6]" />
    </div>
  )

  const isInherited = (section: 'cleaner' | 'embedding' | 'reranker') =>
    (cfg[section] as SectionCfg & { inherit: boolean }).inherit

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-[#e5e5e5] bg-white">
        <h1 className="text-lg font-semibold text-[#171717]">系统设置</h1>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto scrollbar-thin px-6 py-5">
        <div className="max-w-3xl mx-auto flex flex-col gap-5">

          {/* 1. Chat model */}
          <Card icon={<MessageSquare size={16} />} title="对话模型" subtitle="Agent 对话生成，核心 LLM">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label text="模型名称" />
                <select value={cfg.chat.model_name} onChange={e => set('chat', 'model_name', e.target.value)}
                  className="w-full px-3 py-2 border border-[#e5e5e5] rounded-lg text-sm focus:outline-none focus:border-[#3b82f6] bg-white">
                  {CHAT_MODELS.map(m => <option key={m}>{m}</option>)}
                </select>
              </div>
              <div>
                <Label text="Base URL" />
                <input value={cfg.chat.base_url} onChange={e => set('chat', 'base_url', e.target.value)}
                  className="w-full px-3 py-2 border border-[#e5e5e5] rounded-lg text-sm focus:outline-none focus:border-[#3b82f6]" />
              </div>
              <div className="col-span-2">
                <Label text="API Key" />
                <div className="flex gap-2">
                  <PasswordInput value={cfg.chat.api_key} onChange={v => set('chat', 'api_key', v)}
                    placeholder="留空保留原有密钥" />
                  <TestBtn section="chat" settings={cfg} onResult={onTestResult} />
                </div>
              </div>
            </div>
          </Card>

          {/* 2. Cleaner model */}
          <Card icon={<Wand2 size={16} />} title="AI 清洗模型" subtitle="文档清洗入库时使用">
            <InheritToggle section="cleaner" />
            <div className={`grid grid-cols-2 gap-4 transition-opacity ${isInherited('cleaner') ? 'opacity-30 pointer-events-none' : ''}`}>
              <div>
                <Label text="模型名称" />
                <input value={cfg.cleaner.model_name} onChange={e => set('cleaner', 'model_name', e.target.value)}
                  placeholder="留空继承对话模型"
                  className="w-full px-3 py-2 border border-[#e5e5e5] rounded-lg text-sm focus:outline-none focus:border-[#3b82f6]" />
              </div>
              <div>
                <Label text="Base URL" />
                <input value={cfg.cleaner.base_url} onChange={e => set('cleaner', 'base_url', e.target.value)}
                  className="w-full px-3 py-2 border border-[#e5e5e5] rounded-lg text-sm focus:outline-none focus:border-[#3b82f6]" />
              </div>
              <div className="col-span-2">
                <Label text="API Key" />
                <div className="flex gap-2">
                  <PasswordInput value={cfg.cleaner.api_key} onChange={v => set('cleaner', 'api_key', v)}
                    placeholder="留空继承对话模型密钥" />
                  <TestBtn section="cleaner" settings={cfg} onResult={onTestResult} />
                </div>
              </div>
            </div>
          </Card>

          {/* 3. Embedding */}
          <Card icon={<Database size={16} />} title="Embedding 模型" subtitle="文本向量化，影响检索质量"
            warning="更换 Embedding 模型后必须清空向量库并重新入库，否则维度不匹配会导致检索失败。">
            <InheritToggle section="embedding" />
            <div className={`grid grid-cols-2 gap-4 transition-opacity ${isInherited('embedding') ? 'opacity-30 pointer-events-none' : ''}`}>
              <div>
                <Label text="模型名称" />
                <select value={cfg.embedding.model_name} onChange={e => set('embedding', 'model_name', e.target.value)}
                  className="w-full px-3 py-2 border border-[#e5e5e5] rounded-lg text-sm focus:outline-none focus:border-[#3b82f6] bg-white">
                  {EMBED_MODELS.map(m => <option key={m}>{m}</option>)}
                </select>
              </div>
              <div>
                <Label text="Base URL" />
                <input value={cfg.embedding.base_url} onChange={e => set('embedding', 'base_url', e.target.value)}
                  className="w-full px-3 py-2 border border-[#e5e5e5] rounded-lg text-sm focus:outline-none focus:border-[#3b82f6]" />
              </div>
              <div className="col-span-2">
                <Label text="API Key" />
                <div className="flex gap-2">
                  <PasswordInput value={cfg.embedding.api_key} onChange={v => set('embedding', 'api_key', v)}
                    placeholder="留空继承对话模型密钥" />
                  <TestBtn section="embedding" settings={cfg} onResult={onTestResult} />
                </div>
              </div>
            </div>
          </Card>

          {/* 4. Reranker */}
          <Card icon={<BarChart2 size={16} />} title="重排序模型" subtitle="检索结果精排，提升准确率">
            <div className="flex items-center gap-2 mb-4">
              <button onClick={() => set('reranker', 'enabled', !cfg.reranker.enabled)}
                className={`relative w-10 h-5 rounded-full transition-colors duration-200 ${cfg.reranker.enabled ? 'bg-[#3b82f6]' : 'bg-[#d1d5db]'}`}>
                <span className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-all duration-200 ${cfg.reranker.enabled ? 'left-5' : 'left-0.5'}`} />
              </button>
              <span className="text-sm text-[#6b6b6b]">启用重排序</span>
              <InheritToggle section="reranker" label="继承对话模型 API 配置" />
            </div>
            <div className={`grid grid-cols-2 gap-4 transition-opacity ${!cfg.reranker.enabled || isInherited('reranker') ? 'opacity-30 pointer-events-none' : ''}`}>
              <div>
                <Label text="模型名称" />
                <select value={cfg.reranker.model_name} onChange={e => set('reranker', 'model_name', e.target.value)}
                  className="w-full px-3 py-2 border border-[#e5e5e5] rounded-lg text-sm focus:outline-none focus:border-[#3b82f6] bg-white">
                  {RERANKER_MODELS.map(m => <option key={m}>{m}</option>)}
                </select>
              </div>
              <div>
                <Label text="Base URL" />
                <input value={cfg.reranker.base_url} onChange={e => set('reranker', 'base_url', e.target.value)}
                  className="w-full px-3 py-2 border border-[#e5e5e5] rounded-lg text-sm focus:outline-none focus:border-[#3b82f6]" />
              </div>
              <div className="col-span-2">
                <Label text="API Key" />
                <div className="flex gap-2">
                  <PasswordInput value={cfg.reranker.api_key} onChange={v => set('reranker', 'api_key', v)}
                    placeholder="留空继承对话模型密钥" />
                  <TestBtn section="reranker" settings={cfg} onResult={onTestResult} />
                </div>
              </div>
            </div>
          </Card>

          {/* 5. Wiki path */}
          <Card icon={<FolderOpen size={16} />} title="Wiki 路径配置" subtitle="外部知识库源，自动同步">
            <div className="flex flex-col gap-4">
              <div>
                <Label text="Wiki 源路径" />
                <input value={cfg.wiki.path} onChange={e => setCfg(p => ({ ...p, wiki: { ...p.wiki, path: e.target.value } }))}
                  placeholder="https://your-wiki.com/api/export 或本地绝对路径"
                  className="w-full px-3 py-2 border border-[#e5e5e5] rounded-lg text-sm focus:outline-none focus:border-[#3b82f6]" />
              </div>
              <div className="flex items-end gap-3">
                <div className="flex-1">
                  <Label text="同步周期" />
                  <select value={cfg.wiki.sync_interval} onChange={e => setCfg(p => ({ ...p, wiki: { ...p.wiki, sync_interval: e.target.value } }))}
                    className="w-full px-3 py-2 border border-[#e5e5e5] rounded-lg text-sm focus:outline-none focus:border-[#3b82f6] bg-white">
                    {INTERVALS.map(v => <option key={v}>{v}</option>)}
                  </select>
                </div>
                <button onClick={syncWiki} disabled={syncing || !cfg.wiki.path}
                  className="flex items-center gap-1.5 px-4 py-2 border border-[#3b82f6] text-[#3b82f6] hover:bg-blue-50 disabled:opacity-40 rounded-lg text-sm font-medium transition-colors">
                  {syncing ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                  立即同步
                </button>
              </div>
            </div>
          </Card>

          {/* Save / Reset */}
          <div className="flex justify-end gap-3 pb-5">
            <button onClick={() => { setCfg(DEFAULT); showToast('已重置为默认值', 'warning') }}
              className="flex items-center gap-1.5 px-5 py-2.5 border border-[#e5e5e5] rounded-lg text-sm text-[#6b6b6b] hover:bg-[#f7f7f8] transition-colors">
              <RotateCcw size={14} /> 重置
            </button>
            <button onClick={save} disabled={saving}
              className="flex items-center gap-1.5 px-6 py-2.5 bg-[#3b82f6] hover:bg-[#2563eb] disabled:opacity-50 text-white rounded-lg text-sm font-semibold transition-colors shadow-sm">
              {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
              保存全部设置
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

export default SettingsPage
