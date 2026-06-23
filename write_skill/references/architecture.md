# 架构详解

## 三层分工

```
┌────────────────────────────────────────────────────────────┐
│  web/  (用户端浏览器)                                      │
│  ── vanilla JS、SSE 消费、settings 抽屉                    │
│  ── 用户端：login.html / user.html                         │
│  ── assets/：common.css / settings.js（用户端共享）        │
├────────────────────────────────────────────────────────────┤
│  web-admin/  (管理员端 React SPA)                          │
│  ── React + TypeScript + Tailwind + Vite                   │
│  ── 开发：port 5173（代理 API → :5002）                    │
│  ── 生产：npm run build → dist/ → Flask 5002 静态托管      │
└────────────────────────────────────────────────────────────┘
                            ↓ HTTP / SSE
┌────────────────────────────────────────────────────────────┐
│  api/  (Flask 蓝图)                                        │
│  ── 两个独立入口：app_user（5001）/ app_admin（5002）      │
│  ── 认证：auth.py（session + MySQL）                       │
│  ── 路由、SSE 包装、文件 IO、chroma 写入、单例缓存          │
└────────────────────────────────────────────────────────────┘
                            ↓ Python 函数调用
┌────────────────────────────────────────────────────────────┐
│  agent_service/  (业务核心)                                │
│  ── RAG、LangGraph 图、加密、配置                          │
│  ── skill_loader.py：三层披露；triggers 匹配；L1 表        │
├────────────────────────────────────────────────────────────┤
│  skills/  (技能包，与项目根同级)                           │
│  ── <技能名>/SKILL.md：frontmatter(name/desc/triggers)     │
│  ──                   + body = L2 系统提示                  │
│  ── <技能名>/references/*.md：L3 政策文档（工具按需读取）   │
└────────────────────────────────────────────────────────────┘
                            ↓ TCP
┌────────────────────────────────────────────────────────────┐
│  MySQL  sales_agent.users                                  │
│  ── 用户认证、注册信息（phone / department）               │
└────────────────────────────────────────────────────────────┘
```

**关键边界**：
- `agent_service/` 不感知 Flask / HTTP / 浏览器 / MySQL
- `api/` 不直接调 LLM；调 graph 或 RAG 单例
- `web/` 只通过 SSE 接收事件，发请求只 POST JSON

## 双端启动架构

两个独立 Flask 进程，各自独立 session：

```
用户访问 :5001           管理员访问 :5002
      |                        |
 app_user.py             app_admin.py
      |                        |
  / → login.html          /* → web-admin/dist/index.html (SPA)
  /user → user.html        /assets/* → web-admin/dist/assets/
      |                        |
 /auth/login              /auth/admin-login
 （接受 user + admin）    （仅接受 admin）
      |                        |
      └──────── 共享蓝图 ───────┘
       auth / agent / conversations
       knowledge / settings / users
```

**session 隔离原理**：两端使用不同的 `secret_key`（`USER_SECRET_KEY` / `ADMIN_SECRET_KEY`），cookie 名相同但签名不兼容，即使用同一浏览器访问两端，session 也不互通。

## Redis Session 缓存（`api/session_store.py`）

Session 数据存储在 Redis，实现空闲超时退出 + 短期免登录。

**配置参数（环境变量）：**

| 变量 | 默认值 | 说明 |
|---|---|---|
| `REDIS_HOST` | `127.0.0.1` | Redis 地址 |
| `REDIS_PORT` | `6379` | Redis 端口 |
| `REDIS_PASSWORD` | `123456` | Redis 密码 |
| `REDIS_DB` | `0` | Redis 数据库编号 |
| `SESSION_IDLE_MINUTES` | `30` | 空闲超时分钟数 |

**工作原理：**

```
用户发起请求
     │
     ▼
Flask 从 Redis 读 session（按 cookie 中的 session ID 查找）
     │ 找不到（超时或第一次）     │ 找到
     ▼                           ▼
session.get("user_id") = None  session.get("user_id") = 正常值
     │                           │
     ▼                           ▼
路由返回 401 / 跳转 /          正常处理请求
     │                           │
     ▼                           ▼
前端 fetch 拦截器                响应时刷新 Redis TTL（滑动窗口）
window.location.href = '/'
```

**Redis key 区分：**
- 用户端：`user_sess:<session_id>`
- 管理端：`admin_sess:<session_id>`

**降级行为**：Redis 不可用时自动回退为 Flask 原生 cookie session（`SESSION_IDLE_MINUTES` 仍然作为 `PERMANENT_SESSION_LIFETIME`）。

