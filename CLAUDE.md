# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Start user-facing server (port 5001)
python -m api.app_user

# Start admin server (port 5002)
python -m api.app_admin

# Install Python dependencies
pip install -r agent_service/requirements.txt
pip install cryptography PyMySQL

# Start admin frontend dev server (port 5173, proxies API to :5002)
cd web-admin && npm install && npm run dev
```

## Architecture Overview

This is a Flask + LangGraph sales assistant with RAG, dual-server architecture, and Feishu (Lark) integration.

### Dual-Server Split

Two independent Flask processes with separate sessions and secret keys:
- **`api/app_user.py`** → port 5001, vanilla JS frontend in `web/`
- **`api/app_admin.py`** → port 5002, React/TypeScript frontend in `web-admin/` (Vite dev) or `web/admin/` (vanilla)

Both register the same set of blueprints. Session cookies use different secret keys (`USER_SECRET_KEY` / `ADMIN_SECRET_KEY`) so they never cross-authenticate.

### Layer Boundaries

```
web/ web-admin/          ← vanilla JS or React; SSE consumers; fetch only relative paths
        ↓ HTTP/SSE
api/                     ← Flask blueprints; IO boundary; file writes; chroma writes; singleton cache
        ↓ Python calls
agent_service/           ← Pure logic: RAG, LangGraph graphs, encryption, config
        ↓ TCP
MySQL sales_agent.users  ← auth only
```

**Critical**: `agent_service/` never touches Flask/HTTP/MySQL. `api/` never calls LLM directly — it calls the graph or RAG singleton. All file paths use absolute constants from `agent_service/__init__.py` (`DOCS_DIR`, `WIKI_DIR`, `CHROMA_DIR`, `CONVERSATIONS_DIR`).

### Two LangGraph Graphs

**Cleaning subgraph** (`agent_service/graph/cleaning/`): `route_input → read_file? → clean → END`
- Called synchronously via `.invoke()`
- Input: `CleaningState{file_path?, raw_text?, system_prompt, cleaner_cfg}`
- Used by: `/ingest`, `/feedback`, conversation compaction

**QA main graph** (`agent_service/graph/qa/`): `extract_keywords → retrieve? → generate → END`
- Called with `.stream(state, stream_mode="custom")`
- Nodes push SSE events via `get_stream_writer()` (requires langgraph ≥ 0.2.34)
- SSE event types: `tool_start`, `tool_end`, `token`, `done`, `error`

### RAG & Settings Singletons (`api/services.py`)

- `services.get_rag(chunk_size, chunk_overlap, separators)` — builds and caches `HybridRetriever`; cache key includes doc filenames + params. Call `invalidate_rag()` after any file change.
- `services.load_chat_settings()` / `load_cleaner_settings()` / `load_reranker_settings()` / `load_embedding_settings()` — **always use these**, never read `config.yaml` directly. They decrypt `enc:` prefixed keys via `agent_service/security.py`.
- API keys are Fernet-encrypted with `enc:` prefix in `config.yaml`. The key lives at `agent_service/.secret_key`.

### Feishu (Lark) Integration

Two parallel modules in `agent_service/mcp/`:

| Module | Role |
|---|---|
| `mcp_manager.py` | Singleton: loads Feishu MCP tools via `langchain-mcp-adapters`, background asyncio thread |
| `lark_bot.py` | Singleton: `lark-oapi` SDK WebSocket long-connection bot; receives P2P messages, loads history, calls QA graph, persists turn, replies |
| `lark_history.py` | File-based chat history for the Feishu bot: `load_history` / `append_turn` / `clear_history`; files in `agent_service/lark_conversations/`, keyed by `open_id + chat_id`, rolling window of 10 turns |

Both started in `create_app()` as daemon threads. Config in `agent_service/mcp/lark_mcp.json` (top-level `app_id`/`app_secret` shared by both; `mcpServers` used only by `mcp_manager`).

`lark_bot._query()` uses lazy imports (`from api import services` inside the function) to avoid circular dependency — follow this pattern for any node/thread that needs `services`.

### Conversation Persistence

Stored as `agent_service/conversations/<uuid_hex>.json`. The `compact_at` pointer separates compressed history (kept for audit) from active history. `get_history()` prepends the summary as a system message. Two compaction levels: L1 manual (`compact` command), L2 auto-triggered at 80% of 32k token budget.

### API Key Encryption

`encrypt(plain)` → `"enc:" + Fernet token`. `decrypt(value)` recognizes the `enc:` prefix; bare strings are treated as legacy plaintext. Never log or return decrypted keys to the frontend — GET /settings returns only masked values.

## Key Conventions

- **SSE `ensure_ascii=False`**: always `json.dumps(payload, ensure_ascii=False)` to avoid `\uXXXX` encoding.
- **SSE response headers**: always include `Cache-Control: no-cache` and `X-Accel-Buffering: no`.
- **Frontend token field**: SSE `token` events use `ev.text` (not `ev.content`); `done` uses `ev.full_text`.
- **`/feedback` requires `history`**: frontend must pass the full `chatHist` array or the backend returns 400.
- **`/ingest` requires JSON body**: `{"filename": "..."}` with `Content-Type: application/json`.
- **Lazy imports in nodes/threads**: any `agent_service/` code that imports `api.services` must do so inside the function to avoid circular imports.
- **Do not use `Path("docs")`**: always use `DOCS_DIR / filename` from `agent_service/__init__.py`.

## Development Reference

Detailed documentation lives in `write_skill/references/`:
- `architecture.md` — full architecture with startup order
- `api-protocols.md` — all routes, SSE event schemas, request/response shapes
- `graph-patterns.md` — how to add nodes/subgraphs, stream writer usage
- `conversation-storage.md` — compaction algorithm, invariants
- `settings-encryption.md` — four-section config, inheritance rules, encryption chain
- `frontend-patterns.md` — SSE consumer template, settings drawer, CSS pitfalls
- `common-pitfalls.md` — 27 known issues with root causes and fixes (read before debugging)

**Workflow rule**: read `write_skill/SKILL.md` before modifying the project; sync `write_skill/references/` after any architectural change.
