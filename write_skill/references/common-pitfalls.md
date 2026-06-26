# 已知陷阱（按发生频率排）

## 1. RAG 缓存未自动构建 → "对话不返回内容"

**症状**：服务重启后第一次提问，agent 拿到 `rag_fn=None`，没检索就生成。

**根因**：`services.get_current_rag()` 只读缓存不构建。

**修复**：`/agent/chat` 必须用 `services.get_rag(chunk_size, chunk_overlap, separators)` 触发构建。已在 `api/agent.py` 内做。

**触发点**：如果以后加新的"需要 RAG 的"路由，照同样模板。

## 1b. PDF/Word 上传并入库后检索不到 / `/ingest` 写库被重建覆盖（已修）

**症状（修复前）**：上传 `.pdf`/`.docx`、点「清洗入库」显示成功，但语义检索永远召回不到其内容；只有 `read_document` 工具能整篇读到。

**双重根因**：
1. `/ingest` 的 Step 3 自己 embed + `col.add()` 写进 `simple_rag` 集合，但查询路径 `get_rag()`→`_rebuild_rag()`→`ChromaVectorStore` **每次删集合重建**（从 `DocumentLoader.load(docs/wiki/skill)` 重新读原文件），ingest 的写入在下一次查询就被整段丢弃——纯属无效功。
2. `DocumentLoader` 只加载 `allowed_extensions`（旧值不含 pdf/docx）且用 `read_text_smart`（按文本编码读，无法解析二进制）。`/ingest` 对 pdf/docx 又刻意不回写原文件，于是二进制内容**永远进不了索引**。

**修复（fix A）**：
- `allowed_extensions` 加 `.pdf/.docx`（`config.yaml` + `RAGConfig` 默认）；
- `DocumentLoader.load` 对 `.pdf/.docx` 走 `extract_text_from_file`（延迟导入防循环依赖），单文件失败只跳过；
- 删掉 `/ingest` 里被覆盖的 embed+Chroma 写入块，索引统一归口 `_rebuild_rag`（带嵌入缓存）。`/ingest` 只保留：清洗 → 纯文本回写 → `invalidate_rag()`。

**遗留权衡**：二进制文档的检索文本是 loader 的**原始提取**（未经 ingest 清洗）。正式公文清洗多为原样透传，影响小；若将来要让二进制也吃清洗结果，需引入 sidecar 清洗文本缓存（见分析备忘）。另：`extract_text_from_file` 在每次重建时对每个 PDF/docx 重跑（嵌入有缓存、提取没有），大量大 PDF 时重建会变慢，可按 mtime 加提取缓存。

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

**进阶**：仅在登录时检查 `is_banned` 不能踢掉"封禁前已登录"的会话。`/auth/me` 现在每次都查库判断，封禁后清 session 并返回 403；`web/user.html` 全局 fetch 拦截器识别该 403 跳转 `/?banned=1`，并以 30s 间隔轮询 `/auth/me` 实现会话内自动登出（非实时，最多延迟 30s）。管理员端封禁操作前会弹确认框（解封无需确认），见 `web-admin/src/pages/UsersPage.tsx` 的 `banUser` 状态。

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

## 36. read_document 读已入库的 PDF/Word 报错 / 读到乱码

**症状**：上传 PDF/Word 并点「清洗入库」后，再让助手 `read_document` 读该文件，PyMuPDF 解析失败或读到纯文本/乱码。

**根因**：`/ingest` 默认会把清洗后的纯文本覆写回原文件。若对二进制 PDF/.docx 也回写，原件就被破坏成「扩展名是 .pdf 但内容是文本」，`read_document` 的 PDF 分支（fitz）随之失败。

**修复**：`api/knowledge.py` 的 ingest 已加守卫——`target.suffix.lower() in _BINARY_DOC_EXT`（.pdf/.docx）时**跳过回写**，只把清洗文本存进向量库，原二进制文件保留。改 ingest 时不要去掉这个判断。

## 37. PDF 读取报「未安装 PyMuPDF」

**症状**：`read_document` 或 `/ingest` 读 PDF 返回「服务器未安装 PyMuPDF」。

**根因**：`pymupdf` 没装进**应用实际运行的环境**（本项目是 conda `agent` 环境，不是 base）。`extract_text_from_file` 缺依赖时抛错，工具/ingest 转成友好提示。

