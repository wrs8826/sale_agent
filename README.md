# 销售 Agent

基于 Flask + LangGraph 构建的 AI 销售助手，支持知识库问答（RAG）、多轮对话、飞书机器人集成与飞书 MCP 工具调用。

## 功能概览

- **知识库问答**：上传销售资料，混合检索（BM25 + 向量）+ 可选重排序，自动根据命中质量决定是否降级到上下文回答
- **多轮对话**：会话持久化，支持两级历史压缩（手动 `/compact` + 超限自动压缩）
- **飞书机器人**：WebSocket 长连接，无需公网域名，收到消息后优先调用飞书 MCP 工具，降级时走 RAG 问答
- **飞书 MCP 工具**：通过 `@larksuiteoapi/lark-mcp` 接入飞书日历、文档、IM 等能力，LLM 自主决策调用
- **用户管理**：MySQL 用户表，支持注册/封禁/删除，每用户可独立配置模型参数
- **API Key 加密**：Fernet 对称加密，密文以 `enc:` 前缀存入 `config.yaml`
- **双端架构**：用户端（5001）与管理员端（5002）独立进程，session 互不干扰

## 目录结构

```
销售agent/
├── agent_service/          # 业务核心（纯逻辑，不含 Flask/MySQL）
│   ├── config.yaml         # 配置主文件
│   ├── security.py         # Fernet 加密
│   ├── rag/                # HybridRetriever / EmbedderFactory / Reranker
│   ├── graph/
│   │   ├── cleaning/       # 清洗子图（文档清洗 / 反馈摘要）
│   │   └── qa/             # QA 主图（关键词提取 → 检索 → 生成）
│   ├── mcp/
│   │   ├── lark_mcp.json   # 飞书凭证 + MCP 服务器配置
│   │   ├── mcp_manager.py  # MCP 工具加载与 ReAct Agent 调用
│   │   ├── lark_bot.py     # 飞书长连接机器人
│   │   └── lark_history.py # 飞书对话历史持久化
│   ├── docs/               # 知识库文档目录（.txt / .md / .html）
│   └── conversations/      # Web 端会话 JSON 存储
├── api/                    # Flask 蓝图（IO 边界、SSE 包装、单例缓存）
│   ├── app_user.py         # 用户端入口（5001）
│   ├── app_admin.py        # 管理员端入口（5002）
│   ├── services.py         # RAG / Reranker 单例 + 四段配置访问器
│   ├── agent.py            # /agent/chat SSE + /feedback
│   ├── knowledge.py        # 文件上传 / 向量化 / 检索调试
│   ├── settings.py         # 配置读写 + 连通测试
│   ├── conversations.py    # 会话 CRUD + 压缩
│   ├── auth.py             # 注册 / 登录 / 登出
│   └── users.py            # 用户管理（仅 admin）
├── web/                    # 用户端静态前端（vanilla JS）
│   ├── login.html
│   └── user.html
├── web-admin/              # 管理员端前端（React + TypeScript + Tailwind + Vite）
│   └── src/pages/          # KnowledgePage / ChatPage / UsersPage / SettingsPage
└── write_skill/            # 项目开发文档
    └── references/         # 架构 / API 协议 / 图模式 / 前端模式等
```

## 快速启动

### 1. 克隆后初始化配置文件

```bash
# 配置文件不含任何 Key，需从示例模板复制后填写
cp agent_service/config.yaml.example        agent_service/config.yaml
cp agent_service/mcp/lark_mcp.json.example  agent_service/mcp/lark_mcp.json
```

### 2. 安装依赖

```bash
pip install -r agent_service/requirements.txt
pip install cryptography PyMySQL
```

Node.js（飞书 MCP 工具需要 `npx`）：

```bash
node -v   # 需要 >= 18
```

### 3. 配置数据库

MySQL 中创建数据库并建表：

```sql
CREATE DATABASE sales_agent CHARACTER SET utf8mb4;
USE sales_agent;

CREATE TABLE users (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  username      VARCHAR(64)          NOT NULL UNIQUE,
  password_hash VARCHAR(256)         NOT NULL,
  role          ENUM('admin','user') NOT NULL DEFAULT 'user',
  phone         VARCHAR(20)          NOT NULL DEFAULT '',
  department    VARCHAR(64)          NOT NULL DEFAULT '',
  is_banned     TINYINT(1)           NOT NULL DEFAULT 0,
  user_settings TEXT,
  created_at    DATETIME             NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 创建默认管理员账号（密码：admin123）
INSERT INTO users (username, password_hash, role)
VALUES ('admin', 'pbkdf2:sha256:...', 'admin');
```