**前端 401 拦截**：所有页面（`user.html` / `knowledge.html` / `chat.html` / `users.html`）在 `<script>` 块最顶部通过 IIFE 包装 `window.fetch`，统一拦截 401 响应后跳转登录页。

## 认证流程

### 用户端注册
```
浏览器  POST /auth/register { username, password, phone, department }
api     校验格式（手机号正则 ^1[3-9]\d{9}$，密码≥6位）
api     INSERT INTO users (role='user', ...)
        → 201 { ok, username }
```

### 用户端登录
```
浏览器  POST /auth/login { username, password }
api     SELECT + check_password_hash
        role 可以是 user 或 admin
        → session { user_id, username, role }
浏览器  跳转 /user
```

### 管理员端登录
```
浏览器  POST /auth/admin-login { username, password }
api     SELECT + check_password_hash
        role 必须是 admin，否则 403
        → session { user_id, username, role }
浏览器  跳转 /admin
```

## 图编排（LangGraph）

两张并列图，由 `api/` 层在不同业务时机调用：

### 清洗子图（`graph/cleaning/`）

```
START ──route_input──┬─► read_file ──after_read──┬─► clean ── END
                     │                           └─► END (error)
                     └─► clean    (raw_text 已填)
                     └─► END      (raw_text & file_path 都缺)
```

- 输入：`CleaningState{file_path?, raw_text?, system_prompt, cleaner_cfg}`
- 输出：`cleaned_text`
- **复用 2 次**：`/ingest`（从文件读）+ `/feedback`（直接传 raw_text）+ 会话压缩（同样 raw_text 路径）

### QA 主图（`graph/qa/`）—— 两种形态由 feature flag `agent_mode` 决定

`build_qa_graph(agent_mode)` 编译不同图；`agent_mode` 来自 `services.get_agent_mode()`（env `AGENT_MODE` > config.yaml 顶层 `agent_mode` > `single`）。

```
single（默认）：START → call_tools → extract_keywords ──┬─► retrieve → generate → END
                                                        └─► generate → END (rag_fn=None)
react（多步循环）：START → extract_keywords ──┬─► retrieve → agent_react → END
                                              └─► agent_react → END
```

- **single**：`call_tools` 单趟一次工具调用 → 结果注入 `generate` 系统提示。
- **react**：先 `extract_keywords`+`retrieve` 预检索作 RAG grounding，再 `agent_react` 节点用 `create_react_agent`（与飞书主路径同款）多步 思考→调工具→观察 循环（`max_tool_rounds` → `recursion_limit`；网页端 `api/agent.py` 传 15，节点默认 5），最终流式作答。节点内用 `astream_events(v2)` 把 `on_chat_model_stream→token`、`on_tool_start/-end→tool_*` 映射回既有 SSE；工具记录汇总成一个 `tool_turn` 供持久化（Phase 0）。**网页端 + 飞书降级路径都按此 flag 切换**；飞书主路径本就是 ReAct。
  - **硬化**：① 调工具的 LLM 轮靠 `tool_call_chunks` 识别后**不外流其文本**（避免中间思考泄漏）；`full_text` 取自"无 tool_calls 的 `on_chat_model_end`"（权威终轮答案），前端 `done` 定型到它；② 超 `max_tool_rounds`（GraphRecursionError）时做一次**无工具强制收尾作答**（`_forced_answer`，附上已获取工具结果，流式）。
