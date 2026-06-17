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