**修复**：`& E:\miniconda\envs\agent\python.exe -m pip install pymupdf`（已写进 `requirements.txt`）。`.docx` 用 `python-docx`（已装）。

## 38. 生成的 Word 下载链接打不开 / 下载不了

**症状**：让 agent 生成文档，给出的链接点不开；或链接长成 `https://<host>/<uuid>`（无 `/download/`、无 `.docx`）；或经某公网入口访问时"连接关闭"。

**四个独立根因（逐层排）**：
1. **single 模式下工具没被可靠调用** → 文件根本没生成。单趟工具决策对"生成整篇文档"不稳。**切 `agent_mode=react`**（多步循环会真正调 `generate_word_document`）。验证：`agent_service/downloads/`（复数目录）是否出现 `.docx`。
2. **依赖模型把 `/download/...docx` 链接写进回答 → 不可靠**：实测 DeepSeek 会把链接写错或**编造**成 `https://<部署域名>/<uuid>` 这种（无 `/download/`、无 `.docx`）。**已改为确定性下载**：`api/agent.py` 转发循环里对 `generate_word_document` 的 `tool_end` 用 `_extract_download()` 抽出真实 `/download/...`，下发 `download` 事件；两端据此渲染下载按钮（重载历史时从持久化的 `role=tool` 消息正则抽链接）；提示词也改成"不要自行编造下载链接，系统会显示按钮"。
   - **提示词与工具返回值不再自相矛盾（已修）**：早期 `generate_word_document` 的**返回串/ docstring 命令模型「请把链接原样提供给用户」**，与系统提示「不要粘贴链接、点下方按钮」直接打架，导致模型有时仍粘一个可能写错的链接。现 `builtin_tools.generate_word_document` 返回**中性**文案（仅 `文档已生成：[..](/download/..docx)`，不含粘贴指令），链接形态不变、`_extract_download` 照常工作。**飞书无下载按钮 UI**，需模型把链接写进回复，故由 `builtin_mcp_server` 转发层在重写为绝对链接后**追加**「请把上面的下载链接原样发给用户」——指令只加在飞书路径，网页端不经过转发层，二者不冲突。改这块时：网页端工具返回保持中性、靠系统提示约定；要让飞书发链接就在转发层加，**别**把粘贴指令塞回共享的 `builtin_tools` 返回值。
3. **工具轮上限吃满**：若某轮在 `max_tool_rounds` 步内还没轮到 `generate_word_document` 就到顶，这轮就没生成。`api/agent.py` 网页端已放宽到 15。注意：`download` 事件在工具执行完成那刻发出，**不受后续超时/超限影响**——只要工具真跑了按钮就在。
4. **相对链接 `/download/...` 经入口未转发**：按钮 href 是相对路径，依赖访问入口把 `/download` 转给 Flask。`web-admin` 开发态需在 `vite.config.ts` 代理 `/download`（和 `/admin`，否则政策页也断）；外部隧道/平台可能根本不转发或掐断该路径。

**定位方法（关键）**：本机直连 `http://localhost:5001/download/<file>` 与 `:5002/download/<file>`——若返回 200（含中文名，`Content-Disposition: filename*=UTF-8''...`），说明**应用层完全正常**，问题在外部入口/代理那层。`DOWNLOADS_DIR = agent_service/downloads`（PACKAGE_ROOT 下，绝对路径，与 cwd 无关）。

**本地访问对称**：用户端 `http://localhost:5001`、管理端 `http://localhost:5002`（Flask `app_admin` 直接服务 `web-admin/dist/`）；局域网用 `<本机局域网IP>:<port>`（后端监听 `0.0.0.0`）。相对下载链接会自动指向当前访问地址，故本地访问即走本地下载、不经任何公网入口。

## 39. `load_policy_file` 读不到 skill 的 `SKILL.md` / 带 `references/` 前缀扑空

**症状**：工具调用 `load_policy_file(skill_name="甬江人才政策", filename="SKILL.md")` 返回「文件 'SKILL.md' 不存在」；或模型传 `filename="references/资金政策.md"` 也读不到。

**根因**：旧实现只在 `SKILLS_ROOT/<skill>/references/<filename>` 下找。① `SKILL.md` 在 skill **根目录**不在 references/ → 读不到；② 文档地图里写的是带 `references/` 前缀的路径，模型照抄传进来 → 拼成 `references/references/...` → 扑空。模型之所以会去读 `SKILL.md`，是旧 docstring 写「已通过 SKILL.md 文档地图确定目标文件」，诱导它"先读 SKILL.md 拿地图"（其实地图在 detect_skill 命中后已注入 `skill_system_prompt`）。