- 输入：`ChatState{query, history, chat_cfg, rag_fn, top_k, score_threshold, skill_system_prompt, skill_table, web_tools, agent_mode, max_tool_rounds, keywords, hits, tool_results, full_text, error}`
- `call_tools` 节点（single）优先执行：将 `skill_system_prompt`（含文档地图）注入给 LLM，LLM 按需调用内置工具（如 `load_policy_file`），结果写入 `tool_results`
- 节点用 `langgraph.config.get_stream_writer()` 推 SSE 事件
- 外层用 `graph.stream(state, stream_mode="custom")` 直接转发为 SSE 流
- `retrieve` 节点内部经 `rag_fn`（`api/agent.py` 闭包）调 `HybridRetriever.search()`（`agent_service/rag/simple_rag.py`）：用 `ThreadPoolExecutor` 并行执行 BM25 检索与向量检索（embedding API 调用 + Chroma 查询），再做混合分数融合。
  - **BM25 分词用 jieba**（`BM25Retriever._tokenize`）：连续中文先切词再建 BM25，否则 `\w+` 会把整段中文当成单个 token、中文词项召回退化。jieba 缺失时回退正则（降级，已加入 requirements）。
  - **分数归一化融合**（`HybridRetriever.search` + `_make_normalizer`）：BM25 分数无上界、向量分 ∈ (0,1]，量纲不同。融合前先把两路分数各自 **min-max 归一化到 [0,1]** 再按 `bm25_weight` 加权，使 `hybrid_score` 落在 [0,1]、`bm25_weight` 真正生效，且与 `score_threshold`（默认 0.3）门控语义兼容（**没用 RRF 正是因为 RRF 分数 ~0.016 会击穿该阈值**）。原始分仍保留在 `bm25_score`/`vector_score`，归一值在 `bm25_norm`/`vector_norm`（供 `/query` 调试）。
  - **对话路径与 `/query` 检索口径已对齐**：`rag_fn` 同样启用 reranker（`services.get_reranker()`，已配置即用、缺失则降级纯混合检索）+ `services.apply_source_weights()` 来源加权，召回放宽到 `max(k, bm25_k, vector_k)` 再截到 `k`。（历史上 `rag_fn` 只 `search(top_k=k)`、不带 reranker/权重，导致生产对话与管理员检索测试结果不一致——已修复。）
  - **嵌入持久缓存**（`_embed_documents_cached`，落 `<persist_directory>/embedding_cache.pkl`）：按 `(嵌入器类型+模型名, chunk 文本)` 的 sha256 为键缓存向量。重建索引时只对**新增/变更的 chunk** 调 embedding API，未变 chunk 复用缓存，避免每次文件增删都全量重算（API 嵌入成本/延迟随文档数线性增长）。写回时只保留当前语料的键（自然淘汰旧块）。**仅对按单块文本确定的嵌入器启用**（`OpenAIEmbedder`/`DashScopeEmbedder`/`SentenceTransformerEmbedder`）；**TF-IDF `SimpleEmbedder` 不缓存**（其向量依赖整个语料 fit，同文本在不同语料下向量不同）。换模型 → 键前缀变 → 全 miss 重嵌；维度变化由 `ChromaVectorStore` 每次重建集合兜底。注意 BM25 与 Chroma 集合仍按全语料重建（本地、廉价），被消除的是 embedding API 调用。

## 数据流

### 上传文档 → 入库
```
浏览器  POST /upload (multipart)
    ↓ 落盘到 docs/
api    POST /ingest (filename)
    ↓ SSE 流：reading / cleaning / storing / result
    ↓ build_cleaning_graph().invoke({raw_text, system_prompt, cleaner_cfg})
api    chunk + embed (cfg_with_embedding) + chroma.add
api    invalidate_rag()
```

### 用户提问
```
浏览器  POST /agent/chat {message, conversation_id?, top_k}
api    取 user_id = session["user_id"]，is_admin = (role=="admin")
api    检查 compact 命令 → 验证归属 → 早返回压缩响应
api    有 conversation_id → 验证归属 → load_conversation(cid, user_id)
       无 conversation_id 且已登录 → 自动创建新会话（user_id 子目录）
api    get_history(cid, user_id) (含 summary 拼成 system 消息)
api    估算 token，超 80% 阈值 → 自动压缩 → reload history
api    detect_skill(message) → skill_system_prompt（含文档地图）
api    build_skill_table() → skill_table（L1 常驻注入）
api    build_qa_graph().stream(state, stream_mode="custom")
        ↓ call_tools: 注入 skill_system_prompt，LLM 按需调 load_policy_file 等内置工具
        ↓ SSE: tool_start / tool_end / token / done / error
api    append_turn(cid, user_id, message, full_text)  ← 持久化
浏览器  收 conversation_saved {conversation_id} → 刷新侧栏
```

### 反馈
```
浏览器  POST /feedback {rating, comment, history}
api    拼 raw_text → build_cleaning_graph().invoke({_FEEDBACK_SYSTEM})
api    写 wiki/feedback_<ts>_<r>star.txt → invalidate_rag()
```

## Skill 系统（三层披露 + Path 2 工具读取）

### 目录约定

```
skills/
└── <技能名>/
    ├── SKILL.md          ← frontmatter(name/description/triggers) + body(L2 系统提示+文档地图)
    └── references/
        └── *.md          ← L3 精细化政策文档（按问题类型拆分，每文件 400-800 字）
```

### 三层披露

| 层级 | 内容 | 注入时机 |
|---|---|---|
| L1 | `build_skill_table()` 返回的 Markdown 表（name + description） | **常驻**注入每次 generate_node |
| L2 | `skill.system_prompt`（SKILL.md body，含角色定位 + 文档地图） | 关键词命中后注入 |
| L3 | `references/*.md` 政策原文 | `call_tools_node` 中 LLM 调 `load_policy_file` 按需读取 |

