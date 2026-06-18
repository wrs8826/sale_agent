# 销售 Agent

基于 Flask + LangGraph 的销售助手，支持 RAG 检索增强、对话历史管理、管理后台，以及飞书（Lark）机器人集成。

## 功能概览

- **RAG 问答**：混合检索（BM25 + 向量）+ 可选重排序，支持自定义知识库
- **流式输出**：Server-Sent Events（SSE）逐 token 推送（`tool_start / tool_end / plan_* / token / download / done / auto_compacted / circuit_break / conversation_saved` 等事件）
- **多步工具循环（ReAct）**：`agent_mode` feature flag 灰度切换——`single`（单趟一次工具）/ `react`（`create_react_agent` 多步 思考→调工具→观察，网页端最多 15 轮）；网页端与飞书降级共用
- **先列方案再执行**：`enable_planning` 开关（仅 react 生效）——执行前先产出一份执行方案（任务拆分），以独立「📋 执行方案」卡片流式展示，并作为执行指令注入循环；仅网页端
- **工具执行实时清单**：调用工具时前端实时渲染可折叠清单，未执行 `[ ]`、成功 `[✅]`、失败 `[❌]`，每个工具事件刷新一次；实时与历史、用户端与管理端统一同一套 UI
- **确定性文件下载**：生成 Word 后由后端从真实工具结果下发下载链接、前端渲染「下载」按钮，不依赖模型转述链接（避免模型把链接写错/编造）
- **内置工具**：读取文档（PDF / Word / 文本，`read_document`）、列文件、查政策原文、生成 Word（返回下载链接）、获取时间；网页端独有文档读取，飞书侧不暴露
- **文档读取与上传**：知识库支持 `.txt/.md/.rst/.html/.pdf/.docx`；对话框回形针可直接上传文件让助手读取
- **四级对话压缩**：L1 滑动窗口（最近 20 轮）+ L2 工具记录裁剪 + L3 滚动摘要（DeepSeek 分词器精确计数，1M 上下文预算）+ L4 熔断全局强压；工具调用持久化进历史
- **技能系统**：通过 `skills/` 目录挂载专项技能，关键词路由自动匹配；管理端可上传政策材料 →（开发态 `policy_skill_maker` 方法论）自动生成 skill 草稿 → 人工审核发布
- **管理后台**：用户管理、知识库管理、政策 Skill 更新、参数配置、对话统计（React + TypeScript）
- **飞书机器人**：WebSocket 长连接，无需公网域名；两个 MCP server（官方飞书工具 + 内置工具）
- **API Key 加密**：Fernet 对称加密存储，`enc:` 前缀标识密文，前端只看到掩码

## 架构

```
web/ web-admin/          ← 用户端（vanilla JS）/ 管理端（React SPA）
        ↓ HTTP / SSE
api/                     ← Flask 蓝图；双端口：5001（用户）/ 5002（管理员）
        ↓ Python 调用
agent_service/           ← 纯逻辑：RAG、LangGraph 图、加密、配置
skills/                  ← 技能包（SKILL.md + references/）
        ↓ TCP
MySQL sales_agent.users  ← 用户认证
```

两个独立 Flask 进程，Session 互不干扰：

| 服务 | 端口 | 说明 |
|------|------|------|
| `api/app_user.py` | 5001 | 用户聊天界面 |
| `api/app_admin.py` | 5002 | 管理后台（React SPA） |

## 快速开始

### 1. 环境要求

- Python 3.10+
- Node.js 18+（管理后台前端）
- MySQL 8.0+
- Redis（Session 存储）

### 2. 安装依赖

```bash
pip install -r agent_service/requirements.txt
pip install cryptography PyMySQL
```

### 3. 配置文件

复制模板并填写你的配置：

```bash
cp agent_service/config.yaml.example agent_service/config.yaml
cp agent_service/mcp/lark_mcp.json.example agent_service/mcp/lark_mcp.json
```

**`agent_service/config.yaml`** — 主配置（RAG 参数 + LLM API Key）：

```yaml
# 嵌入模型 API Key（阿里云百炼 / OpenAI 兼容）
api_key: "你的API Key"
api_base: "https://dashscope.aliyuncs.com/compatible-mode/v1"

chat:
  api_key: "你的API Key"
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
  model_name: "qwen-plus-2025-07-28"
```

**`agent_service/mcp/lark_mcp.json`** — 飞书机器人配置（可选）：

```json
{
  "app_id": "你的飞书App ID",
  "app_secret": "你的飞书App Secret"
}
```

### 4. 初始化数据库

```sql
CREATE DATABASE sales_agent CHARACTER SET utf8mb4;
-- 建表 SQL 见 api/users.py 注释
```

### 5. 启动服务

```bash
# 用户端（port 5001）
python -m api.app_user

# 管理后台 API（port 5002）
python -m api.app_admin

# 管理后台前端开发服务器（port 5173，代理 API → :5002）
cd web-admin && npm install && npm run dev
```

访问 `http://localhost:5001` 进入用户聊天界面，`http://localhost:5173` 进入管理后台。

