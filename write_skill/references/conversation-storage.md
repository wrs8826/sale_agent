# 会话持久化 + 两级压缩

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
    {"role": "user|assistant", "content": "...", "ts": "ISO 8601"}
  ]
}
```

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
| `POST /conversations/<id>/compact` | 手动压缩（keep_tail_pairs 可覆盖） | 需归属校验 |

`list_conversations()` 按 `updated_at` 倒序，最近活跃的排最前。

**归属校验**：`_check_ownership(conv)` → admin 直接通过；普通用户要求 `conv["user_id"] == session["user_id"]`。

## 核心函数签名（v2，含 user_id）

```python
load_conversation(cid: str, user_id: int) -> Optional[Dict]
find_conversation(cid: str) -> Optional[Dict]           # 跨用户搜索，仅供 admin/内部
save_conversation(conv: Dict) -> None                   # conv 必须含 user_id
append_turn(cid, user_id, user_text, assistant_text) -> Optional[Dict]
get_history(cid: str, user_id: int) -> List[Dict]
compact_conversation(cid, user_id, cleaner_cfg, keep_tail_pairs) -> Dict
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

## 两级历史压缩

### 触发条件

| 级别 | 触发 | 保留尾部 |
|---|---|---|
| L1 手动 | 用户消息 `compact` 或 `/compact` | `LEVEL1_KEEP_TAIL_PAIRS = 4` 轮 (8 条) |
| L2 自动 | 估算 token > `MAX_CONTEXT_TOKENS × COMPACT_THRESHOLD`（32000 × 0.8 = 25600） | `LEVEL2_KEEP_TAIL_PAIRS = 2` 轮 (4 条) |

常量在 `api/conversations.py` 顶部。要改默认值改这里。

### 压缩算法

1. 拼 `[历史摘要]\n<prior_summary>\n<messages[compact_at:tail_start]>` 为 raw_text
2. 调清洗子图（`build_cleaning_graph().invoke`），用 `COMPACT_SYSTEM` prompt
3. 把产出写回 `conv["summary"]`，更新 `conv["compact_at"] = tail_start`
4. **不删 messages**，只移动 `compact_at` 指针

### 复用清洗子图

压缩 = 文本转写。清洗子图的抽象就是 `(system_prompt, raw_text) → cleaned_text`，刚好契合。

```python
out = build_cleaning_graph().invoke({
    "raw_text": _format_for_compaction(prior_summary, to_compact),
    "system_prompt": COMPACT_SYSTEM,
    "cleaner_cfg": cleaner_cfg,
})
```

### token 估算

`estimate_tokens(text)`：粗略 CJK 1 字 ≈ 1 token，ASCII ≈ 0.75，加 4 角色开销。不精确，但作触发阈值够用。

精确需求请接 tiktoken 或 DashScope tokenizer API，但目前不需要。

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

# 1) L1 早返回
if message.lower() in _COMPACT_CMDS:
    return _compact_response(conversation_id, level=1)

# 2) L2 内联（在 generator 里）
def generate():
    if conversation_id:
        if conv_store.estimate_history_tokens(state["history"]) + ... > threshold:
            yield status …
            res = conv_store.compact_conversation(...)
            state["history"] = conv_store.get_history(conversation_id)
            yield auto_compacted …
    for event in graph.stream(state, ...):
        yield event
```

## 前端约定

- `compact` 命令 **不进 `chatHistory`**、**不渲染 user 气泡**
- `compact_done` / `auto_compacted` 事件用紫色 badge 渲染，不进 `chatHistory`
- 切换会话时清空气泡，从 `messages[]`（完整未压缩版）重新渲染
- **前端不显示 L1 / L2 级别标识**（按用户要求隐藏）

## 不变量（不能违反）

1. **不丢消息**：压缩只动 `summary` + `compact_at`，从不删除 `messages` 数组里的条目
2. **切换会话不串数据**：fetch `/conversations/<id>` 后必须清空再渲染
3. **服务端是历史的权威源**：`/agent/chat` 提供 `conversation_id` 时忽略客户端传的 `history`
4. **compact 命令不持久化**：跳过 `append_turn`

## 给反馈用的"完整历史"

`/feedback` 接收前端 `chatHistory`（完整未压缩版）—— 这是有意为之，反馈写 wiki 用的是原对话不是压缩版。

## 加新压缩级别的思路（如果以后要）

1. 在 `api/conversations.py` 加 `LEVEL3_KEEP_TAIL_PAIRS`
2. 在 `compact_conversation` 不需要改（已经按 `keep_tail_pairs` 参数化）
3. 在 `/agent/chat` 加新触发条件
4. 在前端 `appendCompactBadge` 不用改（已按 evt.type 分支）
