# API 协议详解

## 路由总览

| 路由 | 方法 | 蓝图 | 说明 |
|---|---|---|---|
| `/` `/user` | GET | app_user.py | 用户端静态 HTML（登录保护） |
| `/` `/admin` `/admin/knowledge` `/admin/chat` | GET | app_admin.py | 管理员端静态 HTML（admin 角色保护） |
| `/assets/<path>` | GET | app_*.py | 静态资源 |
| `/auth/register` | POST | auth | 用户注册（固定 user 角色） |
| `/auth/login` | POST | auth | 用户端登录（接受 user + admin） |
| `/auth/admin-login` | POST | auth | 管理员端登录（仅接受 admin） |
| `/auth/logout` | POST | auth | 清除 session |
| `/auth/me` | GET | auth | 返回当前登录信息 |
| `/users` | POST | users | 创建新用户（仅 admin） |
| `/users` | GET | users | 列出所有非 admin 用户（仅 admin） |
| `/users/<id>` | PATCH | users | 修改基本信息（密码/手机/部门/封禁） |
| `/users/<id>` | DELETE | users | 删除用户（不能删 admin 角色） |
| `/users/<id>/settings` | GET | users | 读取用户专属四段模型配置（api_key 脱敏） |
| `/users/<id>/settings` | POST | users | 保存用户专属模型配置（api_key 自动加密） |
| `/users/<id>/settings/test` | POST | users | 用表单值测试四段连通性（不保存） |
| `/files` | GET | knowledge | 列 docs/ 下文件 |
| `/upload` | POST | knowledge | multipart 上传（`ALLOWED_EXT`=.txt/.md/.rst/.html/.pdf/.docx；对话页回形针也复用此接口） |
| `/files/<name>` | DELETE | knowledge | 删 docs/ 下文件 |
| `/download/<file>` | GET | knowledge | 下载工具产物（DOWNLOADS_DIR，防目录穿越，**无 login_required，公开**） |
| `/query` | POST | knowledge | RAG 检索调试（含分块参数） |
| `/vectordb/clear` | POST | knowledge | 清空 chroma collection |
| `/vectordb/rebuild` | POST | knowledge | **SSE** 用当前 embedding 配置重建向量库（embedding 变更后自动触发，也可手动） |
| `/ingest` | POST | knowledge | **SSE** 单文件清洗+入库 |
| `/agent/chat` | POST | agent | **SSE** 对话主入口 |
| `/feedback` | POST | agent | **SSE** 评分写 wiki |
| `/settings` | GET / POST | settings | 四段配置读写 |
| `/settings/test` | POST | settings | 四模型连通测试 |
| `/conversations` | GET / POST | conversations | 列表 / 新建 |
| `/conversations/<id>` | GET / PATCH / DELETE | conversations | 取 / 改名 / 删 |
| `/conversations/<id>/compact` | POST | conversations | 手动压缩 |
| `/admin/policy-staging` | GET / DELETE | policy_skill | 列/删 政策材料暂存（**仅 admin 端注册 + admin 角色**） |
| `/admin/policy-skill/draft` | POST | policy_skill | **SSE** 解析暂存政策文件 → 按 policy_skill_maker 生成 skill 草稿 |
| `/admin/policy-skill/drafts` | GET | policy_skill | 列待审核草稿 |
| `/admin/policy-skill/draft/<id>` | GET | policy_skill | 取草稿全文（审核用） |
| `/admin/policy-skill/publish` | POST | policy_skill | 人工确认后落盘到 skills/（备份+热重载+删暂存/草稿） |
| `/admin/policy-skill/discard` | POST | policy_skill | 丢弃草稿（保留暂存） |

> **政策 skill 更新流隔离**：`policy_skill` 蓝图只在 `app_admin` 注册，每路由再校验 admin 角色。政策材料经 `/upload?kind=policy` 进 `POLICY_STAGING_DIR`（不进 RAG）；草稿生成复用清洗子图（system_prompt=`policy_skill_maker` body），agent 只产草稿不碰 live `skills/`；发布由后端落盘。正常用户对话（`/agent/chat`）完全接触不到这条流。

## SSE 事件协议（前端契约）

**所有 SSE 行格式**：`data: <json>\n\n`

