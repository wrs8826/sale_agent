# 销售 Agent

基于 Flask + LangGraph 的销售助手，支持 RAG 检索增强、对话历史管理、管理后台，以及飞书（Lark）机器人集成。

## 功能概览

- **RAG 问答**：混合检索（BM25 + 向量）+ 可选重排序，支持自定义知识库
- **流式输出**：Server-Sent Events（SSE）逐 token 推送，支持 `tool_start / tool_end / token / done` 事件
- **对话历史**：自动分级压缩（L1 手动 / L2 自动），支持摘要式上下文窗口管理
- **技能系统**：通过 `skills/` 目录挂载专项技能，关键词路由自动匹配
- **管理后台**：用户管理、知识库管理、参数配置、对话统计（React + TypeScript）
- **飞书机器人**：WebSocket 长连接，无需公网域名，接收私聊消息后调用 QA 图回复
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
│   └── services.py         # RAG / 配置单例
├── agent_service/          # 业务核心（不含 Flask / HTTP）
│   ├── graph/              # LangGraph 图
│   │   ├── qa/             # 主问答图（流式）
│   │   └── cleaning/       # 文档清洗子图
│   ├── mcp/                # 飞书机器人 & MCP 工具
│   │   ├── lark_bot.py     # WebSocket 长连接机器人
│   │   └── mcp_manager.py  # LangChain MCP 工具加载器
│   ├── config.yaml         # 运行时配置（gitignored）
│   ├── config.yaml.example # 配置模板
│   └── security.py         # API Key Fernet 加密
├── skills/                 # 技能包目录
│   └── <技能名>/
│       ├── SKILL.md        # frontmatter(name/desc) + 系统提示
│       └── references/     # 技能专属知识文档
├── web/                    # 用户端静态文件（vanilla JS）
└── web-admin/              # 管理后台（React + TypeScript + Vite）
```

## 知识库管理

将文档（`.txt` / `.md` / `.html`）放入 `agent_service/docs/` 目录，通过管理后台的「知识库」页面上传或触发重建索引，也可调用：

```
POST /knowledge/ingest
Content-Type: application/json
{"filename": "your_doc.txt"}
```

反馈沉淀的优质回答会自动写入 `agent_service/wiki/`，作为补充知识源（权重可在 `config.yaml` 的 `source_weights` 调节）。

## 飞书机器人

1. 在[飞书开放平台](https://open.feishu.cn)创建企业自建应用
2. 开启「机器人」能力，获取 `App ID` 和 `App Secret`
3. 填入 `agent_service/mcp/lark_mcp.json`
4. 启动服务后机器人自动通过 WebSocket 长连接在线，无需配置公网域名

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `USER_SECRET_KEY` | 用户端 Session 密钥 | `user-app-secret-key-change-in-prod` |
| `ADMIN_SECRET_KEY` | 管理端 Session 密钥 | `admin-app-secret-key-change-in-prod` |
| `MYSQL_HOST` | MySQL 地址 | `127.0.0.1` |
| `MYSQL_PORT` | MySQL 端口 | `3306` |
| `MYSQL_USER` | MySQL 用户名 | `root` |
| `MYSQL_PASSWORD` | MySQL 密码 | — |
| `MYSQL_DB` | 数据库名 | `sales_agent` |
| `REDIS_URL` | Redis 地址 | `redis://localhost:6379/0` |

## 技术栈

- **后端**：Flask、LangGraph、LangChain、ChromaDB、rank-bm25、sentence-transformers
- **前端**：vanilla JS（用户端）、React + TypeScript + Tailwind CSS + Vite（管理端）
- **LLM**：兼容 OpenAI 接口（默认阿里云百炼 Dashscope）
- **飞书集成**：lark-oapi SDK WebSocket 长连接

## License

MIT
