import React, { useState, useRef, useCallback, useEffect } from 'react'
import {
  Upload, Trash2, FileText, CheckCircle, Loader2,
  Search, Database, Sliders
} from 'lucide-react'
import { useApp } from '../context/AppContext'
import type { KnowledgeFile, RAGParams } from '../types'

const SEPARATORS = ['Markdown标题', '空行', '段落', '换行', '中英文句末', '正则']

const fmtSize = (b?: number) => {
  if (!b) return ''
  return b >= 1048576 ? `${(b / 1048576).toFixed(1)} MB · ` : `${(b / 1024).toFixed(0)} KB · `
}

const StatusBadge: React.FC<{ status: KnowledgeFile['status'] }> = ({ status }) => {
  if (status === 'ready')
    return <span className="inline-flex items-center gap-1 text-xs text-green-600 bg-green-50 px-2 py-0.5 rounded-full"><CheckCircle size={11} />已向量化</span>
  if (status === 'processing')
    return <span className="inline-flex items-center gap-1 text-xs text-amber-600 bg-amber-50 px-2 py-0.5 rounded-full"><Loader2 size={11} className="animate-spin" />处理中</span>
  return <span className="text-xs text-red-500 bg-red-50 px-2 py-0.5 rounded-full">错误</span>
}

const DEFAULT_PARAMS: RAGParams = {
  topK: 5, chunkSize: 500, chunkOverlap: 50,
  bm25Weight: 0.5, bm25K: 1.5, vectorK: 8,
  useReranker: true, separators: ['Markdown标题', '空行', '段落'],
}

const SliderRow: React.FC<{
  label: string; value: number; min: number; max: number; step?: number
  onChange: (v: number) => void
}> = ({ label, value, min, max, step = 1, onChange }) => (
  <div>
    <div className="flex justify-between mb-1.5">
      <span className="text-xs text-[#6b6b6b]">{label}</span>
      <span className="text-xs font-medium text-[#171717]">{value}</span>
    </div>
    <input type="range" min={min} max={max} step={step} value={value}
      onChange={e => onChange(Number(e.target.value))}
      className="w-full h-1.5 bg-[#e5e5e5] rounded-full appearance-none cursor-pointer accent-[#3b82f6]"
    />
  </div>
)

