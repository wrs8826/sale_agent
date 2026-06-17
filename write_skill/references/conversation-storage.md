# 会话持久化 + 四级压缩（Phase 0 工具持久化 + L1/L2 发送态裁剪 + L3 滚动摘要 + L4 熔断）

## 文件布局（用户隔离）

```
agent_service/conversations/
├── <user_id>/               ← 每个用户一个子目录（user_id = MySQL users.id）
│   ├── <uuid_hex>.json
│   └── <uuid_hex>.json
├── <user_id>/
│   └── ...
└── <uuid_hex>.json          ← 历史遗留文件（无 user_id），仅 admin 可见
```

uuid 由后端 `uuid.uuid4().hex` 生成，匹配 `^[A-Za-z0-9_-]{6,64}$`。文件操作前先 `_safe_id()` 校验，防路径注入。

## JSON Schema

```json
{
  "id":         "<32-char hex>",
  "user_id":    42,
  "title":     "首条用户消息截取（≤32 字）；可被 PATCH 改",
  "created_at": "ISO 8601 UTC",
  "updated_at": "ISO 8601 UTC",
  "summary":    "已压缩部分的事实摘要；空字符串表示从未压缩",
  "compact_at": 0,
  "messages": [
    {"role": "user|assistant", "content": "...", "ts": "ISO 8601"},
    {"role": "tool", "name": "read_document", "args": {...}, "content": "<截断结果>", "ts": "ISO 8601"}
  ]
}
```

**`role=tool`（Phase 0 工具轮持久化）**：一轮里 user 与 assistant 之间，按发生顺序插入工具调用记录（`name`/`args`/`content`）。`content` 落盘前按 `TOOL_STORE_MAX=4000` 截断。`append_turn` 写入、`get_history` 回放、前端折叠卡片展示。

**`user_id`**：绑定至 MySQL `users.id`。创建时由 `session["user_id"]` 写入，此后不可更改。

### `compact_at` 的语义

- `messages[0:compact_at]` —— 已压缩部分（保留在文件里仅供审计 / 反馈使用）
- `messages[compact_at:]` —— 未压缩部分（实际送给 LLM 的）
- `get_history(cid, user_id)` 返回 `[system 摘要] + messages[compact_at:]`

## 路由与权限

| 路由 | 用途 | 权限 |
|---|---|---|
| `GET /conversations` | 列表（不含 messages 体） | 普通用户只看自己的；admin 看全部 |
| `POST /conversations` | 新建空会话，绑定当前 session user_id | 需登录 |
| `GET /conversations/<id>` | 完整内容（含 messages + summary） | 需归属校验 |
| `PATCH /conversations/<id>` | 改名 | 需归属校验 |
| `DELETE /conversations/<id>` | 删 | 需归属校验 |
| `POST /conversations/<id>/compact` | 手动 L3 压缩（keep_tail_turns 可覆盖，按轮） | 需归属校验 |

`list_conversations()` 按 `updated_at` 倒序，最近活跃的排最前。

**归属校验**：`_check_ownership(conv)` → admin 直接通过；普通用户要求 `conv["user_id"] == session["user_id"]`。

## 核心函数签名（v2，含 user_id）

```python
load_conversation(cid: str, user_id: int) -> Optional[Dict]
find_conversation(cid: str) -> Optional[Dict]           # 跨用户搜索，仅供 admin/内部
save_conversation(conv: Dict) -> None                   # conv 必须含 user_id
append_turn(cid, user_id, user_text, assistant_text, tool_items=None) -> Optional[Dict]
get_history(cid: str, user_id: int) -> List[Dict]   # tool 消息原样回放（含 name）
compact_conversation(cid, user_id, cleaner_cfg, keep_tail_turns) -> Dict   # keep 按轮；=0 全压(L4)
```

## 自动新建会话（agent.py 行为）