### `/agent/chat`

| event.type | 字段 | 触发 |
|---|---|---|
| `status` | `message` | 通用状态文案，例如压缩进行中 |
| `tool_start` | `name` | 节点/工具开始（`提取关键词` / `检索知识库` / 实际工具名）；前端据此在执行清单加 `[ ] name` |
| `tool_end` | `name, keywords?, count?, result?, error?` | 节点/工具结束；前端把清单里对应项翻成 `[✅]`（无 `error`）或 `[❌]`（有 `error`）。react 工具失败时由 `agent_react_node` 按 `ToolMessage.status=='error'` 带上 `error` |
| `plan_start` | —— | 规划开始（仅 react + `enable_planning`），前端建「📋 执行方案」卡片 |
| `plan_token` | `text` | 执行方案的单个 token，**逐字追加渲染到方案卡片** |
| `plan_end` | `plan, warning?` | 方案完成（卡片折叠）；`plan` 为空表示规划失败/跳过，前端撤掉卡片 |
| `download` | `url, filename` | 生成可下载文件后下发（`generate_word_document`）。由 `api/agent.py` 从**真实工具结果**抽出 `/download/...docx`，前端据此渲染下载按钮——**不依赖模型把链接写进回答**（模型常把链接写错/编造） |
| `token` | `text` | 单个 token，**逐字追加渲染** |
| `done` | `full_text` | 生成完成，前端定型气泡 |
| `error` | `message` | 任何阶段失败 |
| `warning` | `message` | 非致命警告（如自动压缩失败但继续） |
| `conversation_saved` | `conversation_id, title, updated_at, message_count` | 持久化成功 |
| `compact_done` | `level, compacted_count?, kept_count?, summary_preview?, unchanged?, reason?` | 手动 compact 命令的结果 |
| `auto_compacted` | `level(=3), compacted_count, kept_count, summary_preview, tokens_before, tokens_after, total_compact_count` | L3 自动压缩完成 |
| `circuit_break` | `compacted_count, kept_count, summary_preview, tokens_before, tokens_after, total_compact_count(=0)` | L4 熔断：第 3 次自动压缩时持久化清零计数（折叠口径同 L3，头尾仍保留；仅"压缩频繁"提示） |

> **内部事件 `tool_turn`**：节点推出后由 `api/agent.py` 的转发循环拦截（`continue`，不下发前端），仅用于把本轮工具调用持久化为会话历史里的 `role=tool` 消息。新增 SSE 类型若**不希望**下发前端，照此在转发循环里拦截；`plan_*` 不在拦截名单，会正常下发。

> **下载链接的确定性下发**：转发循环里对 `tool_end` 且 `name == "generate_word_document"` 的事件，用 `_extract_download()`（正则 `/download/...docx`）从**工具结果原文**抽出 `{url, filename}`，先于该 `tool_end` 下发一个 `download` 事件。前端据此渲染下载按钮，**不依赖模型把链接写进回答**——实测 DeepSeek 会把下载链接写错/编造（如 `https://<host>/<uuid>`）。重载历史时，前端从持久化的 `role=tool`（`generate_word_document`）消息内容里同样正则抽链接渲染按钮。生成类工具的提示词也已改为「不要自行编造/改写下载链接，系统会显示下载按钮」。

### `/ingest`

| event.type | 字段 |
|---|---|
| `reading` / `cleaning` / `storing` | `message` |
| `result` | `raw_preview, cleaned_content, chunks_stored, raw_len, clean_len` |
| `error` | `message` |

### `/feedback`

| event.type | 字段 |
|---|---|
| `status` | `message` |
| `result` | `filename, filepath, cleaned_preview, raw_len, clean_len[, message]` |
| `error` | `message` |

## 请求 / 响应 schema

### `POST /users`
需 admin 角色 session。Body：
```json
{ "username": "2~32位", "password": "≥6位", "phone": "11位手机号", "department": "部门名（留空默认'未分配'）" }
```
响应：`201 { user: { id, username, role, phone, department, is_banned, chat_model, has_custom_settings, created_at } }`
- `409` 用户名已存在；`400` 校验失败
- `has_custom_settings` 初始为 `false`，`chat_model` 初始为 `""`

