# Settings 与加密链路

## 四段配置

`config.yaml` 里的新四段（settings 抽屉写入）：

```yaml
chat:
  api_key: "enc:gAAAAAB..."     # Fernet 密文，带 enc: 前缀（当前部署为空，回退顶层 legacy api_key）
  base_url: "https://api.deepseek.com"
  model_name: "deepseek-v4-pro"
  rag_score_threshold: 0.3       # RAG 命中分数门限；由 services.load_rag_threshold() 读取，低于该值回退会话上下文作答

cleaner:
  api_key: ""                    # 空 = 继承 chat
  base_url: ""
  model_name: ""

reranker:
  api_key: "enc:..."             # 当前部署用 DashScope，已加密
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
  model_name: "gte-rerank-v2"

embedding:
  api_key: "enc:..."             # 当前部署用 DashScope 在线 embedding，已加密
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
  model_name: "text-embedding-v4"  # DashScope 在线模型（配合顶层 api_provider: openai）

storage:
  wiki_dir: ""                   # 留空使用默认 agent_service/wiki/；不加密，普通字符串
```

### 继承规则

- cleaner 任一字段为空 → 取 chat 同名字段
- reranker 任一字段为空 → 取 chat 的 `api_key` / `base_url`；`model_name` 默认 `gte-rerank-v2`
- embedding 任一字段为空 → 取 chat 的 `api_key` / `base_url`；`model_name` 默认 `BAAI/bge-large-zh-v1.5`
- **当前部署用的是 DashScope 在线 embedding**（`config.yaml` 顶层 `api_provider: openai` + `embedding` 段填了 DashScope 的 `api_key`/`base_url` + `model_name: text-embedding-v4`），**不是**本地 sentence-transformers。
- 若要切回本地 sentence-transformers：把顶层 `api_provider` 置为 `null`、`embedding.api_key`/`base_url` 留空、`model_name` 用本地模型名（如 `BAAI/bge-large-zh-v1.5`）。两种模式维度不同，切换后必须重建向量库（见 `common-pitfalls.md` #2）。

实现：`api/services.py:load_cleaner_settings()` / `load_reranker_settings()` / `load_embedding_settings()`。

## 旧字段兼容

```yaml
api_key: "sk-..."        # 旧字段；明文
api_base: "..."
embedder_name: "..."
```

- 这些字段仍被 `RAGConfig` 读取
- `chat.api_key` 为空时，`_legacy_api_key()` 会回退到这里
- `EmbedderFactory.create(cfg)` 直接读 `cfg.api_key` / `cfg.api_base` / `cfg.embedder_name`
- **新代码用 `services.cfg_with_embedding(cfg)`** 把新四段叠加到旧字段上，再传给 `EmbedderFactory`

## 加密链路

```
浏览器输入明文 sk-xxx
    ↓ POST /settings (HTTPS 建议)
api/settings.py: services.save_settings(payload)
    ↓
api/services.py: 每个段的 api_key 非空 → security.encrypt(plain)
    ↓
agent_service/security.py:
    - 首次调用 → 生成 Fernet key → 写 .secret_key (0o600)
    - encrypt(plain) → "enc:" + base64(Fernet token)
    ↓
yaml.safe_dump 写回 config.yaml
```

读取链路反向：`decrypt(value)` 识别 `enc:` 前缀，无前缀视为历史明文原样返回。

### `security.py` 关键函数

```python
encrypt(plaintext: str) -> str        # 空字符串原样返回；返回带 "enc:" 前缀
decrypt(value: Optional[str]) -> str  # 空 → ""；非 enc: → 视为明文；enc: → 解密
mask(plaintext: str) -> str           # "sk-******1234"，给前端显示
```

### Fernet key 生命周期

- 文件：`agent_service/.secret_key`
- 权限：尝试 `chmod 0o600`（Windows 可能失败，忽略）
- 已加入 `.gitignore`
- 删除 key 文件 = 所有已加密的 api_key 永久丢失，需要用户重新填写

### 降级：未装 cryptography

如果 `import cryptography` 失败：
- `encrypt(x)` 返回 `x`（明文）
- `decrypt(x)` 返回 `x`
- 打印一次 warning 到 stdout

`requirements.txt` 已经声明 `cryptography>=42.0.0`，正常环境下不会降级。

## /settings/test 连通测试

`api/settings.py:test_settings()` 串行测四段：