### Path 2 架构：工具按需读取（非 RAG）

**核心原则**：`references/` 文件**不进 RAG 向量索引**（`all_refs_dirs()` 返回 `[]`），由模型通过工具显式获取。

**运行流程**：
```
用户提问
  ↓
detect_skill(query) → skill_system_prompt（含文档地图 Markdown 表）
  ↓
call_tools_node：将 skill_system_prompt 注入 LLM
  LLM 读取文档地图 → 决定调哪个文件
  → load_policy_file("甬江人才政策", "申报条件_制造业.md")
  → 返回文件全文，写入 tool_results
  ↓
generate_node：skill_prompt + tool_results → 回答
```

**优势**：不同政策文档完全隔离（不存在跨政策 RAG 污染）；政策更新只需改文件，无需重建向量库；成本低（无 embedding）。

### 内置工具（`agent_service/mcp/builtin_tools.py`）

| 工具名 | 参数 | 用途 | 工具集 |
|---|---|---|---|
| `load_policy_file` | `skill_name, filename` | 读取 `skills/<skill_name>/references/<filename>` 全文 | 核心 |
| `get_current_time` | `timezone` | 获取当前日期时间 | 核心 |
| `generate_word_document` | `title, sections, filename?` | 生成 .docx 落 `downloads/`，返回 `/download/<file>` 链接 | 核心 |
| `read_document` | `filename` | 读取 `docs/` 中某文件的整篇文本（PDF/.docx/纯文本），过长截断；文件不存在时返回可用列表 | 仅网页 |
| `list_documents` | — | 列出 `docs/` 中可读取的文件名 | 仅网页 |

**两套工具集（飞书隔离）**：
- `BUILTIN_TOOLS` = 核心工具，网页端与飞书 QA 降级路径共用。
- `WEB_TOOLS` = 核心 + 文档读取工具（`read_document` / `list_documents`），**仅网页端**启用。
- 区分方式：`ChatState.web_tools` 标志。`api/agent.py` 构建 state 时置 `web_tools=True`；
  `lark_bot._query()`（飞书主路径 MCP Agent + 降级路径 QA 图）都不带此标志，故飞书拿不到文档读取工具。
- `call_tools_node` 与 `generate_node`（`build_tool_table(tools)`）都按 `state.get("web_tools")` 选择工具集。

**文档文本提取**：`extract_text_from_file(path)` 是 `read_document` 工具与 `/ingest` 清洗的单一实现：
`.pdf`→PyMuPDF + pdfplumber、`.docx`→unstructured（回退 python-docx）、纯文本→`text_utils.read_text_smart` 自动识别编码；`.doc` 与缺依赖时抛错。
**读 Word 用 unstructured 结构化，写 Word（`generate_word_document` 工具）仍用 python-docx**——读写两条路不要混。
- **纯文本编码**（`agent_service/text_utils.py`）：`read_text_smart(path)` 按 BOM→UTF-8→GB18030→charset-normalizer→UTF-8 replace 顺序解码，**GB18030 排在探测库前**（大陆旧文本几乎都是 GBK/GB2312，探测库对短样本易误判）。用户上传文本的三处读取统一走它：`extract_text_from_file` 纯文本兜底、`DocumentLoader.load`（RAG 索引）、清洗子图 `read_file_node`。**不要**再用 `read_text(encoding="utf-8", errors="ignore")` 读用户文本——会把 GBK 中文整段丢成空。
- `.pdf`（`_extract_pdf`）：**PyMuPDF 取正文文本块 + pdfplumber 取表格，按页内垂直位置合并**。落在 pdfplumber 表格 bbox 内的 PyMuPDF 文本块会被剔除（中心点判定），避免与表格内容重复（PyMuPDF 全页文本本就含被打散的表格单元格）。pdfplumber 缺失或单页解析异常时优雅降级为 PyMuPDF 全页文本，不报错。扫描版/图片 PDF 仍无文本层（无 OCR）。
- `.docx`（`_extract_docx_unstructured` 为主）：用 `unstructured.partition.docx.partition_docx` **按文档原始顺序**取元素——`Title`→渲染成 `## `（供 DocumentChunker 标题分隔识别）、`Table`→`metadata.text_as_html` 经 `_html_table_to_pipes` 转管道行、`ListItem`→`- `、其余→段落；**页眉/页脚也会被捕获**。unstructured 缺失或解析失败时回退 `_extract_docx_python_docx`（按 body XML 顺序遍历 `w:p`/`w:tbl`，保表格位置，**勿**用 `doc.paragraphs`+`doc.tables` 分别遍历那会把表格挪到文末）。

