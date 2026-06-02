---
name: sales-agent-dev
description: Use when adding features, fixing bugs, or making changes to the 销售 Agent project at F:\销售agent\. This project is a Flask + LangGraph + vanilla JS sales assistant with knowledge base (RAG), conversation persistence, two-level history compaction, encrypted API key storage, and MySQL-backed user auth. Trigger when user asks to add an endpoint, modify an SSE event, touch a langgraph node, add a config field, change the user/admin UI, or debug existing behavior. Skip for unrelated projects.
---

# 销售 Agent — 项目开发指南

本 skill 让你在不重新摸索整个代码库的前提下，对销售 Agent 项目做**精准的功能增删 / 缺陷修复**。

## 何时使用

- 用户要求 **加新接口 / 新页面 / 新功能**
- 用户报告 **bug**，并明确指向本项目
- 用户要求 **调整配置 / SSE 协议 / 数据库 schema**
- 用户要求 **重构** 某个模块

不该用：通用编程问题、其他项目、与本项目无关的咨询。

## 三段式速览

```
F:/销售agent/
├── agent_service/        ← 业务核心（纯逻辑，不含 web）
├── api/                  ← Flask 蓝图（IO 边界、SSE 包装、单例缓存）
├── web/                  ← 旧静态前端（vanilla JS，用户端保留）
├── web-admin/            ← 新管理员前端（React + TypeScript + Tailwind CSS + Vite）
└── write_skill/          ← 本 skill
```

## 模块地图（一行一个）

### `agent_service/`

| 路径 | 职责 |
|---|---|
| `__init__.py` | 暴露绝对路径常量：`CONFIG_PATH` / `DOCS_DIR` / `WIKI_DIR`（默认值） / `CHROMA_DIR` / `CONVERSATIONS_DIR` |
| `config.yaml` | 配置入口；分块/检索参数 + chat/cleaner/reranker/embedding 四段 API 配置 + source_weights |
| `security.py` | Fernet 加密 API key（密钥落 `.secret_key`） |
| `rag/simple_rag.py` | `DocumentChunker` / `EmbedderFactory` / `HybridRetriever` / `DashScopeReranker` / `RAGConfig` |
| `graph/state.py` | `CleaningState` + `ChatState` 两份 TypedDict |
| `graph/cleaning/` | 清洗子图：`route_input → read_file? → clean → END` |
| `graph/qa/` | QA 主图：`extract_keywords → retrieve? → generate → END`，节点用 `get_stream_writer` 推 SSE |
| `mcp/lark_mcp.json` | 飞书凭证（顶层 `app_id/app_secret/verification_token/encrypt_key`）+ MCP 服务器配置（`mcpServers`） |
| `mcp/mcp_manager.py` | `MCPManager` 单例：后台 asyncio 线程持久持有飞书 MCP 上下文，状态回调，同步桥接 |
| `mcp/lark_bot.py` | `LarkBot` 单例：`lark-oapi` SDK 长连接接收飞书消息，调 QA 图生成回复，通过 SDK 发回飞书；无需公网域名 |
| `mcp/lark_history.py` | 飞书机器人对话历史持久化：`load_history` / `append_turn` / `clear_history`；文件存 `agent_service/lark_conversations/` |

### `api/`

| 文件 | 路由 / 职责 |
|---|---|
| `app_user.py` | **用户端入口**（端口 5001）：注册 auth/agent/conversations/knowledge/settings 蓝图，提供 `/`（登录页）/ `/user` 路由，session 保护 |
| `app_admin.py` | **管理员端入口**（端口 5002）：同样注册所有蓝图，提供 `/`（管理员登录页）/ `/admin/*` 路由，严格 admin 角色保护 |
| `app.py` | 兼容旧命令的 shim，直接 import `app_user.app` |
| `auth.py` | **认证蓝图**（`/auth/*`）：`register` / `login` / `admin-login` / `logout` / `me`，MySQL users 表，Werkzeug 密码哈希 |
| `users.py` | **用户管理蓝图**（`/users/*`，仅 admin）：列表 / 修改信息 / 删除 / 封禁 |
| `services.py` | 单例（RAG / Reranker），settings 四段访问器 + `cfg_with_embedding(cfg)` + `get_wiki_dir()`（读 config.yaml storage.wiki_dir） |
| `agent.py` | `POST /agent/chat`（SSE，QA 图驱动）+ `POST /feedback`（清洗子图） |
| `knowledge.py` | `/files` CRUD、`POST /upload`、`POST /ingest`（SSE）、`POST /query`、`POST /vectordb/clear` |
| `settings.py` | `GET/POST /settings` + `POST /settings/test`（四模型连通测试） |
| `conversations.py` | 会话 CRUD + `/conversations/<id>/compact` + `compact_conversation()`（含两级压缩）；**按用户子目录隔离**（`conversations/<user_id>/<uuid>.json`），所有路由校验归属，admin 可跨用户访问 |
| `lark_agent.py` | 飞书蓝图：`GET /lark/status` + `POST /lark/chat` + SocketIO namespace `/lark` |
| `socketio_instance.py` | 共享 SocketIO 实例（threading 模式），防循环导入 |