### `GET /users`
需 admin 角色 session。返回：
```json
{ "users": [{ "id": 1, "username": "...", "phone": "...", "department": "...",
  "has_custom_settings": true, "chat_model": "deepseek-v4-pro",
  "is_banned": false, "created_at": "2026-01-01 12:00" }] }
```
- `has_custom_settings`：用户是否配置了专属模型
- `chat_model`：用户专属 chat.model_name（供列表展示，空则继承系统）

### `PATCH /users/<id>`
需 admin 角色 session。Body 字段可任意组合（全部可选）：
```json
{ "password": "新密码≥6位", "phone": "11位手机号", "department": "部门", "is_banned": true }
```
响应：`200 { ok: true }` / `400 校验失败` / `404 用户不存在或无权`

### `DELETE /users/<id>`
需 admin 角色 session。不能删除 admin 角色账号。
响应：`200 { ok: true }` / `404 用户不存在或无权`

### `GET /users/<id>/settings`
返回用户四段专属模型配置（api_key 脱敏）：
```json
{ "settings": {
    "chat":      { "api_key_mask": "sk-***1234", "api_key_set": true, "base_url": "...", "model_name": "..." },
    "cleaner":   { ... },
    "embedding": { ... },
    "reranker":  { ... }
}}
```

### `POST /users/<id>/settings`
保存用户四段模型配置。api_key 若为空字符串则保留原值；非空则视为新明文并加密存储。
```json
{ "chat": { "api_key": "新明文或空", "base_url": "...", "model_name": "..." }, "cleaner": {...}, ... }
```
响应：`200 { ok: true, settings: <masked> }`

### `POST /users/<id>/settings/test`
用前端表单值（未保存）测试四段连通性。空 api_key → 自动回退到已保存值 → 再回退到系统设置。
```json
{ "chat": { "api_key": "...", "base_url": "...", "model_name": "..." }, ... }
```
响应：`{ "results": { "chat": { "ok": true, "error": "", "latency_ms": 234 }, ... } }`

### `POST /auth/register`
```json
{ "username": "2~32位", "password": "≥6位", "phone": "11位手机号", "department": "部门名" }
```
响应：`201 { ok: true, username }` / `400 校验失败` / `409 用户名重复`

手机号正则：`^1[3-9]\d{9}$`，角色固定为 `user`。

### `POST /auth/login`
```json
{ "username": "...", "password": "..." }
```
响应：`200 { user_id, username, role }` / `401 密码错误`

接受 user 和 admin 角色，写入 Flask session（7 天有效）。响应与 `/auth/me` 同形（含 `user_id`），前端 setAuth 后无需再请求 `/auth/me` 即可拿到自身 id。

### `POST /auth/admin-login`
```json
{ "username": "...", "password": "..." }
```
响应：`200 { user_id, username, role }` / `401 密码错误` / `403 非 admin`

role ≠ admin 时即使密码正确也返回 403。

### `GET /auth/me`
响应：`{ user_id, username, role }` / `401` / `403`
- `user_id` 为 MySQL `users.id`（int），前端用于判断会话归属
- 每次请求都会查库检查 `is_banned`：若账号已被封禁，清除 session 并返回 `403 { "error": "该账号已被封禁，请联系管理员" }`
- `web/user.html` 每 30s 轮询本接口，收到该 403 后跳转 `/?banned=1`，登录页据此显示封禁提示