新增工具：在 `builtin_tools.py` 加 `@tool` 函数，追加到 `BUILTIN_TOOLS`（核心，飞书也要用）
或仅 `WEB_TOOLS`（仅网页端）。若要给飞书 MCP 路径用，还需在 `builtin_mcp_server.py` 加 `@mcp_server.tool()` 转发。

#### 两个 MCP server（飞书路径的工具来源）

`lark_mcp.json` 的 `mcpServers` 注册两个 stdio MCP server，`mcp_manager` 用 `MultiServerMCPClient`
把两者工具**合并**后注入飞书 ReAct Agent：

| MCP server | 启动命令 | 工具 |
|---|---|---|
| `lark-mcp` | `npx @larksuiteoapi/lark-mcp` | 纯飞书 API 工具（通讯录/消息/文档…） |
| `builtin-tools` | `python -m agent_service.mcp.builtin_mcp_server` | 3 个核心工具：`get_current_time` / `load_policy_file` / `generate_word_document` |

- 两个 server 都注入飞书 Agent；飞书因此可查时间、查政策原文、生成 Word。
- 文档读取工具（`read_document` / `list_documents`）**不在任一 MCP server**，只走网页端 `WEB_TOOLS`，飞书拿不到。
- 网页端不消费 MCP，直接 bind `BUILTIN_TOOLS`/`WEB_TOOLS`；MCP server 仅服务飞书路径。
- **下载链接绝对化**：`generate_word_document` 默认返回相对 `/download/<file>`（网页端浏览器自解析）；飞书路径在 `builtin_mcp_server.py` 转发层用 `_public_base_url()`（读 `lark_mcp.json` 的 `public_base_url`，缺省回退 `oauth_redirect_uri` 的 origin）把它重写为绝对 URL，便于飞书用户点击。`/download` 路由公开（无 `login_required`），故无需登录即可下载。

### 文档地图约定（SKILL.md body）

每个 L3 文件对应文档地图中的一行，格式：

```markdown
| 问题类型描述（触发条件） | `references/文件名.md` |
```

模型在 `call_tools_node` 中读取文档地图，按问题类型匹配对应文件名，调 `load_policy_file` 传入。

### 触发匹配（`skill_loader.py`）

1. SKILL.md frontmatter 的 `triggers:` YAML list（精确子串匹配，优先）
2. 降级：从 `description` 抽取引号包裹词 + `- ` 条目首词

`detect_skill(query)` 返回第一个命中的 `SkillDef`，无匹配返回 `None`。

## 单例与缓存（`api/services.py`）

| 单例 | 缓存 key | 失效时机 |
|---|---|---|
| `_rag` | `(docs 文件名 frozenset, wiki 文件名 frozenset, chunk_size, chunk_overlap, separators)` | 文件增删 / 分块参数改 / 显式 `invalidate_rag()`；重建逻辑由 `_rag_lock`（`threading.Lock` + 双重检查）保护，避免多对话并行请求同时触发重建 |
| `_reranker` | `(api_key, model_name)` | settings 改 / 显式 `invalidate_reranker()` |
| `Fernet` 实例 | 全局 lazy 单例 | 重启 |

settings 写回（POST /settings）后两个缓存都会被强制失效。

## 配置（`config.yaml`）

> ⚠️ 下面是**结构示例**，model/provider 以实际 `config.yaml` 为准。当前部署：chat 走 DeepSeek、embedding/reranker 走 DashScope 在线服务（**非**本地 sentence-transformers）。

```yaml
# 控制台日志级别：DEBUG / INFO / WARNING / ERROR（默认 INFO）。排查问题改 DEBUG。
log_level: INFO

# 旧字段（向后兼容；embedder 在 chat fallback 链中末尾）
api_key: "sk-..."             # 明文（legacy）。chat.api_key 为空时回退到这里 → 当前部署正用此明文 key 作对话 key
api_base: "https://api.deepseek.com/v1"
embedder_name: "text-embedding-v4"
api_provider: openai           # openai/dashscope → 走在线 embedding API；null → 才回退本地 sentence-transformers
use_sentence_transformers: true

# 新四段（settings 抽屉写入；api_key 加密）
chat:     { api_key: "enc:... 或空", base_url: "https://api.deepseek.com", model_name: "deepseek-v4-pro", rag_score_threshold: 0.3 }
cleaner:  { api_key: "",        base_url: "",    model_name: "" }    # 空=继承 chat
reranker: { api_key: "enc:...", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", model_name: "gte-rerank-v2" }   # DashScope 在线
embedding:{ api_key: "enc:...", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", model_name: "text-embedding-v4" } # DashScope 在线，需 api_key/base_url

# 存储配置（settings 抽屉 Storage 段写入）
storage:
  wiki_dir: ""                # 留空使用默认 agent_service/wiki/；支持绝对/相对路径

# 数据源权重（检索后置加权）
source_weights:
  docs: 1.0
  wiki: 0.7
  skill: 1.0                  # 仅当 all_refs_dirs() 返回非空时才生效；当前 Path 2 架构下它返回 []，故此权重休眠（见 Skill 系统节）
```