`POST /agent/chat` 无 `conversation_id` 且用户已登录 → 后端**自动创建**新会话（`user_id` 绑定当前登录用户），然后正常持久化。未登录用户（匿名）不持久化。

```python
# agent.py 内部逻辑
elif user_id is not None:  # 已登录但未提供 conversation_id
    new_conv = { "id": new_id(), "user_id": user_id, ... }
    conv_store.save_conversation(new_conv)
    conversation_id = new_conv["id"]
```

## 向前兼容

根目录下无 `user_id` 的老 JSON 文件不会被任何用户的列表扫到（各自只扫自己的子目录），仅 admin 扫全目录时可见。可按需手动迁移到对应子目录并补 `user_id` 字段。

## 原子写

```python
tmp = fp.with_suffix(".json.tmp")
tmp.write_text(json.dumps(conv, ...), encoding="utf-8")
tmp.replace(fp)
```

避免半写损坏。

## 工具轮持久化（Phase 0，四级压缩方案的地基）

让工具调用进入对话历史（此前只在当轮 system prompt 注入、用完即弃），为后续 L2「工具裁剪」提供对象。

| 环节 | 改动 |
|---|---|
| `call_tools_node`（qa/nodes.py） | 执行工具时收集 `tool_items=[{name,args,result}]`，额外 `writer({"type":"tool_turn","items":...})` 推一个**内部事件** |
| `api/agent.py` 落盘 | generate() 从 `tool_turn` 事件收集 `tool_items_acc`（**`continue` 不下发前端**），传给 `append_turn(..., tool_items)` |
| `append_turn`（conversations.py） | user 与 assistant 之间插入 `role=tool` 消息，`content` 按 `TOOL_STORE_MAX=4000` 截断 |
| `get_history` | `role=tool` 原样透传（含 `name`） |
| `_history_to_messages`（qa/nodes.py） | `role=tool` → `SystemMessage("[历史工具调用 {name} 的返回]\n{content}")`（以文本回放，跨厂商稳，与当轮注入口径一致；**不**用正式 ToolMessage 以免严格的 tool_call 配对校验） |
| 前端 | `web/user.html` 的 `appendToolMsg()` + `ChatPage.tsx` 的 `role==='tool'` 分支：折叠卡片展示工具名/参数/结果（满足「对话界面可见」）。live 态仍用 tool_start/tool_end 临时指示，工具卡片在重新加载会话时出现 |

> 现 QA 图是单趟 `call_tools→retrieve→generate`，Phase 0 只持久化这一轮工具调用；schema/回放已按多轮设计，后续上 ReAct 循环兼容。

## 发送态裁剪（Phase 1 L1 窗口 + Phase 2 L2 工具裁剪）

`window_history(history, window_turns=20, tool_keep_turns=10)`（conversations.py）——**只决定"这一轮发什么"，不动存储，UI 仍渲染完整 `messages[]`**：
- **L1 窗口**：以 user 消息为轮边界，仅保留最近 `SEND_WINDOW_TURNS=20` 轮；前置 `system` 摘要恒保留（不计入轮数）。
- **L2 工具裁剪**：窗口内距今超过 `TOOL_KEEP_RECENT_TURNS=10` 轮的 `role=tool` 消息剪掉（最近 10 轮保留工具完整记录，11–20 轮仅留 user/assistant）。

**关键：测量与发送分离**。`api/agent.py` 的 `generate()` 里顺序是
①用**完整** `get_history` 估 token / 触发自动压缩 → ②`state["history"] = window_history(state["history"])` 再发图。
所以窗口不影响压缩阈值判定，只瘦身实际请求。飞书路径不调用（lark 自带 10 轮滚动历史）。

渐进降级效果：`[摘要] + 最近 11–20 轮(纯文本) + 最近 1–10 轮(含工具)`。

## L3 滚动摘要（手动 + 自动，存储态压缩）

### 触发条件

**L3 统一（Phase 3）**：手动 `compact` 与自动阈值压缩**同为 L3**，仅触发方式不同，保留尾部一致。

