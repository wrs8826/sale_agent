import React, { useState, useEffect, useCallback } from 'react'
import {
  ScrollText, RefreshCw, Trash2, Wand2, FileText, Check, X,
  Loader2, AlertCircle, ChevronDown, ChevronRight,
} from 'lucide-react'
import { useApp } from '../context/AppContext'

interface StagingItem { filename: string; size: number; mtime: number }
interface DraftMeta {
  draft_id: string; source: string; created_at: number
  action: 'create' | 'update'; skill_name: string; region?: string
}
interface DraftRef { filename: string; op?: string; content: string }
interface DraftBody {
  action: 'create' | 'update'; skill_name: string; region?: string
  reason?: string; notes?: string; skill_md: string
  references?: DraftRef[]; delete_references?: string[]
}
interface DraftRecord { draft_id: string; source: string; created_at: number; draft: DraftBody }

const fmtSize = (n: number) => n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`

const PolicySkillPage: React.FC = () => {
  const { showToast } = useApp()

  const [staging, setStaging] = useState<StagingItem[]>([])
  const [drafts, setDrafts] = useState<DraftMeta[]>([])
  const [loading, setLoading] = useState(true)
  const [genFile, setGenFile] = useState<string | null>(null)   // 正在生成草稿的暂存文件
  const [genMsg, setGenMsg] = useState('')
  const [review, setReview] = useState<DraftRecord | null>(null)
  const [publishing, setPublishing] = useState(false)
  const [openRefs, setOpenRefs] = useState<Record<string, boolean>>({})

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const [s, d] = await Promise.all([
        fetch('/admin/policy-staging').then(r => r.json()),
        fetch('/admin/policy-skill/drafts').then(r => r.json()),
      ])
      setStaging(Array.isArray(s) ? s : [])
      setDrafts(Array.isArray(d) ? d : [])
    } catch {
      showToast('加载失败', 'error')
    } finally {
      setLoading(false)
    }
  }, [showToast])

  useEffect(() => { reload() }, [reload])

  const deleteStaging = async (name: string) => {
    const r = await fetch(`/admin/policy-staging/${encodeURIComponent(name)}`, { method: 'DELETE' })
    if (r.ok) { setStaging(prev => prev.filter(s => s.filename !== name)); showToast(`已删除：${name}`) }
    else showToast('删除失败', 'error')
  }

  const genDraft = async (filename: string) => {
    setGenFile(filename); setGenMsg('开始解析…')
    try {
      const resp = await fetch('/admin/policy-skill/draft', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename }),
      })
      if (!resp.ok) { const e = await resp.json().catch(() => ({})); throw new Error(e.error || `HTTP ${resp.status}`) }
      const reader = resp.body!.getReader()
      const dec = new TextDecoder()
      let buf = ''
      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const lines = buf.split('\n'); buf = lines.pop() ?? ''
        for (const line of lines) {
          if (!line.startsWith('data:')) continue
          const evt = JSON.parse(line.slice(5).trim())
          if (evt.type === 'status') setGenMsg(evt.message || '')
          else if (evt.type === 'error') { showToast(evt.message || '草稿生成失败', 'error') }
          else if (evt.type === 'done') {
            showToast(`草稿已生成：${evt.action === 'create' ? '新建' : '更新'} ${evt.skill_name}`, 'success')
            await reload()
          }
        }
      }
    } catch (e) {
      showToast(`草稿生成失败：${e instanceof Error ? e.message : ''}`, 'error')
    } finally {
      setGenFile(null); setGenMsg('')
    }
  }

  const openReview = async (id: string) => {
    try {
      const r = await fetch(`/admin/policy-skill/draft/${id}`)
      if (!r.ok) throw new Error()
      setReview(await r.json()); setOpenRefs({})
    } catch { showToast('打开草稿失败', 'error') }
  }

  const publish = async () => {
    if (!review) return
    setPublishing(true)
    try {
      const r = await fetch('/admin/policy-skill/publish', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ draft_id: review.draft_id }),
      })
      const data = await r.json()
      if (!r.ok || !data.ok) throw new Error(data.error || '发布失败')
      showToast(`已发布到 skills/${data.skill_name}（已热重载）`, 'success')
      setReview(null)
      await reload()
    } catch (e) {
      showToast(`发布失败：${e instanceof Error ? e.message : ''}`, 'error')
    } finally {
      setPublishing(false)
    }
  }

  const discard = async (id: string) => {
    const r = await fetch('/admin/policy-skill/discard', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ draft_id: id }),
    })
    if (r.ok) { setDrafts(prev => prev.filter(d => d.draft_id !== id)); showToast('草稿已丢弃') }
    else showToast('丢弃失败', 'error')
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-[#e5e5e5] bg-white shrink-0">
        <div>
          <h1 className="text-lg font-semibold text-[#171717]">政策 Skill 更新</h1>
          <p className="text-xs text-[#9ca3af] mt-0.5 flex items-center gap-1">
            <ScrollText size={11} />
            上传政策材料 → agent 生成草稿 → 人工审核 → 发布到政策 skill（隔离于正常知识库）
          </p>
        </div>
        <button onClick={reload} className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-[#6b6b6b] border border-[#e5e5e5] rounded-lg hover:bg-[#f7f7f8] transition-colors">
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} /> 刷新
        </button>
      </div>

      <div className="flex-1 overflow-y-auto scrollbar-thin p-6 flex flex-col gap-5 max-w-[900px]">

        {/* 暂存政策材料 */}
        <section className="bg-white rounded-xl border border-[#e5e5e5] overflow-hidden">
          <div className="px-4 py-3 border-b border-[#e5e5e5] flex items-center gap-2">
            <FileText size={15} className="text-[#6b6b6b]" />
            <span className="text-sm font-semibold text-[#171717]">暂存政策材料</span>
            <span className="ml-auto text-xs text-[#9ca3af]">{staging.length} 个</span>
          </div>
          {staging.length === 0 ? (
            <div className="px-4 py-8 text-center text-xs text-[#9ca3af]">
              暂无。请到「知识库管理」上传时选择 <b className="text-[#6b6b6b]">政策材料</b>。
            </div>
          ) : (
            <div className="divide-y divide-[#f3f4f6]">
              {staging.map(s => (
                <div key={s.filename} className="flex items-center gap-3 px-4 py-3">
                  <FileText size={15} className="text-[#9ca3af] shrink-0" />
                  <div className="min-w-0 flex-1">
                    <div className="text-sm text-[#171717] truncate">{s.filename}</div>
                    <div className="text-[11px] text-[#9ca3af]">{fmtSize(s.size)}</div>
                  </div>
                  <button
                    onClick={() => genDraft(s.filename)}
                    disabled={genFile !== null}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-white bg-[#3b82f6] rounded-lg hover:bg-[#2563eb] disabled:opacity-40 transition-colors"
                  >
                    {genFile === s.filename ? <Loader2 size={13} className="animate-spin" /> : <Wand2 size={13} />}
                    生成草稿
                  </button>
                  <button onClick={() => deleteStaging(s.filename)} disabled={genFile !== null}
                    className="p-1.5 text-[#9ca3af] hover:text-red-500 disabled:opacity-40 transition-colors">
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </div>
          )}
          {genFile && (
            <div className="px-4 py-2.5 bg-blue-50 border-t border-blue-100 text-xs text-[#3b82f6] flex items-center gap-2">
              <Loader2 size={13} className="animate-spin" /> {genMsg || '生成中…'}
            </div>
          )}
        </section>

        {/* 待审核草稿 */}
        <section className="bg-white rounded-xl border border-[#e5e5e5] overflow-hidden">
          <div className="px-4 py-3 border-b border-[#e5e5e5] flex items-center gap-2">
            <Wand2 size={15} className="text-[#6b6b6b]" />
            <span className="text-sm font-semibold text-[#171717]">待审核草稿</span>
            <span className="ml-auto text-xs text-[#9ca3af]">{drafts.length} 个</span>
          </div>
          {drafts.length === 0 ? (
            <div className="px-4 py-8 text-center text-xs text-[#9ca3af]">暂无草稿。对暂存材料点「生成草稿」。</div>
          ) : (
            <div className="divide-y divide-[#f3f4f6]">
              {drafts.map(d => (
                <div key={d.draft_id} className="flex items-center gap-3 px-4 py-3">
                  <span className={`shrink-0 px-2 py-0.5 rounded text-[11px] font-medium ${d.action === 'create' ? 'bg-emerald-50 text-emerald-600' : 'bg-amber-50 text-amber-600'}`}>
                    {d.action === 'create' ? '新建' : '更新'}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="text-sm text-[#171717] truncate">{d.skill_name}{d.region ? ` · ${d.region}` : ''}</div>
                    <div className="text-[11px] text-[#9ca3af] truncate">来源：{d.source}</div>
                  </div>
                  <button onClick={() => openReview(d.draft_id)}
                    className="px-3 py-1.5 text-xs text-[#3b82f6] border border-[#3b82f6]/40 rounded-lg hover:bg-blue-50 transition-colors">
                    审核
                  </button>
                  <button onClick={() => discard(d.draft_id)}
                    className="p-1.5 text-[#9ca3af] hover:text-red-500 transition-colors">
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>

      {/* 审核弹窗 */}
      {review && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-6" onClick={() => !publishing && setReview(null)}>
          <div className="bg-white rounded-2xl w-full max-w-[820px] max-h-[88vh] flex flex-col overflow-hidden" onClick={e => e.stopPropagation()}>
            {/* head */}
            <div className="px-5 py-4 border-b border-[#e5e5e5] flex items-center gap-3 shrink-0">
              <span className={`px-2 py-0.5 rounded text-[11px] font-medium ${review.draft.action === 'create' ? 'bg-emerald-50 text-emerald-600' : 'bg-amber-50 text-amber-600'}`}>
                {review.draft.action === 'create' ? '新建' : '更新'}
              </span>
              <div className="min-w-0">
                <div className="text-sm font-semibold text-[#171717] truncate">skills/{review.draft.skill_name}/</div>
                <div className="text-[11px] text-[#9ca3af] truncate">来源：{review.source}{review.draft.region ? ` · ${review.draft.region}` : ''}</div>
              </div>
              <button onClick={() => !publishing && setReview(null)} className="ml-auto p-1 text-[#9ca3af] hover:text-[#171717]"><X size={18} /></button>
            </div>

            {/* body */}
            <div className="flex-1 overflow-y-auto scrollbar-thin p-5 flex flex-col gap-4 text-sm">
              {(review.draft.reason || review.draft.notes) && (
                <div className="flex gap-2 px-3 py-2.5 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-800">
                  <AlertCircle size={14} className="shrink-0 mt-0.5" />
                  <div className="leading-relaxed">
                    {review.draft.reason && <div><b>判定：</b>{review.draft.reason}</div>}
                    {review.draft.notes && <div className="mt-1"><b>变更点：</b>{review.draft.notes}</div>}
                  </div>
                </div>
              )}

              <div>
                <div className="text-xs font-semibold text-[#6b6b6b] mb-1.5">SKILL.md</div>
                <pre className="text-[12px] leading-relaxed bg-[#f7f7f8] border border-[#e5e5e5] rounded-lg p-3 overflow-x-auto whitespace-pre-wrap break-words max-h-[280px] overflow-y-auto">{review.draft.skill_md}</pre>
              </div>

              <div>
                <div className="text-xs font-semibold text-[#6b6b6b] mb-1.5">references（{(review.draft.references || []).length} 个写入）</div>
                <div className="flex flex-col gap-1.5">
                  {(review.draft.references || []).map(r => {
                    const open = !!openRefs[r.filename]
                    return (
                      <div key={r.filename} className="border border-[#e5e5e5] rounded-lg overflow-hidden">
                        <button onClick={() => setOpenRefs(p => ({ ...p, [r.filename]: !open }))}
                          className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-[#f7f7f8]">
                          {open ? <ChevronDown size={14} className="text-[#9ca3af]" /> : <ChevronRight size={14} className="text-[#9ca3af]" />}
                          <span className="text-xs text-[#171717] truncate flex-1">{r.filename}</span>
                          {r.op && <span className="text-[10px] text-[#9ca3af]">{r.op === 'create' ? '新增' : '更新'}</span>}
                        </button>
                        {open && (
                          <pre className="text-[12px] leading-relaxed bg-[#f7f7f8] border-t border-[#e5e5e5] p-3 overflow-x-auto whitespace-pre-wrap break-words max-h-[240px] overflow-y-auto">{r.content}</pre>
                        )}
                      </div>
                    )
                  })}
                  {(review.draft.references || []).length === 0 && (
                    <div className="text-xs text-[#9ca3af]">无 references 写入（仅改 SKILL.md）。</div>
                  )}
                </div>
              </div>

              {(review.draft.delete_references || []).length > 0 && (
                <div>
                  <div className="text-xs font-semibold text-red-600 mb-1.5">将删除的 references</div>
                  <div className="text-xs text-[#6b6b6b]">{(review.draft.delete_references || []).join('，')}</div>
                </div>
              )}
            </div>

            {/* foot */}
            <div className="px-5 py-3.5 border-t border-[#e5e5e5] flex items-center justify-end gap-2 shrink-0">
              <button onClick={() => !publishing && setReview(null)} disabled={publishing}
                className="px-4 py-2 text-sm text-[#6b6b6b] border border-[#e5e5e5] rounded-lg hover:bg-[#f7f7f8] disabled:opacity-40">取消</button>
              <button onClick={publish} disabled={publishing}
                className="flex items-center gap-1.5 px-4 py-2 text-sm text-white bg-[#3b82f6] rounded-lg hover:bg-[#2563eb] disabled:opacity-40">
                {publishing ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />} 发布
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default PolicySkillPage
