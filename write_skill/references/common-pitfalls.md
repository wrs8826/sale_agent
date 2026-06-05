# 已知陷阱（按发生频率排）

## 1. RAG 缓存未自动构建 → "对话不返回内容"

**症状**：服务重启后第一次提问，agent 拿到 `rag_fn=None`，没检索就生成。

**根因**：`services.get_current_rag()` 只读缓存不构建。

**修复**：`/agent/chat` 必须用 `services.get_rag(chunk_size, chunk_overlap, separators)` 触发构建。已在 `api/agent.py` 内做。

**触发点**：如果以后加新的"需要 RAG 的"路由，照同样模板。

## 2. embedding 模型换了但向量库没清 → 检索 0 命中

**症状**：换 embedding model 后所有 query 都返回空。

**根因**：旧向量是 1024 维（text-embedding-v4），新模型可能是 1536 维（text-embedding-3-small）；chroma 拒绝跨维查询。

**当前处理**：
- `POST /settings` 保存时若检测到 `embedding.model_name/api_key/base_url` 任一字段变更，响应返回 `embedding_changed: true`
- `settings.js` 收到后自动调 `/vectordb/rebuild` SSE 端点，用新 embedding 重建向量库
- 重建过程：`invalidate_rag()` → `get_rag()` → `ChromaVectorStore` 删旧集合 + 用新 embedding 重建
- 注意：`ChromaVectorStore` 每次 `get_rag()` 总是重建集合（不复用旧向量），所以维度错位问题已在架构层规避

## 3. flex 子元素被挤消失

**症状**：按钮、面板头、滑块"消失"。

**根因**：flex 默认 `min-width: auto`，元素拒绝收缩，超出后被裁。

**修复**：
```css
.parent { display: flex; min-width: 0; }
.child  { flex: 1 1 0; min-width: 0; }
.btn    { flex-shrink: 0; padding: 0 1.2rem; min-width: 5rem; }
```

参见 `admin/knowledge.html` 已修过的 `.query-row` / `details.settings`。

## 4. 百分比 padding 在嵌套 flex/grid 里抖动

**症状**：浮动时按钮乱跳、面板宽度异常。

**根因**：`padding: X%` 相对父宽度计算，多层 flex 嵌套时父宽度计算和子宽度耦合，触发循环。

**修复**：统一改 rem / px。

## 5. SSE 中文变成 `\uXXXX`

**根因**：`json.dumps(...)` 默认 `ensure_ascii=True`。

**修复**：永远写 `json.dumps(payload, ensure_ascii=False)`。

## 6. SSE 被 nginx / cloudflare 缓冲住

**症状**：浏览器要等到流结束才出第一个 token。

**修复**：Response headers 永远带 `X-Accel-Buffering: no` 和 `Cache-Control: no-cache`。

## 7. `get_stream_writer` ImportError

**根因**：langgraph < 0.2.34。

**修复**：`pip install -U "langgraph>=0.2.34"`，`requirements.txt` 已锁。

## 8. 切换会话后旧消息残留

**症状**：从会话 A 切到 B，B 的气泡叠在 A 上面。

**根因**：忘了 `chatMsgs.innerHTML = ""`。

**修复**：`switchConversation` 必须先清空。

## 9. 前端传 history 服务端又覆盖 → 混乱

**症状**：用户在另一个标签页删了消息，本标签页又把旧 history 提交过来。

**根因**：早期 `/agent/chat` 拿客户端 history。

**修复**：当前已实现：有 `conversation_id` 时**忽略客户端 history**，以服务端持久化为权威源。新加路由如果涉及 history 务必照此做。

## 10. Chroma 在 Windows 上 SQLite 文件锁

**症状**：删除 / 移动 `chroma_persist/` 时报 "Device or resource busy"。

**根因**：进程还持有 SQLite 文件句柄。

**修复**：
- 关进程或重启
- PowerShell `Remove-Item -Path "<dir>" -Recurse -Force` 强删

## 11. config.yaml 中 `chat:` 段加了但没在 `load_chat_settings` 里读

**症状**：前端保存了但生效不了。

**修复**：四段访问器 `load_<x>_settings()` 必须显式列出要读的字段（默认值 `_DEFAULT_<X>`）；漏一个就生效不了。

## 12. 设置抽屉里改了 base_url 但 reranker 还用老的

