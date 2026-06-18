export type Page = 'knowledge' | 'chat' | 'users' | 'settings' | 'policy'

export interface KnowledgeFile {
  name: string
  size?: number
  uploadedAt?: string
  status: 'ready' | 'processing' | 'error'
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'tool'
  content: string
  streaming?: boolean
  sources?: string[]
  name?: string                 // role=tool 时的工具名（Phase 0 工具轮持久化）
  args?: Record<string, unknown>
  plan?: string                 // role=assistant 时的执行方案（先列方案再执行，仅当轮，不持久化）
  download?: { url: string; filename: string }  // 由真实工具结果渲染的下载信息（generate_word_document）
  tools?: { name: string; status: 'running' | 'ok' | 'error' }[]  // 工具执行实时清单（live，不持久化）
}

export interface User {
  id: number
  username: string
  phone: string
  department: string
  role: 'admin' | 'user'
  is_banned: boolean
  created_at: string
  has_custom_settings?: boolean
  chat_model?: string
}

export interface ModelCfg {
  api_key: string
  api_key_mask?: string
  api_key_set?: boolean
  base_url: string
  model_name: string
}

export interface Settings {
  chat: ModelCfg
  cleaner: ModelCfg
  reranker: ModelCfg
  embedding: ModelCfg
}

export type ToastType = 'success' | 'error' | 'warning'

export interface ToastItem {
  id: string
  message: string
  type: ToastType
}

export interface RAGParams {
  topK: number
  chunkSize: number
  chunkOverlap: number
  bm25Weight: number
  bm25K: number
  vectorK: number
  useReranker: boolean
  separators: string[]
}

export interface AuthInfo {
  user_id: number
  username: string
  role: string
}