### `POST /agent/chat`
```json
{
  "message": "用户问题",
  "conversation_id": "uuid hex (optional)",
  "top_k": 10
}
```
- **`top_k` 缺省时兜底到 `config.yaml`（`RAGConfig.top_k`），请求体显式提供则覆盖**（判定为「key 在且非 None」）。历史上这里写死兜底 `5`，导致 config 的 `top_k` 在对话链路里失效——已修复。`bm25_k`/`vector_k`/`bm25_weight` 一直读 config，不接受请求体覆盖。
- 若 `message.lower() ∈ {"compact", "/compact"}` 且有 `conversation_id`，触发**一级压缩**，**不进 QA 图**
- 若有 `conversation_id`，服务端的 `get_history()` 是**唯一权威源**，客户端不能传 `history`
- **无 `conversation_id` 且已登录**：后端自动创建新会话（绑定 `session["user_id"]`），返回 `conversation_saved` 事件携带新 ID
- 未登录用户：不持久化，无 `conversation_saved` 事件
- **admin 向他人会话发消息** → `403 "管理员只能查看用户对话历史，不能代用户发送消息"`（前端已通过只读模式阻断，后端作双重保护）
- **单飞 / 中断（`409`）**：同一会话同一时刻只允许一个生成在跑。入口用 `conv_store.acquire_conversation(cid, owner_id, wait=CHAT_LOCK_WAIT)` 取会话级锁（进程内 `threading.Lock` + 跨进程 `filelock`），贯穿整段 SSE 持有，于生成器 `finally` 释放（正常完成 / 异常 / 客户端断开 `GeneratorExit` 均释放）。取不到（已有生成/压缩，多为另一设备/标签页或管理端）→ `409 "该对话正在生成中，请先中断当前回答或稍后再试"`。**中断 = 客户端 abort SSE → 服务端折返进 finally 放锁，且因未走到 `append_turn`，本轮 user+assistant 整体不落盘（丢弃）**。前端：流式中禁用 Enter、发送键变「停止」、收到 409 回滚乐观插入的用户气泡并 toast。`compact` 命令路径同样取锁、409 语义一致。

### `POST /upload` (multipart)
```
form-data: file=<.txt|.md|.rst|.html|.pdf|.docx>, kind=normal|policy(可选,默认 normal)
```
响应：`{ok: true, filename, kind}`。
- `kind=normal`：落 `DOCS_DIR` 并 `invalidate_rag()`。对话页回形针上传复用此分支（文件入知识库后由 `read_document` 工具读取）。
- `kind=policy`（**仅 admin**）：落 `POLICY_STAGING_DIR`，**不进 RAG**，供「政策 skill 更新」流解析。

### `POST /ingest`
```json
{ "filename": "must-exist-in-docs/" }
```
读取走 `extract_text_from_file`（PDF/.docx 文本提取）；PDF/.docx 二进制原件清洗后**不回写**（保留原文供 `read_document` 读），纯文本仍回写。

**注意（索引归口）**：`/ingest` **不再**自己 embed + 写 Chroma——那套写入会被下一次 `get_rag()` 的整集合重建覆盖（`ChromaVectorStore` 每次删集合重建），属无效功。现在 `/ingest` 只做：清洗 → 纯文本回写原文件 → `invalidate_rag()`；实际嵌入/索引由 `services.get_rag()`→`_rebuild_rag()` 统一负责（带按 chunk 哈希的嵌入缓存）。`chunks_stored` 是「将被索引的分块数」估算（二进制文档实际以 loader 提取文本为准，可能略有出入）。PDF/.docx 的检索文本由 `DocumentLoader` 在重建时经 `extract_text_from_file` 结构化提取——`allowed_extensions` 现含 `.pdf/.docx`，故二进制文档**已能进入混合检索**（此前被静默跳过）。

### `POST /query`
```json
{
  "query": "...",
  "chunk_size": 800, "chunk_overlap": 150, "separators": [...]|null,
  "top_k": 10, "bm25_weight": 0.5, "bm25_k": 20, "vector_k": 20,
  "use_reranker": false
}
```
响应：`{hits: [...], rebuilt: bool, source_weights: {...}}`
- **所有检索旋钮（`chunk_size`/`chunk_overlap`/`separators`/`top_k`/`bm25_weight`/`bm25_k`/`vector_k`）缺省时兜底到 `config.yaml`，请求体显式提供则覆盖**（判定「key 在且非 None」，故 `bm25_weight=0.0` 纯向量这类合法假值不会被吞）。上方数值为示例 config 值，非写死默认。
- 历史上这里全部写死兜底（`chunk_size=400`/`top_k=5`/`bm25_k=8`…），与对话链路用的 `cfg.chunk_size=800` 等**不一致**——`/query` 会按 400 分块另建一套索引（`get_rag` 缓存键含 `chunk_size`），管理员测出的召回与线上对话不同。现已对齐到 config。