**修复**（`agent_service/mcp/builtin_tools.py: load_policy_file`）：
- `safe = Path(filename).name` 取末段 → 自动剥掉 `references/` 前缀，同时防目录穿越；
- 候选路径 `[<skill>/references/<safe>, <skill>/<safe>]` 依次找 → **支持读根目录 `SKILL.md`**；
- 错误信息同时列出 references/ 和根目录的 `.md`；
- docstring 改为「文档地图已在系统提示中，无需再读 SKILL.md」。

**触发点**：`builtin_mcp_server.py` 从 `builtin_tools` 导入同一函数转发，**实现**自动同享此修复；新增带 references 的 skill 无需改工具。

**⚠️ docstring 不会自动同步（文档漂移坑）**：MCP 转发层（`builtin_mcp_server.py`）里每个 `@mcp_server.tool()` 包装函数**重新声明了自己的 docstring**——这才是飞书侧 LLM 读到的工具 schema，**不是** `builtin_tools` 里的那份。所以改了 `builtin_tools` 工具的 docstring/参数说明后，必须手工把 `builtin_mcp_server.py` 对应包装函数的 docstring 一并改掉，否则飞书侧模型读到旧提示（本坑的 `load_policy_file` 就曾因此残留「已通过 SKILL.md 确定目标文件」的旧措辞，诱导飞书侧重新去读 SKILL.md）。根治办法是让转发层复用 LangChain 工具的 `.description`/参数 schema、消除重复，未做之前以「改一处记得改两处」为准。

## 40. 置信度门控误用 hybrid_score（归一化分）→ 弱相关查询也被判「命中」(已修)

**症状（修复前）**：明明知识库里没有相关内容，问一个边缘/无关问题，agent 仍把检索到的无关片段当作上下文喂给模型（`has_hits=True`），生成「一本正经的幻觉」。OOD（库外）查询本该走「无上下文」分支却没走。

**根因**：`graph/qa/nodes.py: _hits_above_threshold` 早期用 `h.get("rerank_score", h.get("hybrid_score"))` 跟 `score_threshold`（默认 0.3）比较。但 `hybrid_score` 是 BM25/向量两路分数各自在**本次候选池**内 min-max 归一化（`simple_rag._make_normalizer`）后加权得到的——**只要召回了任何候选，排第一的那个就被归一化成 ≈1**，与「绝对相关性」脱钩。拿它跟绝对阈值比，几乎永远为真。

**修复**：新增 `_confidence_score(h)`，按「绝对可比」优先级取分：① `rerank_score`（reranker 的 0-1 相关性，跨查询可比，最可靠）→ ② `vector_score`（原始向量相似度 `1/(1+distance)`，未经候选池归一化）→ ③ `hybrid_score`（仅前两者都缺时兜底）。三处门控（`generate_node`/`plan_node`/`agent_react_node`）共用。

**⚠️ 阈值需按是否启用 reranker 重新标定**：`score_threshold`（config `chat.rag_score_threshold`）以前是跟「恒≈1 的归一化分」比，故 0.3 形同虚设。改后：
- **启用 reranker**：门控吃 `rerank_score`（干净的 0-1），0.3 左右合理；
- **未启用 reranker**：门控吃 `vector_score`。注意 Chroma 默认 L2 距离下 `1/(1+distance)` 有**下限**（归一化向量约 0.33），0.3 仍会放过几乎所有候选——**没接 reranker 时应把 `rag_score_threshold` 调高**（建议先按真实语料用 `eval/agent_eval.py` 的置信度维度标定，rag 类该过阈、ood 类该被挡）。

**触发点**：`vector_score` 键在 `HybridRetriever.search` 的每个候选上**恒存在**（BM25-only 命中也会被置 `vector_score=0.0`），故纯关键词命中、语义零重叠的片段会被门控挡掉——这是预期的保守行为（避免拿无语义关联的关键词巧合当高置信上下文）。

## 41. 飞书机器人：重复回答 + 并发丢历史（已修）

**症状（修复前）**：① 同一问题偶发被机器人回答两遍；② 用户连发两条消息时，其中一轮对话在历史里「消失」（下次重载历史看不到）。

