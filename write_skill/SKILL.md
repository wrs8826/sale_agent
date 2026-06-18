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
| `__init__.py` | 暴露绝对路径常量：`CONFIG_PATH` / `DOCS_DIR` / `WIKI_DIR` / `CHROMA_DIR` / `CONVERSATIONS_DIR` / `SKILLS_ROOT` / `DOWNLOADS_DIR` / `POLICY_STAGING_DIR` / `POLICY_DRAFTS_DIR` / `SKILL_BACKUPS_DIR` / `POLICY_SKILL_MAKER` |
| `skill_loader.py` | 解析 `skills/*/SKILL.md`；`detect_skill(query)` 关键词匹配；`all_refs_dirs()` 返回所有 references 目录 |
| `config.yaml` | 配置入口；分块/检索参数 + chat/cleaner/reranker/embedding 四段 API 配置 + source_weights |
| `security.py` | Fernet 加密 API key（密钥落 `.secret_key`） |
| `rag/simple_rag.py` | `DocumentChunker` / `EmbedderFactory` / `HybridRetriever` / `DashScopeReranker` / `RAGConfig` |
| `graph/state.py` | `CleaningState` + `ChatState` 两份 TypedDict |
| `graph/cleaning/` | 清洗子图：`route_input → read_file? → clean → END` |
| `graph/qa/` | QA 主图，`build_qa_graph(agent_mode)` 两形态：**single**（`call_tools→extract→retrieve?→generate`，单趟一次工具）/ **react**（`extract→retrieve?→plan→agent_react`，`create_react_agent` 多步自主工具循环，`max_tool_rounds` 网页端传 15/节点默认 5，`astream_events`→SSE）。由 `services.get_agent_mode()`（env `AGENT_MODE`>config 顶层 `agent_mode`>single）切换；网页端+飞书降级共用。`plan` 节点（先列方案再执行）按 `enable_planning`（`get_plan_first()`，env `PLAN_FIRST`>config>False）门控，仅 react；节点用 `get_stream_writer` 推 SSE |
| `mcp/lark_mcp.json` | 飞书凭证（顶层 `app_id/app_secret/...` + `oauth_redirect_uri` + `public_base_url`：对外基地址，飞书下载绝对链接用，缺省回退 oauth_redirect_uri 的 origin）+ MCP 服务器配置（`mcpServers`）。**注册两个 MCP server**：`lark-mcp`（官方 `@larksuiteoapi/lark-mcp`，纯飞书工具）+ `builtin-tools`（`python -m agent_service.mcp.builtin_mcp_server`，3 个核心工具） |
| `mcp/mcp_manager.py` | `MCPManager` 单例：后台 asyncio 线程，`MultiServerMCPClient(mcpServers)` 把**两个 server 的工具合并**成 `self._tools` 注入飞书 ReAct Agent；状态回调，同步桥接 |
| `mcp/lark_bot.py` | `LarkBot` 单例：`lark-oapi` SDK 长连接接收飞书消息；`_query()` 入口调 `detect_skill()` 注入 skill 提示词，两条路径（MCP Agent / RAG QA 图）均感知 skill |
| `mcp/lark_history.py` | 飞书机器人对话历史持久化：`load_history` / `append_turn` / `clear_history`；文件存 `agent_service/lark_conversations/` |
| `mcp/builtin_tools.py` | 内置 LangChain `@tool` 单一实现源 + 两套工具集：`BUILTIN_TOOLS`（核心，网页端+飞书 QA 降级共用：`get_current_time` / `load_policy_file` / `generate_word_document`）与 `WEB_TOOLS`（核心 + `read_document` / `list_documents` 文档读取，**仅网页端**）；`build_tool_table(tools)` 按集生成清单。`generate_word_document` 正文经 `_render_body` 渲染（识别 Markdown 表格→带边框 `Table Grid` 表、首行加粗，其余按段落；加粗/列表暂未解析）。`read_document` 读 `docs/` 整篇文本，依赖 `extract_text_from_file(path)`（`.pdf`→PyMuPDF / `.docx`→python-docx / 纯文本→UTF-8；该函数同时供 `/ingest` 用）。**飞书隔离**：靠 `ChatState.web_tools` 标志区分，飞书两条路径都不带，拿不到文档读取。**注意**：此文件非 MCP，仅文件名归类在 mcp/ 下 |
| `mcp/builtin_mcp_server.py` | 把内置工具暴露为 MCP（飞书路径），`FastMCP("builtin-tools")`。**已转发 3 个核心工具**：`get_current_time` / `load_policy_file` / `generate_word_document`（与 `BUILTIN_TOOLS` 一致），均注入飞书 ReAct Agent。文档读取工具（`read_document` / `list_documents`）**刻意未转发**，只走网页端 `WEB_TOOLS`。`generate_word_document` 转发层用 `_public_base_url()` 把相对 `/download/` 链接重写为绝对 URL（仅飞书路径，网页端仍相对）。`/download` 路由无 `login_required`，公开可访问，故绝对链接飞书用户可直接下载。新增工具若也要给飞书用才需在此加 `@mcp_server.tool()` 转发 |