### `POST /settings`
```json
{
  "chat":      {"api_key": "新明文 or '' 保留", "base_url": "...", "model_name": "..."},
  "cleaner":   {...},
  "reranker":  {...},
  "embedding": {...}
}
```
- 任一字段缺失：保留原值
- `api_key` 为空字符串：保留原值
- `api_key` 非空：视为新明文，加密后存储
- 其他字段为空字符串：写回空，触发该段对 chat 的继承

响应：`{ok: true, settings: <masked>, embedding_changed: bool}`
- `embedding_changed: true` 时前端自动调 `/vectordb/rebuild` 重建向量库

### `GET /settings`
```json
{
  "settings": {
    "chat":      {"api_key_mask": "sk-******1234", "api_key_set": true, "base_url": "...", "model_name": "..."},
    "cleaner":   {...},
    "reranker":  {...},
    "embedding": {...}
  }
}
```

### `POST /settings/test`
无 body。响应：
```json
{
  "results": {
    "chat":      {"ok": true, "error": "", "latency_ms": 234},
    "cleaner":   {...},
    "reranker":  {...},
    "embedding": {...}
  }
}
```

### `POST /conversations`
请求：`{title?: "..."}`，响应：会话 summary（不含 messages 体）
- 需登录；自动绑定 `session["user_id"]`
- 会话文件存入 `conversations/<user_id>/<uuid>.json`

### `GET /conversations`
```json
{ "items": [ {id, user_id, title, created_at, updated_at, message_count, has_summary, compact_at}, ... ] }
```
按 `updated_at` 倒序
- **普通用户**：只返回自己的（扫 `conversations/<user_id>/`）
- **admin**：仅在管理端（`app_admin.py`，`app.config["IS_ADMIN_APP"] = True`）登录时返回全部用户会话（扫所有子目录）+ 根目录遗留文件；admin 账号登录用户端（`app_user.py`）时按普通用户逻辑，只能看到自己的会话

### `GET /conversations/<id>`
完整会话 JSON（详见 `conversation-storage.md`）
- 普通用户访问他人会话 → `403`；会话不存在 → `404`
- admin 用 `find_conversation` 跨目录搜索，可访问任何会话

### `PATCH /conversations/<id>`
需归属校验（普通用户只能改自己的）。取会话锁（`MUTATE_LOCK_WAIT`）后锁内重读+改名+保存，防与生成丢更新；取不到 → `409`。

### `DELETE /conversations/<id>`
需归属校验（普通用户只能删自己的）。取会话锁后再 `unlink`，避免删到一半另有生成写回复活僵尸文件；取不到 → `409`。

### `POST /conversations/<id>/compact`
请求：`{keep_tail_turns?: 10}`（L3，按轮，缺省 `L3_KEEP_TAIL_TURNS=10`；头部固定保留 `HEAD_KEEP_TURNS=3` 轮、折叠中间段），响应：`{ok, summary, compacted_count, kept_count}` 或 `{unchanged, reason}` 或 `{error}`
需归属校验（普通用户只能压缩自己的）。取会话锁后压缩（与该会话的生成/另一次压缩互斥，跨进程亦然）；取不到 → `409`。

## SSE 生成器写法（标准模板）

```python
from flask import Response, stream_with_context
import json

def generate():
    try:
        yield f"data: {json.dumps({'type':'status','message':'…'}, ensure_ascii=False)}\n\n"
        # ... do work ...
        yield f"data: {json.dumps({'type':'result', ...}, ensure_ascii=False)}\n\n"
    except Exception as exc:
        yield f"data: {json.dumps({'type':'error','message':str(exc)}, ensure_ascii=False)}\n\n"

return Response(
    stream_with_context(generate()),
    mimetype="text/event-stream",
    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
)
```

**ensure_ascii=False 必带**，否则中文会变 `\uXXXX`。
**X-Accel-Buffering: no** 阻止 nginx 缓冲整流。

## 加新路由的步骤

1. 在合适的蓝图文件加 `@bp.route(...)` 函数
2. 校验 → 调 `services.*` 取数据 → 落盘 / 返回
3. 如果是 SSE，照上面模板
4. 如果新路由要影响 RAG，结束前 `services.invalidate_rag()`
5. 不需要改 `app.py`（蓝图已经注册）