| 触发方式 | 条件 | 保留尾部 |
|---|---|---|
| 手动 | 用户消息 `compact` / `/compact`（`_compact_response`） | `L3_KEEP_TAIL_TURNS = SEND_WINDOW_TURNS = 20` **轮** |
| 自动 | 活跃区精确 token > `MAX_CONTEXT_TOKENS × COMPACT_THRESHOLD`（**1,000,000 × 0.8 = 800,000**） | 同上（20 轮） |

事件 `level` 字段统一为 `3`（前端不读 `level`，仅 `auto_compacted` 用 `total_compact_count`）。常量在 `api/conversations.py` 顶部。

### 压缩算法（保留尾部按"轮"，兼容工具消息）

1. `tail_start = _tail_start_by_turns(messages, keep_tail_turns)`：倒数第 `keep_tail_turns` 个 **user 消息**的下标（轮以 user 为边界，工具消息计入对应轮）。`keep_tail_turns<=0` → `len(messages)`（L4 全压）；总轮数 ≤ keep → `0`（unchanged）。
2. `tail_start <= compact_at` → `unchanged`（短会话/无新内容）。
3. 拼 `[历史摘要]\n<prior_summary>\n<messages[compact_at:tail_start]>` 为 raw_text（`_format_for_compaction` 把 user/assistant/tool 分别标 `用户：/助手：/工具[name]：`）。
4. 调清洗子图（`build_cleaning_graph().invoke`），用 `COMPACT_SYSTEM` prompt。
5. 写回 `conv["summary"]`、`conv["compact_at"] = tail_start`，**不删 messages**，只移动指针。
6. L3 keep_tail（20 轮）与 L1 发送窗口（20 轮）对齐：压缩后活跃区 ≈ 窗口，发送/存储一致。

### 复用清洗子图

压缩 = 文本转写。清洗子图的抽象就是 `(system_prompt, raw_text) → cleaned_text`，刚好契合。

```python
out = build_cleaning_graph().invoke({
    "raw_text": _format_for_compaction(prior_summary, to_compact),
    "system_prompt": COMPACT_SYSTEM,
    "cleaner_cfg": cleaner_cfg,
})
```

### token 计数（Phase 3：官方 DeepSeek 分词器）

`agent_service/token_counter.py` 的 `count_tokens(text)`：用 `token/tokenizer.json` 经
`PreTrainedTokenizerFast(tokenizer_file=...)` **直接加载快速分词器**精确计数（懒加载+进程缓存）。
- ⚠️ 不要用 `AutoTokenizer.from_pretrained(dir)`：transformers 会误退化成慢速 `LlamaTokenizer`（缺 sentencepiece `.model`），**丢中文、计数严重失真**。
- 分词器/transformers 不可用时自动降级粗估（CJK≈1、ASCII≈0.75、+4 开销），永不因计数失败中断对话。
- 会话层 `estimate_tokens` / `estimate_history_tokens` 已委托到 `count_tokens`。DeepSeek 对中文高效（约 0.5 token/字），粗估会高估近 2 倍。

预算 `MAX_CONTEXT_TOKENS = 1_000_000`（模型上下文 1M），阈值 80% = 800k，按**活跃区**（summary + messages[compact_at:]）判定。

## get_history 拼接逻辑

```python
def get_history(cid):
    conv = load_conversation(cid)
    if conv is None: return []
    history = []
    summary = conv.get("summary", "").strip()
    if summary:
        history.append({"role": "system", "content": f"[历史摘要]\n{summary}"})
    compact_at = int(conv.get("compact_at", 0) or 0)
    for m in conv.get("messages", [])[compact_at:]:
        history.append({"role": m["role"], "content": m["content"]})
    return history
```

QA 图的 `_history_to_messages()` 已扩展认 `role=system`，会转 `SystemMessage`。

## /agent/chat 集成点