### `skills/`（领域知识 skill，纯提示注入，无执行能力）

由 `skill_loader.py` 解析，`SKILL.md` = frontmatter(`name`/`description`/`triggers`) + body(L2 系统提示)；`references/*.md` 由 `load_policy_file` 工具按需读取。

| 路径 | 职责 |
|---|---|
| `甬江人才政策/` | 宁波市甬江人才工程（2026）与甬才通系统操作，references 分文档 |
| `太仓人才政策/` / `无锡人才政策/` / `成都人才政策/` | 各地人才政策顾问 |
| `软著专利与技术文档/` | 指导生成软著登记、发明/实用新型专利申请文件、软件说明书、设计文档；body 内置四类文档的标准结构骨架；导出 Word 由内置工具 `generate_word_document` 落盘 |
| `文档读取/` | 引导模型用 `list_documents` + `read_document`（仅网页端的 `WEB_TOOLS`）读取知识库中某个具体文件的整篇内容（PDF/.docx/纯文本），用于阅读/总结/提取/问答；纯提示注入，不含 references |

> **创建/更新政策 skill** 时（如上传了新政策材料），读开发态 meta-skill [`policy_skill_maker/SKILL.md`](../policy_skill_maker/SKILL.md)：固化了政策 skill 的结构、triggers 设计（避免跨地区串味）、文档地图与 references 拆分、验证步骤。注意它在项目根（**不在 `skills/`**），故不会被 `skill_loader` 注入 agent。

### `api/`

| 文件 | 路由 / 职责 |
|---|---|
| `app_user.py` | **用户端入口**（端口 5001）：注册 auth/agent/conversations/knowledge/settings 蓝图，提供 `/`（登录页）/ `/user` 路由，session 保护 |
| `app_admin.py` | **管理员端入口**（端口 5002）：同样注册所有蓝图，提供 `/`（管理员登录页）/ `/admin/*` 路由，严格 admin 角色保护 |
| `app.py` | 兼容旧命令的 shim，直接 import `app_user.app` |
| `auth.py` | **认证蓝图**（`/auth/*`）：`register` / `login` / `admin-login` / `logout` / `me`，MySQL users 表，Werkzeug 密码哈希 |
| `users.py` | **用户管理蓝图**（`/users/*`，仅 admin）：列表 / 修改信息 / 删除 / 封禁 |
| `conv_stats.py` | 对话统计 MySQL 模块：`conversation_stats` 表，`ensure_table()` / `get_compact_count` / `increment_compact_count`（+1）/ `reset_compact_count`（L4 熔断后清零）；计数全持久化，刷新/重启不丢 |
| `session_store.py` | Redis Session 配置：`configure_session(app, key_prefix)`，服务端 session + 滑动空闲超时（`SESSION_IDLE_MINUTES`），用户/管理端各用前缀隔离 |
| `services.py` | 单例（RAG / Reranker），settings 四段访问器 + `cfg_with_embedding(cfg)` + `get_wiki_dir()` + `get_agent_mode()`（react/single flag，env `AGENT_MODE`>config>single）+ `get_plan_first()`（先列方案 flag，env `PLAN_FIRST`>config `enable_planning`>False） |
| `agent.py` | `POST /agent/chat`（SSE，QA 图驱动）+ `POST /feedback`（清洗子图） |
| `knowledge.py` | `/files` CRUD、`POST /upload`（`ALLOWED_EXT`=.txt/.md/.rst/.html/.pdf/.docx）、`POST /ingest`（SSE，读取走 `extract_text_from_file`；PDF/.docx 二进制原件清洗后**不回写**以保留原文供 `read_document` 读取，纯文本仍回写）、`POST /query`、`POST /vectordb/clear`、`GET /download/<file>`（下载工具产物，防目录穿越） |
| `settings.py` | `GET/POST /settings` + `POST /settings/test`（四模型连通测试） |
| `conversations.py` | 会话 CRUD + `/conversations/<id>/compact` + `compact_conversation()`（含两级压缩）；**按用户子目录隔离**（`conversations/<user_id>/<uuid>.json`），所有路由校验归属，admin 可跨用户访问 |
| `policy_skill.py` | **政策 skill 更新蓝图（仅 admin 端注册 + admin 角色校验）**：`/admin/policy-staging`（暂存列/删）、`/admin/policy-skill/draft`（SSE，复用清洗子图 + `policy_skill_maker` body 生成结构化草稿）、`/draft/<id>`（审核）、`/publish`（人工确认后落 `skills/`，备份旧件 + `load_skills(force=True)` 热重载 + 删暂存源与草稿）、`/discard`。agent 只产草稿不碰 live skills/；与 `/agent/chat` 完全隔离 |
| `lark_agent.py` | 飞书蓝图：`GET /lark/status` + `POST /lark/chat` + SocketIO namespace `/lark` |
| `socketio_instance.py` | 共享 SocketIO 实例（threading 模式），防循环导入 |