### `web/`

| 文件 | 职责 |
|---|---|
| `login.html` | **用户端登录 + 注册**（tab 切换）；注册字段：用户名/密码/确认密码/手机号/部门 |
| `user.html` | 用户端：**三页侧栏布局**（上传资料 / 智能问答 / 系统设置），260px 固定左侧导航，含会话侧栏、自动压缩、反馈、四段模型设置 |
| `admin/login.html` | **管理员专属登录页**（调用 `/auth/admin-login`，只允许 admin 角色） |
| `admin/knowledge.html` | 知识库管理 + 文档上传 + RAG 检索调试 |
| `admin/chat.html` | 完整功能 chat（含 top_k 滑块） |
| `admin/users.html` | 用户管理：列表 / 搜索 / 编辑 / 封禁 / 删除 |
| `assets/common.css` | 三页共享样式（header / panel / tabs / 设置抽屉 / 状态圆点） |
| `assets/settings.js` | 共享设置抽屉 + `/settings/test` 调用 |

### `web-admin/`（React 管理员端，端口 5173 开发 / build 后可由 nginx 静态托管）

| 路径 | 职责 |
|---|---|
| `src/pages/KnowledgePage.tsx` | 知识库管理：上传、文件列表、RAG 参数、检索测试 |
| `src/pages/ChatPage.tsx` | Agent 对话：实时 SSE 流式对话、评分反馈 |
| `src/pages/UsersPage.tsx` | 用户管理：表格 CRUD、添加/编辑/封禁/删除模态框 |
| `src/pages/SettingsPage.tsx` | 系统设置：5 张配置卡片（对话/清洗/Embedding/重排序/Wiki） |
| `src/context/AppContext.tsx` | 全局状态（当前页面、用户列表、文件列表、Toast） |
| `src/components/Sidebar.tsx` | 左侧固定导航（260px） |
| `src/components/Toast.tsx` | Toast 通知容器 |
| `vite.config.ts` | 开发代理：所有 API 路径转发到 Flask 5002 端口 |

**启动开发服务器**：
```bash
cd web-admin
npm install
npm run dev   # http://localhost:5173
```

**约定**：
- 所有 API 调用走相对路径（`/settings`、`/users` 等），Vite 代理转发到 Flask 5002
- 新增页面在 `src/pages/` 下创建，在 `App.tsx` switch 里加分支，并在 `Sidebar.tsx` 添加导航项
- 图标统一用 `lucide-react`，不引入其他图标库

## 数据库（MySQL）

数据库名：`sales_agent`