**根因**：reranker 单例按 `(api_key, model_name)` cache，没把 base_url 进 cache key。

**当前状态**：reranker 用 dashscope 原生 HTTP，没用 base_url，所以这条暂时无感。但如果以后接其他 reranker 服务，需要把 base_url 也加进 cache key（`_reranker_key` 元组）。

## 13. langgraph 节点循环 import

**症状**：启动报 ImportError。

**根因**：节点文件 `from .. import services` 但 services 又 import graph。

**修复**：节点内做 lazy import：
```python
def my_node(state):
    from .. import services    # ← 在函数内
    ...
```

## 14. 路径硬编码 `Path("docs")`

**症状**：`python api/app.py` 直接跑 OK，但 `python -m api.app` 或从其他目录启动就找不到文件。

**修复**：永远 `from agent_service import DOCS_DIR`，路径常量已经是绝对的。

## 15. 加密的 api_key 被当成明文用

**症状**：发请求时 api_key 是 "enc:gAAAAAB..."，DashScope 401。

**根因**：直接读 `cfg.api_key`（带 enc: 前缀） 没解密。

**修复**：永远走 `services.load_chat_settings()` 等访问器，里面已经 `decrypt()` 过。**绝不直接读 `RAGConfig.api_key` / `cfg.chat["api_key"]`**。

## 16. 改 settings 后 RAG 用旧 embedding 继续查

**根因**：忘了失效 RAG 缓存。

**修复**：`services.save_settings(payload)` 已经做了 `invalidate_rag() + invalidate_reranker()`。如果你绕过 `save_settings` 直接改 yaml，记得手动失效。

## 17. 自动压缩死循环 / 误触发

**症状**：每条消息进来都触发压缩。

**根因**：token 估算偏高，或阈值太严。

**修复**：
- 检查 `estimate_tokens()` 系数
- 暂时把 `COMPACT_THRESHOLD` 调高（如 0.9）
- 极端情况：让 `compact_conversation` 返回 `unchanged` 时 emit `warning`，给前端调试看

## 18. 反馈 wiki 名重复覆盖

**症状**：1 秒内多次反馈，后一份覆盖前一份。

**根因**：文件名 `feedback_<YYYYMMDD_HHMMSS>_<rating>star.txt`，秒级精度不够。

**修复**：如果用户报这个问题，加毫秒到 `ts`：
```python
ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
```

## 19. 用户端 session 被当成管理员端 session（或反之）

**症状**：用户在 :5001 登录后访问 :5002 管理员端，发现已经是登录状态。

**根因**：两端 `secret_key` 相同，cookie 签名互通。

**修复**：`app_user.py` 用 `USER_SECRET_KEY`，`app_admin.py` 用 `ADMIN_SECRET_KEY`，两个环境变量必须**不同值**。如果都用默认值（内置字符串不同），也天然隔离。不要让两端共用同一个 `FLASK_SECRET_KEY`。

## 20. 普通用户调用 `/auth/admin-login` 拿到 403 但前端没提示

**症状**：管理员登录页用普通账号登录，按钮一直禁用但没有错误文字。

**根因**：前端 `doLogin` 只判断 `!res.ok`，忘了展示 `data.error`。

**修复**：检查 `admin/login.html` 的 `doLogin` 函数，确认 `errorMsg.textContent = data.error || '登录失败'` 路径已覆盖 403。当前实现已正确，若自己加新登录页注意照此模式。

## 21. 注册时 phone 校验在后端通过但前端无提示

**症状**：前端不报错、后端返回 400，但用户看不到原因。

**根因**：`doRegister` 只检查字段非空，没有前端手机号格式校验，后端才报错，但如果网络慢用户会困惑。

**当前状态**：手机号格式校验仅在后端（`^1[3-9]\d{9}$`），前端只做非空检查。如果想加前端实时校验，在 `r-phone` 的 `input` 事件里加 `/^1[3-9]\d{9}$/.test(val)` 即可。

## 22. 加新页面路由忘了在两个 app 文件都加 session 保护

**症状**：用户端新路由无需登录可直接访问。

**根因**：只在 `app_user.py` 里加了路由，忘了写 session 校验，或者只改了 `app.py`（shim，不生效）。

**修复**：每个非静态资源路由都要：
```python
if not session.get("user_id"):
    return redirect("/")
```
管理员端额外加：
```python
if session.get("role") != "admin":
    return redirect("/")
```