加密值 `enc:` 前缀；明文存历史值仍能读。详见 `settings-encryption.md`。

## 持久化

| 数据 | 位置 |
|---|---|
| 用户账号 | MySQL `sales_agent.users` |
| 上传文档 | `agent_service/docs/<name>` |
| 反馈摘要 | `<wiki_dir>/feedback_<username>_<conv8>_<ts>_<rating>star.txt`（wiki_dir 可在设置中配置） |
| 向量库 | `agent_service/chroma_persist/` |
| 会话 | `agent_service/conversations/<uuid>.json` |
| Fernet 密钥 | `agent_service/.secret_key`（gitignored） |
| 配置 | `agent_service/config.yaml` |

所有相对路径以 `agent_service/` 为基准（CONFIG_PATH.parent）。

## 飞书 MCP 集成（`langgraph/`）

独立于现有 QA 图之外，并行的 MCP Agent 能力：

```
langgraph/
├── __init__.py
├── mcp_manager.py      ← MCPManager 单例；后台 asyncio 线程持久持有 MCP 上下文
└── mcp/
    ├── __init__.py
    └── lark_mcp.json   ← MCP 服务器配置（lark-cli mcp）
```

### MCP 生命周期

```
app 启动 → mcp_manager.start()
    ↓ 后台 daemon 线程 + asyncio.new_event_loop()
    ↓ client = MultiServerMCPClient(config)（不用 async with，>=0.1.0 已移除 CM 支持）
    ↓ tools = await client.get_tools()，self._client = client（持有引用防 GC）
    ↓ get_tools() 成功 → state=ready，广播 lark_status 事件
    ↓ await shutdown_event.wait()  ← 永不退出，保持 MCP 连接活跃
```

### 相关文件

| 文件 | 职责 |
|---|---|
| `agent_service/mcp/mcp_manager.py` | MCPManager 单例：状态管理 / 工具加载 / 同步桥接 |
| `agent_service/mcp/lark_bot.py` | LarkBot 单例：SDK 长连接收消息 / 优先调 MCP ReAct Agent / 降级走 QA 图 / 回复 |
| `agent_service/mcp/lark_history.py` | 飞书对话历史持久化（文件存储）：`load_history` / `append_turn` / `clear_history` |
| `agent_service/mcp/lark_mcp.json` | 飞书凭证（app_id / app_secret / verification_token / encrypt_key）+ MCP 服务器配置 |
| `api/lark_agent.py` | 飞书蓝图：状态查询接口 + SocketIO `/lark` namespace |
| `api/socketio_instance.py` | 共享 SocketIO 实例（threading 模式，防循环导入） |

### WebSocket 状态推送

客户端连接 SocketIO namespace `/lark` 即可接收两个状态事件：

```js
const socket = io('/lark');
socket.on('lark_status',     data => { /* MCP 工具调用状态 { state, tools, count, error } */ });
socket.on('lark_bot_status', data => { /* 长连接机器人状态 { state, error } */ });
```

连接时立即推送当前状态（`on_connect`），此后每次状态变更主动推送。

### 飞书长连接机器人（LarkBot）

```
app 启动 → lark_bot.start()
    ↓ 后台 daemon 线程
    ↓ lark.Client.builder().app_id().app_secret().build()  ← 发消息用
    ↓ EventDispatcherHandler 注册 p2p_im_message_receive_v1
    ↓ lark.ws.Client.start()  ← 阻塞，SDK 自动重连
    ↓ 收到消息 → 开新线程
        ↓ 提取 open_id（sender）+ chat_id（message）
        ↓ content == "clear"?
            是 → _save_and_clear()  ← 归档 wiki + 清历史，回复确认后 return
        ↓ lark_history.load_history(open_id, chat_id)  ← 加载历史
        ↓ mcp_manager 就绪？
            是 → mcp_manager.run_agent_sync(text, chat_cfg, history)  ← ReAct Agent 可调飞书工具
            否 → build_qa_graph().stream(state)  ← 降级：纯 RAG 问答
        ↓ lark_history.append_turn(...)               ← 持久化本轮
        ↓ client.im.v1.message.reply()
```