```sql
CREATE TABLE users (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  username      VARCHAR(64)          NOT NULL UNIQUE,
  password_hash VARCHAR(256)         NOT NULL,
  role          ENUM('admin','user') NOT NULL DEFAULT 'user',
  phone         VARCHAR(20)          NOT NULL DEFAULT '',
  department    VARCHAR(64)          NOT NULL DEFAULT '',
  is_banned     TINYINT(1)           NOT NULL DEFAULT 0,
  user_settings TEXT,                          -- JSON，存四段专属模型配置，api_key 加密
  created_at    DATETIME             NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

`user_settings` 格式与 `config.yaml` 四段一致（chat / cleaner / reranker / embedding），api_key 以 Fernet 加密（`enc:` 前缀）。空字段表示继承系统全局设置。

连接配置（优先环境变量，兜底硬编码）：

| 环境变量 | 默认值 |
|---|---|
| `DB_HOST` | `127.0.0.1` |
| `DB_PORT` | `3306` |
| `DB_USER` | `root` |
| `DB_PASS` | `abc123` |
| `DB_NAME` | `sales_agent` |

默认内置账号：`admin / admin123`（role=admin）

## 按任务路由到详细文档

| 任务 | 必读 |
|---|---|
| 加 API 路由 / 改 SSE 事件协议 | `references/api-protocols.md` |
| 加节点 / 改 langgraph 图 | `references/graph-patterns.md` |
| 加配置字段 / 改 API key 加密链路 | `references/settings-encryption.md` |
| 加前端页面 / 改抽屉 / 改 SSE 消费者 | `references/frontend-patterns.md` |
| 会话持久化 / 压缩相关 | `references/conversation-storage.md` |
| 不确定从哪下手时通读架构 | `references/architecture.md` |
| 加新功能（端到端 recipe） | `references/adding-a-feature.md` |
| Bug 排查前先翻翻 | `references/common-pitfalls.md` |

## 不可违反的项目约定

1. **API key 永远加密**：经手 `services.encrypt/decrypt`，绝不明文落 `config.yaml`。从 settings 拿值只用 `services.load_chat_settings()` / `load_cleaner_settings()` / `load_reranker_settings()` / `load_embedding_settings()`。
2. **SSE 事件类型保持稳定**：前端协议是 `tool_start` / `tool_end` / `token` / `done` / `error` / `status` / `conversation_saved` / `compact_done` / `auto_compacted` / `result`（feedback 专用）/ `warning`。加新类型 OK，**不要改老类型语义**。
3. **不绕过单例**：RAG 实例必须 `services.get_rag(...)` 拿，Reranker 必须 `services.get_reranker()`。直接 `HybridRetriever(...)` 会让缓存失效逻辑失灵。
4. **存储留在 api 层**：清洗子图 / QA 图都是纯函数路径；chroma 写入、文件落盘只能在 `api/` 内做。
5. **路径用绝对常量**：从不写 `Path("docs")`，永远 `DOCS_DIR / filename`，否则不同 CWD 启动会炸。
6. **改 embedding model 必清向量库**：维度不匹配会让查询直接挂；在前端 settings 抽屉里这条警告必须保留。
7. **不删 `rag/agent.py` 之外的 shim**：`rag/__init__.py` 仍被 `services.py` import；`graph/__init__.py` 暴露 `build_cleaning_graph` / `build_qa_graph` 是公共 API。
8. **前端 fetch 永远走相对路径**（`/conversations`、`/settings`），后端反代切走不会断。
9. **两端 secret_key 不同**：`app_user.py` 用 `USER_SECRET_KEY`，`app_admin.py` 用 `ADMIN_SECRET_KEY`，session cookie 天然隔离，不要合并。
10. **注册只能创建 user 角色**：`/auth/register` 硬编码 `role='user'`，admin 账号只能由数据库直接写入。

## 工作流模板

修复 bug：
1. 先 read `references/common-pitfalls.md` 看是不是已知问题
2. 用 Grep / Glob 找到现场（**别用 Bash 的 find / grep**）
3. 改之前先 read 完整文件，确认上下文
4. 改完直接交付，不要自创合规检查

加功能：
1. read `references/adding-a-feature.md` 拿 recipe
2. 按"后端蓝图 → services 单例 → SSE 适配 → 前端 fetch → CSS"顺序推进
3. 改完简要列出端到端清单给用户

## 启动信息

```bash
# 用户端（端口 5001）
python -m api.app_user

# 管理员端（端口 5002）
python -m api.app_admin

# 兼容旧命令（等价于 app_user，端口 5001）
python -m api.app

# 依赖
pip install -r agent_service/requirements.txt
pip install cryptography PyMySQL   # API key 加密 + MySQL 驱动

# 关键文件位置
agent_service/.secret_key          # Fernet 密钥（首次运行自动生成，已加 .gitignore）
agent_service/config.yaml          # 配置主文件
agent_service/conversations/*.json # 会话持久化
agent_service/chroma_persist/      # 向量库
```