const KnowledgePage: React.FC = () => {
  const { showToast } = useApp()
  const [files, setFiles] = useState<KnowledgeFile[]>([])
  const [filesLoading, setFilesLoading] = useState(true)
  const [dragging, setDragging] = useState(false)
  const [params, setParams] = useState<RAGParams>(DEFAULT_PARAMS)
  const [ingestingFile, setIngestingFile] = useState<string | null>(null)
  const [queryText, setQueryText] = useState('')
  const [queryResults, setQueryResults] = useState<{ text: string; score: string; source: string }[]>([])
  const [querying, setQuerying] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Load file list from backend on mount
  useEffect(() => {
    fetch('/files')
      .then(r => r.json())
      .then((names: string[]) => {
        setFiles(names.map(name => ({ name, status: 'ready' as const })))
      })
      .catch(() => showToast('加载文件列表失败', 'error'))
      .finally(() => setFilesLoading(false))
  }, [showToast])

  const setParam = <K extends keyof RAGParams>(k: K, v: RAGParams[K]) =>
    setParams(p => ({ ...p, [k]: v }))

  const toggleSep = (s: string) =>
    setParam('separators', params.separators.includes(s)
      ? params.separators.filter(x => x !== s)
      : [...params.separators, s])

  const handleFiles = useCallback(async (fileList: FileList) => {
    for (const file of Array.from(fileList)) {
      const allowed = ['.txt', '.md', '.rst', '.html']
      if (!allowed.some(ext => file.name.toLowerCase().endsWith(ext))) {
        showToast(`仅支持 .txt .md .rst .html，跳过：${file.name}`, 'error')
        continue
      }
      if (files.some(f => f.name === file.name)) {
        showToast(`已存在同名文件：${file.name}，将覆盖`, 'warning')
      }

      // Optimistic add with real metadata
      const newFile: KnowledgeFile = {
        name: file.name,
        size: file.size,
        uploadedAt: new Date().toLocaleString('zh-CN', { hour12: false }).slice(0, 16),
        status: 'processing',
      }
      setFiles(prev => [newFile, ...prev.filter(f => f.name !== file.name)])

      try {
        // 1. Upload
        const fd = new FormData()
        fd.append('file', file)
        const r = await fetch('/upload', { method: 'POST', body: fd })
        const data = await r.json()
        if (!r.ok) throw new Error(data.error || '上传失败')

        // 2. Ingest via SSE
        setIngestingFile(file.name)
        const resp = await fetch('/ingest', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ filename: data.filename }),
        })
        const reader = resp.body!.getReader()
        const dec = new TextDecoder()
        let buf = ''
        while (true) {
          const { value, done } = await reader.read()
          if (done) break
          buf += dec.decode(value, { stream: true })
          const lines = buf.split('\n')
          buf = lines.pop() ?? ''
          for (const line of lines) {
            if (!line.startsWith('data:')) continue
            const evt = JSON.parse(line.slice(5).trim())
            if (evt.type === 'result') {
              setFiles(prev => prev.map(f => f.name === file.name ? { ...f, status: 'ready' } : f))
              showToast(`${file.name} 入库成功（${evt.chunks_stored} 块）`)
            } else if (evt.type === 'error') {
              setFiles(prev => prev.map(f => f.name === file.name ? { ...f, status: 'error' } : f))
              showToast(evt.message, 'error')
            }
          }
        }
      } catch (e: unknown) {
        showToast(e instanceof Error ? e.message : '上传失败', 'error')
        setFiles(prev => prev.map(f => f.name === file.name ? { ...f, status: 'error' } : f))
      } finally {
        setIngestingFile(null)
      }
    }
  }, [files, showToast])

  const handleDelete = async (name: string) => {
    const r = await fetch(`/files/${encodeURIComponent(name)}`, { method: 'DELETE' })
    if (r.ok || r.status === 404) {
      setFiles(prev => prev.filter(f => f.name !== name))
      showToast(`${name} 已删除`)
    } else {
      showToast('删除失败', 'error')
    }
  }

  const handleQuery = async () => {
    if (!queryText.trim()) return
    setQuerying(true)
    setQueryResults([])
    try {
      const r = await fetch('/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          query: queryText, top_k: params.topK,
          chunk_size: params.chunkSize, chunk_overlap: params.chunkOverlap,
          bm25_weight: params.bm25Weight, bm25_k: params.bm25K,
          vector_k: params.vectorK, use_reranker: params.useReranker,
        }),
      })
      const data = await r.json()
      if (!r.ok) throw new Error(data.error)
      setQueryResults((data.hits || []).slice(0, params.topK).map((h: Record<string, unknown>) => ({
        text: String(h.text ?? ''),
        score: Number((h.rerank_score ?? h.hybrid_score ?? 0)).toFixed(3),
        source: String(((h.metadata as Record<string, unknown>)?.filename ?? (h.metadata as Record<string, unknown>)?.source ?? '未知来源')),
      })))
    } catch (e: unknown) {
      showToast(e instanceof Error ? e.message : '检索失败', 'error')
    } finally {
      setQuerying(false)
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-[#e5e5e5] bg-white shrink-0">
        <div>
          <h1 className="text-lg font-semibold text-[#171717]">知识库管理</h1>
          <p className="text-xs text-[#9ca3af] mt-0.5 flex items-center gap-1">
            <Database size={11} />
            qwen3-max 清洗 · text-embedding-v4 · BM25 混合 · gte-rerank-v2
          </p>
        </div>
      </div>

      {/* Body: 40/60 split */}
      <div className="flex flex-1 overflow-hidden">

        {/* Left 40% */}
        <div className="w-[40%] border-r border-[#e5e5e5] flex flex-col overflow-y-auto scrollbar-thin p-5 gap-5">

          {/* Upload card */}
          <div className="bg-white rounded-xl border border-[#e5e5e5] p-5">
            <div className="flex items-center gap-2 mb-4">
              <Upload size={16} className="text-[#3b82f6]" />
              <span className="text-sm font-semibold text-[#171717]">上传文件</span>
            </div>
            <div
              onDragOver={e => { e.preventDefault(); setDragging(true) }}
              onDragLeave={() => setDragging(false)}
              onDrop={e => { e.preventDefault(); setDragging(false); handleFiles(e.dataTransfer.files) }}
              onClick={() => fileInputRef.current?.click()}
              className={`h-[160px] rounded-xl border-2 border-dashed flex flex-col items-center justify-center gap-2 cursor-pointer transition-colors duration-150
                ${dragging ? 'border-[#3b82f6] bg-blue-50' : 'border-[#d1d5db] hover:border-[#3b82f6] hover:bg-[#f7f7f8]'}`}
            >
              <Upload size={28} className={dragging ? 'text-[#3b82f6]' : 'text-[#9ca3af]'} />
              <p className="text-sm text-[#6b6b6b]">拖拽到此处，或 <span className="text-[#3b82f6] font-medium">点击选择</span></p>
              <p className="text-xs text-[#9ca3af]">.txt .md .rst .html（最大 50 MB）</p>
            </div>
            <input ref={fileInputRef} type="file" multiple accept=".txt,.md,.rst,.html"
              className="hidden" onChange={e => e.target.files && handleFiles(e.target.files)} />
            {ingestingFile && (
              <p className="mt-3 text-xs text-[#3b82f6] flex items-center gap-1.5">
                <Loader2 size={12} className="animate-spin" />
                正在清洗入库：{ingestingFile}
              </p>
            )}
          </div>

          {/* File list */}
          <div className="bg-white rounded-xl border border-[#e5e5e5] overflow-hidden flex flex-col min-h-0">
            <div className="px-4 py-3 border-b border-[#e5e5e5] flex items-center gap-2 shrink-0">
              <FileText size={15} className="text-[#6b6b6b]" />
              <span className="text-sm font-semibold text-[#171717]">已上传文件</span>
              <span className="ml-auto text-xs text-[#9ca3af]">{files.length} 个</span>
            </div>
            {filesLoading
              ? <div className="p-4 flex flex-col gap-2">
                  {[1, 2, 3].map(i => <div key={i} className="h-10 bg-[#f3f4f6] rounded-lg animate-pulse" />)}
                </div>
              : files.length === 0
                ? <p className="text-sm text-[#9ca3af] text-center py-10">暂无文件，请先上传</p>
                : <ul className="divide-y divide-[#f3f4f6] overflow-y-auto scrollbar-thin max-h-[420px]">
                    {files.map(f => (
                      <li key={f.name} className="flex items-center gap-3 px-4 py-3 hover:bg-[#f7f7f8] group">
                        <FileText size={15} className="text-[#9ca3af] shrink-0" />
                        <div className="flex-1 min-w-0">
                          <p className="text-sm text-[#171717] truncate" title={f.name}>{f.name}</p>
                          {(f.size || f.uploadedAt) && (
                            <p className="text-[11px] text-[#9ca3af]">{fmtSize(f.size)}{f.uploadedAt}</p>
                          )}
                        </div>
                        <StatusBadge status={f.status} />
                        <button
                          onClick={() => handleDelete(f.name)}
                          disabled={f.status === 'processing'}
                          className="opacity-0 group-hover:opacity-100 text-[#9ca3af] hover:text-red-500 disabled:opacity-20 transition-all ml-1"
                        >
                          <Trash2 size={14} />
                        </button>
                      </li>
                    ))}
                  </ul>
            }
          </div>
        </div>

        {/* Right 60% */}
        <div className="flex-1 overflow-y-auto scrollbar-thin p-5 flex flex-col gap-5">

          {/* Params card */}
          <div className="bg-white rounded-xl border border-[#e5e5e5] p-5">
            <div className="flex items-center gap-2 mb-5">
              <Sliders size={16} className="text-[#3b82f6]" />
              <span className="text-sm font-semibold text-[#171717]">参数设置</span>
            </div>
            <div className="grid grid-cols-2 gap-x-8 gap-y-5">
              <div className="col-span-2">
                <p className="text-[11px] font-semibold text-[#9ca3af] uppercase tracking-wider mb-3">索引参数</p>
                <div className="grid grid-cols-2 gap-5">
                  <SliderRow label="分块大小 (Chunk Size)" value={params.chunkSize} min={200} max={2000} step={50}
                    onChange={v => setParam('chunkSize', v)} />
                  <SliderRow label="分块重叠 (Overlap)" value={params.chunkOverlap} min={0} max={500} step={10}
                    onChange={v => setParam('chunkOverlap', v)} />
                </div>
              </div>
              <div className="col-span-2">
                <p className="text-xs text-[#6b6b6b] mb-2">分隔符优先级</p>
                <div className="flex flex-wrap gap-2">
                  {SEPARATORS.map(s => (
                    <button key={s} onClick={() => toggleSep(s)}
                      className={`px-3 py-1 rounded-full text-xs font-medium border transition-all duration-150
                        ${params.separators.includes(s)
                          ? 'bg-[#3b82f6] text-white border-[#3b82f6]'
                          : 'bg-white text-[#6b6b6b] border-[#e5e5e5] hover:border-[#3b82f6]'}`}>
                      {s}
                    </button>
                  ))}
                </div>
              </div>
              <div className="col-span-2">
                <p className="text-[11px] font-semibold text-[#9ca3af] uppercase tracking-wider mb-3">检索参数</p>
                <div className="grid grid-cols-2 gap-5">
                  <SliderRow label="Top K" value={params.topK} min={1} max={50} onChange={v => setParam('topK', v)} />
                  <SliderRow label="BM25 权重" value={params.bm25Weight} min={0} max={1} step={0.05}
                    onChange={v => setParam('bm25Weight', v)} />
                  <SliderRow label="BM25 K" value={params.bm25K} min={0.1} max={2} step={0.1}
                    onChange={v => setParam('bm25K', v)} />
                  <SliderRow label="向量 K" value={params.vectorK} min={1} max={20}
                    onChange={v => setParam('vectorK', v)} />
                </div>
              </div>
              <div className="col-span-2 flex items-center gap-3">
                <button onClick={() => setParam('useReranker', !params.useReranker)}
                  className={`relative w-10 h-5 rounded-full transition-colors duration-200 ${params.useReranker ? 'bg-[#3b82f6]' : 'bg-[#d1d5db]'}`}>
                  <span className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-all duration-200 ${params.useReranker ? 'left-5' : 'left-0.5'}`} />
                </button>
                <span className="text-sm text-[#6b6b6b]">启用重排序 <span className="text-[#9ca3af]">gte-rerank-v2</span></span>
              </div>
            </div>
          </div>

          {/* Retrieval test card */}
          <div className="bg-white rounded-xl border border-[#e5e5e5] p-5">
            <div className="flex items-center gap-2 mb-4">
              <Search size={16} className="text-[#3b82f6]" />
              <span className="text-sm font-semibold text-[#171717]">检索测试</span>
            </div>
            <div className="flex gap-2">
              <input
                value={queryText} onChange={e => setQueryText(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleQuery()}
                placeholder="输入测试查询，回车或点击检索…"
                className="flex-1 px-3 py-2 border border-[#e5e5e5] rounded-lg text-sm focus:outline-none focus:border-[#3b82f6] transition-colors"
              />
              <button onClick={handleQuery} disabled={querying || !queryText.trim()}
                className="px-4 py-2 bg-[#3b82f6] hover:bg-[#2563eb] disabled:opacity-40 text-white rounded-lg text-sm font-medium transition-colors flex items-center gap-1.5">
                {querying ? <Loader2 size={13} className="animate-spin" /> : <Search size={13} />}
                检索
              </button>
            </div>

            {querying && (
              <div className="mt-4 flex flex-col gap-2">
                {[1, 2, 3].map(i => (
                  <div key={i} className="border border-[#e5e5e5] rounded-lg p-3 animate-pulse">
                    <div className="h-3 bg-[#f3f4f6] rounded w-1/3 mb-2" />
                    <div className="h-3 bg-[#f3f4f6] rounded w-full mb-1" />
                    <div className="h-3 bg-[#f3f4f6] rounded w-4/5" />
                  </div>
                ))}
              </div>
            )}

            {!querying && queryResults.length > 0 && (
              <div className="mt-4 flex flex-col gap-2">
                {queryResults.map((r, i) => (
                  <div key={i} className="border border-[#e5e5e5] rounded-lg p-3">
                    <div className="flex items-center justify-between mb-1.5">
                      <span className="text-[11px] font-medium text-[#6b6b6b] truncate">{r.source}</span>
                      <span className="text-[11px] text-[#3b82f6] shrink-0 ml-2">分数 {r.score}</span>
                    </div>
                    <p className="text-xs text-[#171717] leading-relaxed line-clamp-4">{r.text}</p>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

export default KnowledgePage