**双重根因**：
1. **无幂等去重**：飞书事件是 **at-least-once**，网络抖动/ACK 丢失时会重推同一 `im.message.receive`（同一 `message_id`）。`lark_bot._on_p2p_message` 拿到回调就开线程处理，没按 `message_id` 去重 → 重推 = 重复回答 + `append_turn` 重复写。
2. **历史写入竞态**：`lark_history.append_turn` 是「读文件 → append → 整体覆写」。写本身是 `tmp+replace` 原子的，但**读到写之间无锁**；而 `_on_p2p_message` 每条消息开一个新线程，同一用户连发两条 → 两线程并发 `load→append→save`，**后写覆盖先写，丢一轮**。网页端已有「会话级单飞锁」，飞书路径当时没享受到。

**修复**（`agent_service/mcp/lark_bot.py`，单进程，用进程内锁即可，无需 filelock）：
- **幂等去重**：`LarkBot` 持一个有界 LRU（`OrderedDict`，上限 `_SEEN_MSG_MAX=512`）记最近处理过的 `message_id`；`_seen_before()` 做「检查即标记」原子操作，重复直接丢弃。在**派发线程前**（`_on_p2p_message` 内）就拦掉，连线程都不开。空 `message_id` 放行（无法去重）。
- **会话级单飞**：按 `f"{open_id}::{chat_id}"` 维度持锁（`_conv_lock()` 惰性建锁），在 `_reply_async` 里用 `with self._conv_lock(key):` **包住「读历史 → 生成 → append_turn → 回复」全过程**（含 clear/auth/deauth 指令），同会话消息串行执行，消除竞态。不同会话仍并行。

**注意/权衡**：
- 锁是**阻塞串行**（不像网页端单飞那样回 409 拒绝），因为飞书用户连发的两条都应被回答、只是排队，不能丢。
- `_conv_locks` / `_seen_msgs` 只在内存，**进程重启后清空**——重启瞬间正好重推的极少数消息可能漏去重（可接受）；`_conv_locks` 按会话数增长、不回收（机器人场景有界，量大可加 LRU 淘汰）。
- 改 `_reply_async` 时**别把锁去掉或缩小范围**：锁必须横跨 `_query`（内部 load_history）到 `append_turn`，否则竞态复活。

## 42. 用户端 Markdown 渲染 XSS + 依赖走外网 CDN（已修）

**症状（修复前）**：① 知识库里一份含 `<img src=x onerror=...>` 的文档，或诱导模型回显一段 HTML，下次任何人打开该会话即触发——**存储型 XSS**；② 内网/离线部署时 marked 走 `cdn.jsdelivr.net` 拉不到 → `marked` 未定义 → 每次 `renderMd` 抛错 → **聊天界面整体不可用**；③ CDN 无 SRI，被投毒即前端 RCE。

**根因**：`web/user.html` 的 `renderMd` 直接 `marked.parse(raw)` 后 `innerHTML` 注入，marked 默认放行内联 HTML、**不消毒**；而 `renderMd` 的输入覆盖**所有不可信来源**——用户输入、LLM 回答、历史里的 `role=tool` 工具结果（含知识库文档原文）。marked 又只从外网 CDN 加载、无本地兜底、无 SRI。

**修复**：
- **本地托管**：marked + DOMPurify 下载到 `web/assets/{marked.min.js,purify.min.js}`，`<script src="/assets/...">` 引用（`/assets/<filename>` 路由公开服务，与 `common.css` 同源）。去掉外网 CDN 依赖与供应链风险。
- **消毒 + fail-closed**：`renderMd` 改为 `DOMPurify.sanitize(marked.parse(raw))`；marked/DOMPurify 缺失或 `marked.parse` 抛异常时一律退 `_plainFallback`（`esc()` + `<br>`，**绝不注入未消毒 HTML**）；`marked.use(...)` 加 `if (window.marked)` 守卫，防加载失败拖垮整段内联脚本。

**触发点/约定**：前端渲染消息**必须经 `renderMd`**，别再图省事直接把 LLM/工具/用户文本拼进 `innerHTML`。错误消息、标题、工具名、文件名等非 Markdown 文本继续用 `esc()`。新增任何「把外部文本注入 DOM」的位置照此办。

**附带**：复制按钮原先裸调 `navigator.clipboard`，LAN HTTP（非安全上下文）下它是 `undefined`、点了没反应。已抽成 `copyMsg()`：安全上下文走 Clipboard API，否则退 `document.execCommand('copy')`，再失败 toast 提示。