## 23. wiki_dir 改了但 RAG 还读旧目录

**症状**：在设置抽屉改了 Wiki 目录路径并保存，但检索时仍命中旧目录的文件。

**根因**：`services.get_rag()` 的缓存 key 包含 `str(wiki_dir)`，只要 `get_wiki_dir()` 返回新路径，下次请求就会重建。但如果 `save_settings()` 没触发 `invalidate_rag()`（例如绕过 `save_settings` 直接写 yaml），缓存不会失效。

**修复**：永远通过 `POST /settings` 改路径；`save_settings()` 已经内置 `invalidate_rag()`。

## 24. 封禁用户仍可登录

**症状**：管理员在用户管理页封禁了某用户，但该用户仍能成功登录。

**根因**：`/auth/login` 的 `_authenticate()` 只检查密码是否正确，未检查 `is_banned` 字段。

**修复**：在 `auth.py:_authenticate()` 的 SELECT 中加 `is_banned` 字段，并在 `login()` / `admin-login()` 里判断：
```python
if row.get("is_banned"):
    return jsonify({"error": "该账号已被封禁，请联系管理员"}), 403
```

## 25. `POST /ingest` 不传 filename → 400 / SSE 立即 error

**症状**：上传文件后调 `/ingest` 返回 400，或 SSE 里收到 `error` 事件，前端显示"上传失败"。

**根因**：`/ingest` 后端强要求 `{"filename": "..."}` JSON body；`fetch` 没设 `Content-Type: application/json` 或忘传 body，后端拿到空值直接报错。

**修复**：
```js
const res = await fetch('/ingest', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },   // ← 必须
  body: JSON.stringify({ filename: name }),            // ← 必须，name 来自 /upload 响应的 filename
});
```
**不要**直接 `fetch('/ingest', { method: 'POST' })` 裸调或只传 FormData。

## 26. 前端用 `ev.content` 读 token → 气泡永远为空

**症状**：agent 回复气泡出现但文字一直是空的；SSE 流正常接收到 token 事件，但 agentText 始终 `""`。

**根因**：`/agent/chat` SSE token 事件的字段名是 **`text`**，不是 `content`。用 `ev.content` 取到 `undefined`，字符串追加后还是空。

**修复**：
```js
if (ev.type === 'token') {
  agentText += ev.text || '';     // ← 正确：ev.text
}
if (ev.type === 'done') {
  if (ev.full_text) agentText = ev.full_text;   // done 事件用 full_text 定型
}
```
参考 `web-admin/src/pages/ChatPage.tsx` 确认字段名。

## 27. admin 只读模式失效 — `AuthInfo` 缺 `user_id`

**症状**：管理员打开他人会话时输入框仍然可用，或 `readOnly` 始终为 false。

**根因**：
1. `/auth/me` 早期版本只返回 `{username, role}`，没有 `user_id`
2. 前端 `AuthInfo` 类型没有 `user_id` 字段，`auth.user_id` 取到 `undefined`，导致 `readOnly` 计算永远为 false

**修复**：
- 后端 `auth.py /auth/me` 响应加 `"user_id": session["user_id"]`
- 前端 `types/index.ts` 的 `AuthInfo` 加 `user_id: number`
- `ChatPage` 从 `useApp().auth.user_id` 取 `myUserId`，与 `convUserId` 比较

## 28. 新增会话相关路由时忘记传 user_id → 所有用户数据混用

**症状**：新写的对话功能把所有用户的历史混在一起，或查别人的会话不报 403。

**根因**：`conversations.py` 中的核心函数全部需要显式 `user_id` 参数（v2 接口）：
```python
load_conversation(cid, user_id)      # 从用户子目录读
get_history(cid, user_id)            # 同上
append_turn(cid, user_id, ...)       # 同上
compact_conversation(cid, user_id, ...)
```
admin 跨用户操作使用 `find_conversation(cid)`（无 user_id 参数，搜所有子目录）。

**修复**：在 agent.py / 新蓝图中从 `session.get("user_id")` 取 uid，传给每一个 conv_store 调用。所有路由务必调 `_check_ownership(conv)` 做归属校验。

## 29. React 管理端对话列表加载不出来（用了 `conversations` 字段）

**症状**：`web-admin` ChatPage 侧边栏始终显示「暂无对话记录」，实际后端有历史会话。