> 默认连接参数：`127.0.0.1:3306`，用户名 `root`，密码 `abc123`，数据库 `sales_agent`。  
> 可通过环境变量覆盖：`DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASS` / `DB_NAME`。

### 4. 配置模型 API Key

启动后访问管理员端 `http://localhost:5002`，在「系统设置」中填写：

| 配置段 | 说明 |
|---|---|
| Chat | 对话生成模型（默认 `qwen3-max`） |
| Cleaner | 文档清洗模型（默认继承 Chat） |
| Reranker | 重排序模型（默认 `gte-rerank-v2`） |
| Embedding | 向量化模型（默认 `text-embedding-v4`） |

API Key 会自动加密存储，页面只显示掩码。

### 5. 配置飞书（可选）

编辑 `agent_service/mcp/lark_mcp.json`：

```json
{
  "app_id":     "你的飞书 App ID",
  "app_secret": "你的飞书 App Secret",
  "verification_token": "",
  "encrypt_key": "",
  "mcpServers": {
    "lark-mcp": {
      "transport": "stdio",
      "command": "npx",
      "args": ["-y", "@larksuiteoapi/lark-mcp", "mcp",
               "-a", "你的飞书 App ID",
               "-s", "你的飞书 App Secret"]
    }
  }
}
```

飞书应用需开启**长连接接收消息**权限，无需配置公网回调地址。

### 6. 启动服务

```bash
# 用户端（端口 5001）
python -m api.app_user

# 管理员端（端口 5002）
python -m api.app_admin
```

管理员前端开发模式（热重载，端口 5173，自动代理 API 到 5002）：

```bash
cd web-admin
npm install
npm run dev
```

## 访问地址

| 端 | 地址 | 默认账号 |
|---|---|---|
| 用户端 | http://localhost:5001 | 注册后使用 |
| 管理员端 | http://localhost:5002 | admin / admin123 |
| 管理员前端（开发） | http://localhost:5173 | admin / admin123 |

## 主要配置项（`agent_service/config.yaml`）

```yaml
chunk_size: 400          # 文档分块大小
chunk_overlap: 100       # 分块重叠
top_k: 5                 # 检索返回片段数
bm25_weight: 0.5         # BM25 与向量检索融合权重

chat:
  model_name: qwen3-max
  base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
  rag_score_threshold: 0.3   # 低于此分时忽略知识库，改用对话上下文回答

source_weights:
  docs: 1.0    # 知识库文档权重
  wiki: 0.7    # 反馈沉淀文档权重
```

## 知识库管理

1. 在管理员端「知识库」页上传 `.txt` / `.md` / `.html` 文件
2. 上传完成后点击「向量化」触发 RAG 索引构建（SSE 实时进度）
3. 切换 Embedding 模型后需清空并重建向量库（页面有警告提示）

## 对话反馈与 Wiki 沉淀

用户对每轮回答打分（1-5 星）并填写评语后，清洗模型自动将有价值的内容写入 `agent_service/wiki/` 目录，并在下次检索时以 0.7 权重参与召回，形成知识闭环。

## 飞书机器人工作流

```
用户在飞书发消息
    ↓ WebSocket 长连接（lark_bot）
    ↓ 加载历史对话（lark_history）
    ├─ MCP 工具已就绪 → ReAct Agent（可调飞书日历/文档/IM 等工具）
    └─ MCP 未就绪    → RAG QA 图（知识库问答）
    ↓ 持久化本轮对话
    ↓ 回复消息
```

## 开发文档

详细架构与开发规范见 [`write_skill/references/`](write_skill/references/)：

| 文件 | 内容 |
|---|---|
| `architecture.md` | 完整架构与启动顺序 |
| `api-protocols.md` | 所有路由与 SSE 事件协议 |
| `graph-patterns.md` | LangGraph 节点与子图模式 |
| `conversation-storage.md` | 会话持久化与压缩算法 |
| `settings-encryption.md` | 四段配置与加密链路 |
| `frontend-patterns.md` | SSE 消费模板与前端约定 |
| `common-pitfalls.md` | 已知问题与修复方案 |
| `adding-a-feature.md` | 端到端功能新增 recipe |