**不需要公网域名**：SDK 主动连接飞书 WebSocket 端点，反向长连接。

### 飞书对话历史（LarkHistory）

文件存 `agent_service/lark_conversations/<open_id>__<chat_id>.json`：

```json
{
  "open_id":    "ou_xxx",
  "chat_id":    "oc_xxx",
  "updated_at": "2026-06-01T12:00:00+00:00",
  "messages":   [
    {"role": "user",      "content": "...", "ts": "..."},
    {"role": "assistant", "content": "...", "ts": "..."}
  ]
}
```

- **唯一标识**：`open_id`（飞书用户 ID）+ `chat_id`（会话 ID）；P2P 聊天天然一对一
- **滚动窗口**：只保留最近 `MAX_TURNS=10` 轮（20 条消息），超出自动截断旧消息
- **原子写**：tmp → replace，防文件半写损坏
- **读写失败不阻塞回复**：异常只打印警告，不中断消息处理流程

### 飞书 OAuth 用户授权

用户在飞书聊天中发送 `auth` 触发 OAuth 流程，获取个人 user_access_token：

```
用户发 "auth"
    ↓ lark_bot._send_auth_link()
    ↓ 读取 lark_mcp.json 中 oauth_redirect_uri + app_id
    ↓ lark_oauth.get_auth_url() 生成授权 URL
    ↓ bot 以文本消息发送链接给用户

用户点击链接 → 飞书授权页 → 同意
    ↓ 飞书回调 GET /lark/oauth/callback?code=xxx&state=<open_id>
    ↓ lark_oauth.exchange_code() 换取 user_access_token
    ↓ lark_token_store.save(open_id, token_data) 持久化
    ↓ 返回成功 HTML 页面（用户浏览器可见）

后续对话
    ↓ lark_token_store.get_valid_token(open_id) 取 token（自动续签）
    ↓ lark_user_tools.build_user_tools(token) 创建用户级工具
    ↓ mcp_manager.run_agent_sync(..., extra_tools=[...]) 合并注入
```

**相关文件**：

| 文件 | 职责 |
|---|---|
| `agent_service/mcp/lark_oauth.py` | OAuth URL 生成、授权码换 token、token 续签 |
| `agent_service/mcp/lark_token_store.py` | user_access_token 持久化（`lark_tokens/`），自动刷新 |
| `agent_service/mcp/lark_user_tools.py` | 用户级 LangChain 工具工厂（list_contacts / search_contacts） |
| `api/lark_agent.py` | `/lark/oauth/callback` 回调路由 |

**指令列表**：

| 指令 | 效果 |
|---|---|
| `auth` | 发送 OAuth 授权链接 |
| `deauth` | 清除本地 user token，撤销授权 |
| `clear` | 归档对话历史到 wiki 并清除 |

**配置**（`lark_mcp.json`）：
- `oauth_redirect_uri`：OAuth 回调地址，需在飞书开发者后台同步填写，且必须可公开访问。

**飞书后台需开通的权限**：`contact:user.base:readonly`（获取用户基本信息）

### 飞书 Tenant 通讯录工具（`lark_tenant_tools.py`）

不依赖 OAuth 用户授权，使用 app_access_token 直接调飞书 REST API，绕过 lark-mcp 对 contact 系列工具强制 user token 的限制。每次 `_query()` 调用时由 `lark_bot` 动态构建并注入到 `mcp_manager.run_agent_sync(extra_tools=...)`。

**提供工具**：`list_contacts` / `search_contacts` / `list_departments`

**关键实现细节**：

1. **`department_id_type` 必须用 `open_department_id`**
   - `contact/v3/scopes` 请求时加 `department_id_type=open_department_id`，返回的 dept_id 格式为 `od-xxx`
   - 调 `contact/v3/users` 时同样加 `department_id_type=open_department_id`
   - 不加或用默认的 `department_id` 类型会收到 99992357 错误（Invalid department_id）

2. **app_access_token 本地缓存**（`_token_cache`）
   - 2 小时有效期，提前 5 分钟刷新
   - 多线程安全（`threading.Lock`）

3. **查询降级策略**
   ```
   list_contacts(department_id="")
       ↓ 无 department_id → 调 contact/v3/scopes（open_department_id 类型）
       ↓ 有 dept_ids → 查根部门用户列表
       ↓ 无 dept_ids 但有 user_ids → batch 查用户详情
       ↓ 都没有 → 返回提示
   ```