**根因**：`GET /conversations` 响应体是 `{ "items": [...] }`，不是 `conversations`。前端用 `data.conversations ?? []` 取到 `undefined`，列表始终为空。

**修复**：
```ts
const data = await res.json()
setConvList(data.items ?? [])   // ← 必须用 items
```

## 30. `POST /feedback` 不传 history → 400 "对话历史为空"

**症状**：用户点评分提交后，接口返回 `400 { error: "对话历史为空" }`，反馈失败。

**根因**：`api/agent.py` 的 `/feedback` 路由第一步校验 `if not history: return 400`。老版本前端只传 `{rating, comment, conversation_id}`，缺了 `history` 字段。

**修复**：前端必须维护 `chatHist = [{role, content}]` 数组（每轮追加 user + assistant），评分时一并发送：
```js
body: JSON.stringify({
  rating,
  comment,
  history: chatHist,            // ← 必须，来自本地 chatHist 数组
  conversation_id: convId || '',
})
```
每次发起新对话时 `chatHist = []` 重置；`switchConversation` 后也要重置。

## 31. 飞书 `clear` 指令：cleaner API Key 未配置时历史不会删除

**症状**：用户发 `clear`，机器人回复 `❌ 未配置 API Key`，但历史文件仍然存在。

**根因**：`_save_and_clear` 在 `cleaner_cfg.api_key` 为空时提前 return，没有执行 `clear_history`。

**设计意图**：API Key 缺失时不能清洗，保留历史以免数据丢失，属于预期行为。

**修复**（如需强制删除）：把 `clear_history()` 移到 cleaner_cfg 检查之前，或在 settings 中配置好 cleaner API Key 后再使用 `clear` 命令。

## 32. 飞书通讯录查询报 99992357（Invalid department_id）

**症状**：调用 `list_contacts` 工具，控制台报 `99992357: The request you send is not a valid {open_department_id}`。

**根因**：`contact/v3/scopes` 默认返回原始 `department_id`（如 `cg5cdde4aa9cad8a`），但 `contact/v3/users` API 只接受 `open_department_id`（`od-xxx` 格式）。两端类型不一致导致参数无效。

**修复**：两处都加 `department_id_type=open_department_id`：
```python
# scopes 请求
params={"user_id_type": "open_id", "department_id_type": "open_department_id"}

# users 请求
params={"department_id": root, "department_id_type": "open_department_id", ...}
```

## 33. 飞书 LLM 调错工具报 99991679（应用未获取用户授权）

**症状**：通讯录权限已开通，用户查联系人时仍报 `99991679: 应用未获取所需的用户授权`。

**根因**：lark-mcp 自带 `contact_v3_user_batchGetId` 工具，该工具内部使用 user_access_token 模式。LLM 没有收到足够明确的指引，优先选择了这个工具而非自定义的 `list_contacts`（使用 app token）。

**修复**：在 `mcp_manager._invoke_agent` 的 system prompt 中明确写明：
- 查通讯录列表只能用 `list_contacts` / `search_contacts` / `list_departments`
- 严禁用 `contact_v3_user_batchGetId` 查列表（该工具仅用于已知 ID 的反查）

## 34. Redis session 启用后 `session.permanent` 必须为 True

**症状**：Redis 连通，但用户关闭浏览器后再打开仍需重新登录（短期免登录失效）。

**根因**：`session.permanent = True` 在登录路由里已经设置（`auth.py:login`），但 `flask-session` 的 `SESSION_PERMANENT=True` 是全局默认，两者必须同时成立，cookie 才会带 `Max-Age`。

**修复**：确认 `session_store.py` 中 `SESSION_PERMANENT=True` 存在；`auth.py` 的登录路由保留 `session.permanent = True`（已有）。

## 35. Redis 不可用时启动报错

**症状**：Redis 未启动，服务直接 500 或 ImportError。

**根因**：`configure_session` 内部有连通性测试（`r.ping()`），失败时打印警告并回退到 cookie session，不会崩溃。

**排查步骤**：
1. 查控制台：有 `[session_store] Redis 连接失败` → 回退 cookie session，功能正常但无 Redis 特性
2. 若 `flask-session` 未安装 → 安装：`pip install flask-session redis`
3. 若 Redis 密码错误 → 检查 `REDIS_PASSWORD` 环境变量（默认 `123456`）