```python
# api/agent.py:agent_chat()

# 1) L3 手动：compact 命令早返回
if message.lower() in _COMPACT_CMDS:
    return _compact_response(conversation_id, owner_id)

# 2) L3 自动 + 发送态裁剪（在 generator 里）
def generate():
    if conversation_id:
        if conv_store.estimate_history_tokens(state["history"]) + ... > threshold:  # 完整活跃区测量
            yield status …
            res = conv_store.compact_conversation(..., keep_tail_turns=conv_store.L3_KEEP_TAIL_TURNS)
            state["history"] = conv_store.get_history(conversation_id, owner_id)
            yield auto_compacted …
    # L1 窗口 + L2 工具裁剪：测量/压缩之后、发图之前
    state["history"] = conv_store.window_history(state["history"])
    for event in graph.stream(state, ...):
        yield event
```

## 前端约定

- `compact` 命令 **不进 `chatHistory`**、**不渲染 user 气泡**
- `compact_done` / `auto_compacted` 事件用紫色 badge 渲染，不进 `chatHistory`
- 切换会话时清空气泡，从 `messages[]`（完整未压缩版）重新渲染
- **前端不显示压缩级别标识**（按用户要求隐藏；事件 `level` 现统一为 3）

## 不变量（不能违反）

1. **不丢消息**：压缩只动 `summary` + `compact_at`，从不删除 `messages` 数组里的条目
2. **切换会话不串数据**：fetch `/conversations/<id>` 后必须清空再渲染
3. **服务端是历史的权威源**：`/agent/chat` 提供 `conversation_id` 时忽略客户端传的 `history`
4. **compact 命令不持久化**：跳过 `append_turn`

## 给反馈用的"完整历史"

`/feedback` 接收前端 `chatHistory`（完整未压缩版）—— 这是有意为之，反馈写 wiki 用的是原对话不是压缩版。

## 四级压缩方案进度

| 级别 | 功能 | 类型 | 状态 |
|---|---|---|---|
| Phase 0 | 工具轮持久化（`role=tool` 入历史） | 存储 | ✅ |
| L1 | 滑动窗口（`window_history` 最近 20 轮） | 发送态 | ✅ |
| L2 | 工具裁剪（窗口内仅最近 10 轮留工具） | 发送态 | ✅ |
| L3 | 滚动摘要（手动 compact + 自动 800k 阈值，keep_tail 按轮对齐窗口） | 存储 | ✅ |
| L4 | 熔断：自动 L3 第 3 次 → 全局强压（`keep_tail_turns=0`）+ 持久化清零计数 + `circuit_break` 事件 | 存储 | ✅ |

### L4 熔断实现（`api/agent.py` 自动压缩块）

- 触发判定：本次自动压缩前读 `conv_stats.get_compact_count`，`prior+1 >= CIRCUIT_BREAK_AFTER(3)` → 本次直接做 L4（`keep_tail_turns=L4_KEEP_TAIL_TURNS=0`，单次 LLM 调用，非二次压缩）。
- 计数：非熔断 → `increment_compact_count`（+1）；熔断 → 压缩成功后 `reset_compact_count`（**写 DB 清零，刷新/重启不丢**），事件 `total_compact_count: 0`。
- 计数周期：自动压缩 1→2→(第 3 次熔断+清零)→1→2→… ，DB 是权威源。
- 事件：非熔断发 `auto_compacted`（level:3）；熔断发 `circuit_break`。前端 `user.html` 收到 `circuit_break` → `_setCount(0)` + 撤掉"建议新开对话"提示 + toast。
- `conv_stats`（MySQL `conversation_stats.auto_compact_count`）三个接口：`get` / `increment`（`ON DUPLICATE KEY UPDATE +1`）/ `reset`（`UPDATE =0`），全持久化。`user.html` 加载会话时用 `conv.auto_compact_count` 初始化本地计数，故刷新不丢。