### `web/`

| 文件 | 职责 |
|---|---|
| `login.html` | **用户端登录 + 注册**（tab 切换）；注册字段：用户名/密码/确认密码/手机号/部门 |
| `user.html` | 用户端：**三页侧栏布局**（上传资料 / 智能问答 / 系统设置），260px 固定左侧导航，含会话侧栏、自动压缩、反馈、四段模型设置 |
| `login.html` | **用户端登录 + 注册**（tab 切换）；注册字段：用户名/密码/确认密码/手机号/部门 |
| `user.html` | 用户端：三页侧栏布局（上传资料 / 智能问答 / 系统设置） |
| `assets/common.css` | 三页共享样式（header / panel / tabs / 设置抽屉 / 状态圆点） |
| `assets/settings.js` | 共享设置抽屉 + `/settings/test` 调用 |

### `web-admin/`（React 管理员端，端口 5173 开发 / build 后可由 nginx 静态托管）

| 路径 | 职责 |
|---|---|
| `src/pages/KnowledgePage.tsx` | 知识库管理：上传（含「普通资料 / 政策材料」类型切换，政策走 `kind=policy` 隔离暂存）、文件列表、RAG 参数、检索测试 |
| `src/pages/ChatPage.tsx` | Agent 对话：实时 SSE 流式对话、评分反馈、回形针上传 |
| `src/pages/PolicySkillPage.tsx` | **政策 Skill 更新**：暂存政策材料列表 → 「生成草稿」（SSE 调 `/admin/policy-skill/draft`）→ 审核弹窗（SKILL.md + references + 变更点）→ 发布/丢弃。隔离于正常对话 |
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
2. **SSE 事件类型保持稳定**：前端协议是 `tool_start` / `tool_end` / `plan_start` / `plan_token` / `plan_end`（先列方案，react+enable_planning）/ `token` / `download`（生成文件后由 api 从真实工具结果抽 `/download/...` 下发，前端渲染下载按钮，不靠模型转述）/ `done` / `error` / `status` / `conversation_saved` / `compact_done` / `auto_compacted` / `circuit_break`（L4 熔断）/ `result`（feedback 专用）/ `warning`。加新类型 OK，**不要改老类型语义**。注：`tool_turn` 是 call_tools_node/agent_react → api 的内部事件，api 层 `continue` 不下发前端。
3. **不绕过单例**：RAG 实例必须 `services.get_rag(...)` 拿，Reranker 必须 `services.get_reranker()`。直接 `HybridRetriever(...)` 会让缓存失效逻辑失灵。
4. **存储留在 api 层**：清洗子图 / QA 图都是纯函数路径；chroma 写入、文件落盘只能在 `api/` 内做。
5. **路径用绝对常量**：从不写 `Path("docs")`，永远 `DOCS_DIR / filename`，否则不同 CWD 启动会炸。
6. **改 embedding model 必清向量库**：维度不匹配会让查询直接挂；在前端 settings 抽屉里这条警告必须保留。
7. **保留 import 边界 shim**：`rag/__init__.py` 仍被 `services.py` import（Agent 编排已迁至 `graph/qa`，`rag/agent.py` 已不存在）；`graph/__init__.py` 暴露 `build_cleaning_graph` / `build_qa_graph` 是公共 API。
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