## 目录结构

```
├── api/                    # Flask 蓝图（路由、IO、SSE）
│   ├── app_user.py         # 用户端入口（port 5001）
│   ├── app_admin.py        # 管理端入口（port 5002）
│   ├── agent.py            # /agent/chat（QA 图驱动，流式）
│   ├── conversations.py    # 会话持久化 + 四级压缩（窗口/工具裁剪/摘要/熔断）
│   ├── policy_skill.py     # 政策 Skill 更新流（仅管理端）
│   └── services.py         # RAG / 配置单例 / agent_mode flag
├── agent_service/          # 业务核心（不含 Flask / HTTP）
│   ├── graph/              # LangGraph 图
│   │   ├── qa/             # 主问答图（single / react 两形态，流式）
│   │   └── cleaning/       # 文档清洗子图（也用于压缩/草稿生成）
│   ├── mcp/                # 飞书机器人 & 内置工具
│   │   ├── lark_bot.py     # WebSocket 长连接机器人
│   │   ├── mcp_manager.py  # LangChain MCP 工具加载器（两个 server）
│   │   ├── builtin_tools.py        # 内置 @tool（read_document 等）+ 文本提取
│   │   └── builtin_mcp_server.py   # 把核心工具暴露为 MCP（飞书路径）
│   ├── token_counter.py    # DeepSeek 分词器精确 token 计数（压缩阈值用）
│   ├── config.yaml         # 运行时配置（gitignored）
│   ├── config.yaml.example # 配置模板
│   └── security.py         # API Key Fernet 加密
├── skills/                 # 运行时技能包（SKILL.md + references/）
├── policy_skill_maker/     # 开发态 meta-skill：政策 skill 设计方法论（不进 skills/）
├── token/                  # 官方 DeepSeek 分词器（tokenizer.json 等）
├── web/                    # 用户端静态文件（vanilla JS）
└── web-admin/              # 管理后台（React + TypeScript + Vite）
```

## 知识库管理

将文档（`.txt` / `.md` / `.rst` / `.html` / `.pdf` / `.docx`）放入 `agent_service/docs/` 目录，通过管理后台的「知识库」页面上传或触发重建索引，也可调用：

```
POST /knowledge/ingest
Content-Type: application/json
{"filename": "your_doc.txt"}
```

- PDF / Word 经 `extract_text_from_file`（PyMuPDF / python-docx）提取文本入库；二进制原件保留，供 `read_document` 工具读取整篇内容。
- 对话界面的回形针按钮可即时上传文件并让助手读取（复用 `/upload`）。
- 反馈沉淀的优质回答会自动写入 `agent_service/wiki/`，作为补充知识源（权重可在 `config.yaml` 的 `source_weights` 调节）。

### 政策 Skill 更新（管理端）

管理后台「政策 Skill」页：上传**政策材料**（隔离暂存，不进正常检索）→ Agent 按 `policy_skill_maker` 方法论解析生成 skill 草稿（自动匹配已有地区或新建）→ 人工审核 → 发布到 `skills/`（落盘前备份、热重载）。

## 飞书机器人

1. 在[飞书开放平台](https://open.feishu.cn)创建企业自建应用
2. 开启「机器人」能力，获取 `App ID` 和 `App Secret`
3. 填入 `agent_service/mcp/lark_mcp.json`
4. 启动服务后机器人自动通过 WebSocket 长连接在线，无需配置公网域名

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `USER_SECRET_KEY` | 用户端 Session 密钥（生产务必覆盖） | `user-app-secret-key-change-in-prod` |
| `ADMIN_SECRET_KEY` | 管理端 Session 密钥（生产务必覆盖） | `admin-app-secret-key-change-in-prod` |
| `AGENT_MODE` | 对话循环模式：`react`（多步工具循环）/ `single`（单趟） | `single` |
| `PLAN_FIRST` | 先列执行方案再执行（任务拆分，仅 react 生效）：`1`/`true`/`on` 开启 | `false` |
| `DB_HOST` | MySQL 地址 | `127.0.0.1` |
| `DB_PORT` | MySQL 端口 | `3306` |
| `DB_USER` | MySQL 用户名 | `root` |
| `DB_PASS` | MySQL 密码 | — |
| `DB_NAME` | 数据库名 | `sales_agent` |
| `REDIS_PASSWORD` | Redis 密码（Session 存储） | — |

> `AGENT_MODE` / `PLAN_FIRST` 也可写在 `config.yaml` 顶层（`agent_mode` / `enable_planning`）；环境变量优先。`PLAN_FIRST` 仅在 `AGENT_MODE=react` 时生效。生产前请覆盖两个 `*_SECRET_KEY`。

## 技术栈

- **后端**：Flask、LangGraph、LangChain、ChromaDB、rank-bm25、sentence-transformers
- **前端**：vanilla JS（用户端）、React + TypeScript + Tailwind CSS + Vite（管理端）
- **LLM**：兼容 OpenAI 接口（默认阿里云百炼 Dashscope）
- **飞书集成**：lark-oapi SDK WebSocket 长连接

## License

MIT
