# 架构详解

## 三层分工

```
┌────────────────────────────────────────────────────────────┐
│  web/  (浏览器)                                            │
│  ── vanilla JS、SSE 消费、settings 抽屉                    │
│  ── 用户端：login.html / user.html                         │
│  ── 管理员端：admin/login.html / admin/knowledge / chat    │
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
  / → login.html          / → admin/login.html
  /user → user.html       /admin/* → knowledge/chat
      |                        |
 /auth/login              /auth/admin-login
 （接受 user + admin）    （仅接受 admin）
      |                        |
      └──────── 共享蓝图 ───────┘
       auth / agent / conversations
       knowledge / settings
```

**session 隔离原理**：两端使用不同的 `secret_key`（`USER_SECRET_KEY` / `ADMIN_SECRET_KEY`），cookie 名相同但签名不兼容，即使用同一浏览器访问两端，session 也不互通。

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

### QA 主图（`graph/qa/`）

```
START → extract_keywords ──after_extract──┬─► retrieve → generate → END
                                          └─► generate → END (rag_fn=None)
```

- 输入：`ChatState{query, history, chat_cfg, rag_fn, top_k}`
- 节点用 `langgraph.config.get_stream_writer()` 推 SSE 事件
- 外层用 `graph.stream(state, stream_mode="custom")` 直接转发为 SSE 流

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
api    build_qa_graph().stream(state, stream_mode="custom")
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

## 单例与缓存（`api/services.py`）

| 单例 | 缓存 key | 失效时机 |
|---|---|---|
| `_rag` | `(docs 文件名 frozenset, wiki 文件名 frozenset, chunk_size, chunk_overlap, separators)` | 文件增删 / 分块参数改 / 显式 `invalidate_rag()` |
| `_reranker` | `(api_key, model_name)` | settings 改 / 显式 `invalidate_reranker()` |
| `Fernet` 实例 | 全局 lazy 单例 | 重启 |

settings 写回（POST /settings）后两个缓存都会被强制失效。

## 配置（`config.yaml`）

```yaml
# 旧字段（向后兼容；embedder 在 chat fallback 链中末尾）
api_key: "sk-..."             # 明文，旧版用；新版优先 chat.api_key
api_base: "https://..."
embedder_name: "text-embedding-v4"

# 新四段（settings 抽屉写入；api_key 加密）
chat:     { api_key: "enc:...", base_url: "...", model_name: "qwen3-max" }
cleaner:  { api_key: "",        base_url: "",    model_name: "" }    # 空=继承 chat
reranker: { api_key: "",        base_url: "",    model_name: "gte-rerank-v2" }
embedding:{ api_key: "",        base_url: "",    model_name: "text-embedding-v4" }

# 存储配置（settings 抽屉 Storage 段写入）
storage:
  wiki_dir: ""                # 留空使用默认 agent_service/wiki/；支持绝对/相对路径

# 数据源权重（检索后置加权）
source_weights:
  docs: 1.0
  wiki: 0.7
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

### 约定

- `lark_bot.start()` 和 `mcp_manager.start()` 均在 `create_app()` 里调用，两者独立
- `lark_bot._query()` 和 `lark_history` 的 import 均做 lazy import，避免循环依赖
- `lark_mcp.json` 的顶层字段（`app_id` 等）供两个模块共用；`mcpServers` 仅供 `mcp_manager`
- `lark-oapi>=1.3.0` 已加入 `requirements.txt`

## 启动顺序

1. `python -m api.app_user`（或 `api.app_admin`）
2. 对应 `create_app()` 注册 6 个蓝图（+lark_agent），确保 3 个目录存在
3. `socketio.init_app(app)` → `mcp_manager.start()` → `lark_bot.start()` 后台线程启动
4. 第一次访问 `/agent/chat` 时 `services.get_rag()` 触发首次构建（前提：docs/ 或 wiki/ 有文件）
5. Fernet 密钥首次解密时按需生成
6. MySQL 连接在每次 `/auth/*` 请求时按需建立短连接（无连接池）
7. MCP 工具加载完成后 SocketIO 广播 `lark_status` 事件；长连接就绪后广播 `lark_bot_status`