| 段 | 测试方法 |
|---|---|
| chat | `client.chat.completions.create(messages=[{user:"ping"}], max_tokens=5, temperature=0.0)` — 不带 `extra_body`，兼容所有厂商 |
| cleaner | 同 chat（用 cleaner 的 cfg） |
| embedding | `cfg_with_embedding` 覆盖 → `EmbedderFactory.create().embed_query("ping")` |
| reranker | `DashScopeReranker(...).rerank("ping", [{"text":"pong"}], 1)` |

每段返回 `{ok, error[:240], latency_ms}`。

前端 `web/assets/settings.js:testSettings()` 接收后把对应圆点变绿/红，底栏汇总"✓ 连通：A、B　✗ 失败：C"。

## 加新配置字段（recipe）

### 场景 1：加配置到现有四段（如 `chat.temperature`）

1. `agent_service/rag/simple_rag.py` 的 `RAGConfig` 不需要改（chat 是 `Optional[Dict]`，灵活）
2. `api/services.py:_DEFAULT_CHAT` 加默认值
3. `services.load_chat_settings()` 把字段读出来
4. 节点 / 路由消费时从返回的 dict 拿
5. 前端 `web/assets/settings.js:SECTIONS` 加新字段渲染（用 `buildField()`）
6. 前端 `fillForm()` / `saveSettings()` 不用改（自动按 `data-section/data-field` 取所有 input）

> 例外：`chat.rag_score_threshold` 虽写在 `chat:` 段下，但**不走 `load_chat_settings()`**，而是由专用读取器 `services.load_rag_threshold()` 直接读原始 yaml（缺省 0.3）。这类"挂在某段下、却由专用 reader 消费"的字段，加的时候照它的模式（写 reader + 消费处调 reader），别指望 `load_chat_settings()` 自动带出来。

### 场景 1.5：加一个"非 RAG 的顶层编排开关"（如 `agent_mode` / `enable_planning`）

这类开关写在 `config.yaml` 顶层，但**不属于 RAG 配置**，由 `api/services.py` 的专用读取器直接读原始 yaml 消费（`get_agent_mode()` / `get_plan_first()`，内部走 `_read_raw_yaml()`，**绕过 `RAGConfig`**，因此每请求热生效、无需重启）。

`RAGConfig.from_dict()` 会对任何不在 dataclass 字段里的 yaml key 打印「未知字段，将被忽略」警告。这类顶层开关**不应**加成 `RAGConfig` 字段（会污染 RAG 配置），而应登记进 `RAGConfig._NON_RAG_KEYS` 白名单，避免误导性警告：

```python
# agent_service/rag/simple_rag.py
_NON_RAG_KEYS = frozenset({"agent_mode", "enable_planning", "log_level"})   # 无类型注解 → 不会被当成 dataclass 字段
# from_dict 里：unknown = set(data) - known - cls._NON_RAG_KEYS
```

步骤：① `config.yaml` 顶层加 key；② `services.py` 写 `get_<flag>()` 读取器（env > config 顶层 > 默认）；③ 把 key 加进 `_NON_RAG_KEYS`；④ 消费处调 `get_<flag>()`。

> 排错：若控制台出现「config 中存在未知字段，将被忽略: ['agent_mode']」，**不代表该开关失效**——它仍被 `get_agent_mode()` 读到；该警告仅说明它没进 `_NON_RAG_KEYS` 白名单。

### 场景 2：新增一个段（不太常见）

1. `agent_service/config.yaml` 加段
2. `api/services.py`：
   - `_DEFAULT_<NEW>` 默认
   - `load_<new>_settings()` 函数
   - `get_settings_masked()` 加 `<new>` 段
   - `save_settings()` 的白名单加 `<new>`
3. `api/settings.py:update_settings()` 白名单加 `<new>`
4. 如要连通测试，`test_settings()` 加 `_test_<new>()`
5. 前端 `settings.js:SECTIONS` 加分组

## 安全约定

1. **永远不要在日志 / 错误信息里打印 api_key**
2. **永远不要把解密后的 api_key 通过 GET 接口回传给前端**（GET /settings 只返回 mask）
3. **修改 settings 后立刻失效缓存**：`services.save_settings()` 已经做了 `invalidate_rag() + invalidate_reranker()`；函数返回 `(masked_dict, embedding_changed: bool)` 二元组，调用方必须解包：
   ```python
   masked, embedding_changed = services.save_settings(payload)
   return jsonify({"ok": True, "settings": masked, "embedding_changed": embedding_changed})
   ```
4. **storage 段不加密**：`wiki_dir` 是普通路径字符串，直接写入 `config.yaml`，`save_settings()` 读到 `storage` 键时直接存，不走 Fernet。
5. **不要把 .secret_key 提交到 git**：`.gitignore` 已覆盖，但加新文件时再检查