4. **飞书管理后台必须配置**：工作台 → 应用管理 → 权限管理 → 通讯录授权 → 全员（否则只能查到管理员手动勾选的用户）

**System prompt 关键规则**（`mcp_manager._invoke_agent`）：
- 明确禁止 LLM 使用 `contact_v3_user_batchGetId` 查通讯录列表（该工具仅用于已知 ID 反查）
- 强制 LLM 优先使用 `list_contacts` / `search_contacts` / `list_departments`

### 飞书 `clear` 归档指令

用户在飞书聊天中发送 `clear`（大小写不限）时触发：

```
用户发 "clear"
    ↓ lark_history.load_history()  ← 读取当前会话历史
    ↓ 无历史 → 回复"📭 当前没有可保存的对话历史"，结束
    ↓ 拼接对话文本，构造 llm_input（rating=5，评语=飞书机器人手动归档）
    ↓ build_cleaning_graph().invoke({raw_text, _FEEDBACK_SYSTEM, cleaner_cfg})
    ↓ cleaned 非空 → 写 wiki/feedback_<uid>_lark_<ts>_5star.txt
    ↓ services.invalidate_rag()
    ↓ lark_history.clear_history()  ← 删除历史文件
    ↓ 回复归档结果
```

- 复用 web 端 `/feedback` 相同的清洗 prompt（`_FEEDBACK_SYSTEM`）和子图
- `clear_history` 无论 cleaned 是否为空都会执行（无价值对话也清除）
- 归档文件命名：`feedback_<open_id[:20]>_lark_<ts>_5star.txt`

### 约定

- `lark_bot.start()` 和 `mcp_manager.start()` 均在 `create_app()` 里调用，两者独立
- `lark_bot._query()` 和 `lark_history` 的 import 均做 lazy import，避免循环依赖
- `lark_mcp.json` 的顶层字段（`app_id` 等）供两个模块共用；`mcpServers` 仅供 `mcp_manager`
- `lark-oapi>=1.3.0` 已加入 `requirements.txt`

## 日志（`agent_service/logging_config.py`）

集中式控制台日志。`create_app()` **最先**调 `setup_logging()`（早于注册蓝图），配置根 logger 的 `StreamHandler`，统一格式 `时间 [级别] 模块名: 消息`。

- **级别来源**（优先级从高到低）：环境变量 `LOG_LEVEL` > `config.yaml` 顶层 `log_level` > 默认 `INFO`。排查问题时把 `log_level` 改 `DEBUG`（或 `LOG_LEVEL=DEBUG` 临时覆盖）。
- **三方库降噪**：`httpx`/`openai`/`chromadb` 等（`_NOISY_LIBS`）默认压到 WARNING，**仅 DEBUG 时**放开以便追踪外部 API；`pdfminer`（`_ALWAYS_QUIET_LIBS`，pdfplumber 后端）**恒压 WARNING**——它在 DEBUG 下每解析一个 PDF 会刷上千行，故即使根级别为 DEBUG 也不放开。
- **取 logger**：业务模块用 `from agent_service.logging_config import get_logger; log = get_logger(__name__)`，不要用 `print()`（`print` 不受 `log_level` 控制）。`setup_logging()` 幂等（按 handler 标记去重，双进程/热重载不会重复打印）。
- **已迁移到 logger 的核心路径**：`rag/simple_rag.py`、`api/services.py`、`graph/qa/nodes.py`（检索/嵌入/重排诊断 + RAG 重建/检索命中数 INFO/DEBUG 信号）。其余模块的历史 `print()` 仍直接进控制台、不受级别控制，可按需增量迁移。
- `log_level` 是 config.yaml 顶层非 RAG 字段，已加入 `RAGConfig._NON_RAG_KEYS` 白名单（不触发“未知字段”告警）。

## 启动顺序

1. `python -m api.app_user`（或 `api.app_admin`）
2. 对应 `create_app()` **先 `setup_logging()`**，再注册 6 个蓝图（+lark_agent），确保 3 个目录存在
3. `socketio.init_app(app)` → `mcp_manager.start()` → `lark_bot.start()` 后台线程启动
4. 第一次访问 `/agent/chat` 时 `services.get_rag()` 触发首次构建（前提：docs/ 或 wiki/ 有文件）
5. Fernet 密钥首次解密时按需生成
6. MySQL 连接在每次 `/auth/*` 请求时按需建立短连接（无连接池）
7. MCP 工具加载完成后 SocketIO 广播 `lark_status` 事件；长连接就绪后广播 `lark_bot_status`
