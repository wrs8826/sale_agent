# 会话持久化 + 四级压缩（Phase 0 工具持久化 + L1/L2 发送态裁剪 + L3 滚动摘要 + L4 熔断）

## 存储后端（MySQL `conversations` 表）

会话存 MySQL，不再以 JSON 文件为事实源。表为「去规范化元数据列 + JSON body 列」：

```sql
CREATE TABLE conversations (
    id            VARCHAR(64)  PRIMARY KEY,   -- uuid_hex
    user_id       INT          NOT NULL,
    title         VARCHAR(255) NOT NULL DEFAULT '',
    has_summary   TINYINT(1)   NOT NULL DEFAULT 0,
    compact_at    INT          NOT NULL DEFAULT 0,
    message_count INT          NOT NULL DEFAULT 0,
    created_at    VARCHAR(40)  NOT NULL DEFAULT '',  -- ISO-8601 字符串，字典序==时间序
    updated_at    VARCHAR(40)  NOT NULL DEFAULT '',
    body          LONGTEXT     NOT NULL,             -- 整个会话 JSON（结构见下）
    KEY idx_user_updated (user_id, updated_at),
    KEY idx_updated (updated_at)
);
```

- **元数据列**仅供 `GET /conversations` 列表查询/排序用（走索引，不读 body）；**body 列**是各业务函数读写的 conv dict 全文。消息体是文档形状，刻意不拆成消息表。
- `created_at`/`updated_at` 存 ISO-8601 字符串而非 DATETIME：固定格式下字典序==时间序，`ORDER BY updated_at DESC` 即时间排序，与旧文件实现一致，免去时区/格式转换坑。
- `save_conversation` 走 `INSERT … ON DUPLICATE KEY UPDATE`（`created_at`/`user_id` 仅首次写入，更新不动）。`load_conversation`/`find_conversation` 先查库；`list_conversations` 用 `_list_summaries()` 仅查元数据列。
- DB 连接参数与 `auth.py`/`conv_stats.py` 一致：env `DB_HOST/DB_PORT/DB_USER/DB_PASS/DB_NAME`，兜底硬编码。

### 迁移与旧文件兼容

```
agent_service/conversations/           ← 现仅作：会话级 filelock 锁文件 + 旧 JSON 只读备份
├── <user_id>/<uuid_hex>.json          ← 旧格式，已被启动迁移导入库；保留作备份不删
├── <user_id>/<uuid_hex>.json.lock     ← filelock 跨进程锁文件
└── <uuid_hex>.json                    ← 无 user_id 的根目录老文件：无法判定归属，不入库、不进列表，但可经 find_conversation 直读
```

- **启动迁移**：`create_app` 调 `conversations.ensure_table()` → 建表 + `migrate_files_to_db()` 幂等导入历史文件（按文件名 `<id>.json` 先跳过已迁移项，免重复读盘）。也可手动跑 `python -m scripts.migrate_conversations_to_db [--dry-run]`。
- **读回退**：库内 miss 时 `load/find_conversation` 回退读旧 JSON 文件（只读），覆盖尚未迁移的会话；写只写库。
- **删除**：删库行 + `_file_remove()` 清旧文件备份（防只读回退把已删会话"复活"）。

uuid 由后端 `uuid.uuid4().hex` 生成，匹配 `^[A-Za-z0-9_-]{6,64}$`。所有读写前先 `_safe_id()` 校验，防注入；`id` 同时是表主键。

## JSON Schema（body 列内容）

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

### `compact_at` 的语义（头尾保留 + 中间折叠）

压缩**只折叠中间段**，结构性保留首尾原文。以 `head_end = _turn_end_index(messages, HEAD_KEEP_TURNS)` 为头部末尾：

- `messages[0:head_end]` —— **头部**：最初 `HEAD_KEEP_TURNS`(=3) 轮，逐字保留（用户最初的任务/背景锚点）
- `messages[head_end:compact_at]` —— **中间段**：已折进 `summary`（原文留文件仅供审计 / 反馈）
- `messages[compact_at:]` —— **尾部**：最近若干轮，逐字保留
- `get_history(cid, user_id)` 按时间顺序返回 `头部原文 + [中间历史摘要] + 尾部原文`

不变量：`compact_at == 0`（从未压缩）或 `compact_at >= head_end`（头部永不被折叠，由 `compact_conversation` 的 `fold_from = max(compact_at, head_end)` 保证）。**被折叠的中间段一定先进摘要 → 杜绝静默丢失**（这正是修掉「20 轮窗口丢未摘要历史」的关键）。

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
get_history(cid: str, user_id: int) -> List[Dict]   # 头部原文 + [中间历史摘要] + 尾部原文；tool 消息原样回放（含 name）
should_auto_compact(cid, user_id, keep_head_turns=3, keep_tail_turns=10, margin=4) -> bool  # 主触发：逐字轮数 > 头+尾+余量
compact_conversation(cid, user_id, cleaner_cfg, keep_tail_turns, keep_head_turns=3) -> Dict   # 折中间段、保首尾，均按轮
acquire_conversation(cid, user_id, wait=0.0) -> _ConvGate   # 取会话锁；超时抛 ConversationBusy；调用方负责 .release()
conversation_lock(cid, user_id, wait=0.0)                   # 上下文管理器版（自包含读改写用）
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

历史 `<user_id>/<uuid>.json` 文件在启动时由 `migrate_files_to_db()` 幂等导入库（见上「迁移与旧文件兼容」），导入后仍保留在磁盘作只读备份。根目录下无 `user_id` 的老 JSON 文件无法判定归属，**不入库、不进列表**，但仍可经 `find_conversation`（库 miss → 文件回退）直读；如需让其进列表，手动补 `user_id` 后移入对应 `<user_id>/` 子目录再重启迁移即可。

## 写入（DB upsert）

```python
INSERT INTO conversations (...) VALUES (...)
ON DUPLICATE KEY UPDATE title=VALUES(title), ..., body=VALUES(body)
```

单条 upsert 天然不会"写出半行"，但**不防并发丢更新**（两个 load→改→save 互相覆盖整个 body）——见下。

## 并发：会话级单飞锁（防丢更新 + 中断丢弃）

`load→改→save` 的读改写在并发下会互相覆盖整个 body 行。**双进程**（`app_user` 5001 / `app_admin` 5002 同写一行会话记录，如 admin 压缩用户会话而用户正在聊）使纯进程内锁不够，故用**进程内 `threading.Lock` + 跨进程 `filelock`** 的每会话锁。锁文件仍落在 `CONVERSATIONS_DIR/<user_id>/<cid>.json.lock`（锁与数据存哪无关）。

- **粒度**：每会话一把（key=`<user_id>/<cid>`，锁文件 `<cid>.json.lock`）。不同会话/用户互不阻塞。只锁写，不锁读（读走库单行查询，恒读到完整旧/新版）。
- **只在 4 个入口加锁**（`/agent/chat` 生成、`compact` 命令、`POST /compact`、`PATCH`/`DELETE`），核心读写函数本身不取锁 → **无嵌套、无重入需求**，threading.Lock（非重入）即安全。
- **单飞**：`/agent/chat` 入口 `acquire_conversation(cid, owner_id, wait=CHAT_LOCK_WAIT=10)`，锁贯穿整段 SSE，于外层 `_streamed()` 的 `finally` 释放（完成/异常/客户端断开 `GeneratorExit` 均释放）。取不到 → `409`。`wait=10` 覆盖"中断后立刻重发"时上一请求的释放窗口；真并发（另一设备/管理端）等满 10s → 409。
- **中断 = 丢弃**：用户点停止 → 前端 abort SSE → 服务端下一次 yield 抛 `GeneratorExit` → 进 finally 放锁；因**未走到 `append_turn`**，本轮 user+assistant 整体不落盘（与"持久化只在 done 后"天然一致）。中途的自动压缩若已落盘则保留（与当前轮无关）。
- **降级**：未装 `filelock` → 仅进程内锁 + 告警（单进程仍互斥，跨进程不互斥）。进程崩溃时 OS 自动释放文件锁，无死锁残留。
- **飞书不涉及**：飞书走 `lark_history`、无 UI 中断概念，本机制不覆盖（如需并发安全另议串行锁）。

```python
# /agent/chat 入口（视图取锁、生成器释放）
gate = None
if conversation_id and owner_id is not None:
    try:
        gate = conv_store.acquire_conversation(conversation_id, owner_id, wait=conv_store.CHAT_LOCK_WAIT)
    except conv_store.ConversationBusy:
        return jsonify({"error": "该对话正在生成中，请先中断当前回答或稍后再试"}), 409
def _streamed():
    try:
        yield from generate()       # 内部 append_turn/compact 均在此锁内，无需再取
    finally:
        if gate is not None:
            gate.release()
```

前端（`web/user.html` + `web-admin/ChatPage.tsx`）：流式中禁用 Enter、发送键变「停止/⏹」（点击 abort）、收到 `409` 回滚乐观插入的用户气泡并 toast。两端本就「一会话只在一个标签打开」「streaming 时不发新消息」，409 仅兜底跨设备/管理端。

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
- **L1 窗口**：以 user 消息为轮边界，仅保留最近 `SEND_WINDOW_TURNS=20` 轮。头尾方案落地后，`get_history` 已把活跃区压到 ~13 轮（头3+摘要+尾10），故 L1 通常不裁剪，仅作"压缩没跑成功（缺 Key/失败）"时的兜底。摘要 `system` 消息现位于头部之后（不再在下标 0），但因总轮数 < 20 不会被丢，且 L1 只按 user 边界计轮、不动 system。
  - **摘要永不丢兜底**：极端降级（压缩长期失败、活跃区涨过 20 轮）下 L1 会裁掉头部、可能连带摘要。`window_history` 末尾做 `if sm not in result: result = [sm] + result`——被裁掉的摘要前置回插；常态摘要仍在 result 中、不重复。彻底堵死"窗口丢未摘要历史"的回归口。
- **L2 工具裁剪**：窗口内距今超过 `TOOL_KEEP_RECENT_TURNS=10` 轮的 `role=tool` 消息剪掉（头部那 3 轮的工具会被剪，仅留 user/assistant 文本，符合"头部作锚点"的定位）。

**关键：测量与发送分离**。`api/agent.py` 的 `generate()` 里顺序是
①`should_auto_compact`（按轮）/ token 兜底触发自动压缩 → ②`state["history"] = window_history(state["history"])` 再发图。
所以窗口不影响压缩触发判定，只瘦身实际请求。飞书路径不调用 `window_history`——飞书自己在
`lark_bot._maybe_reset_context()` 里按 token 占比触发 `lark_history.split_for_reset()`，
硬保留头 2 + 尾 5 轮、丢弃中间（归档进 wiki，不留 LLM 摘要），与本节的"折中间段进摘要"机制不同。

发送效果：`头 3 轮原文(工具已剪) + [中间历史摘要] + 尾 10 轮(含工具)`。

## L3 滚动摘要（手动 + 自动，存储态压缩）

### 触发条件

**L3 统一（Phase 3）**：手动 `compact` 与自动阈值压缩**同为 L3**，仅触发方式不同，保留尾部一致。

| 触发方式 | 条件 | 保留 |
|---|---|---|
| 手动 | 用户消息 `compact` / `/compact`（`_compact_response`） | 头 `HEAD_KEEP_TURNS=3` 轮 + 尾 `L3_KEEP_TAIL_TURNS=TAIL_KEEP_TURNS=10` 轮 |
| 自动·主 | `should_auto_compact()`：逐字（未折叠）轮数 > `HEAD+TAIL+AUTO_COMPACT_MARGIN`（3+10+4 → 第 18 轮触发，折到 13 轮） | 同上 |
| 自动·次 | 活跃区精确 token > `MAX_CONTEXT_TOKENS × COMPACT_THRESHOLD`（1,000,000 × 0.8）——单轮超长等极端兜底 | 同上 |

> **为何从 token 改成按轮**：旧设计自动压缩按 token（80 万）触发、但发送侧 `window_history` 按 20 轮裁剪，二者口径不一 → 21 轮~80 万 token 之间的历史被窗口直接丢弃且**未进摘要（静默丢失）**。改为按轮触发后，窗口要丢的轮一定已折进 summary。token 阈值降级为极端兜底（几乎不触发）。

事件 `level` 字段统一为 `3`（前端不读 `level`，仅 `auto_compacted` 用 `total_compact_count`）。常量在 `api/conversations.py` 顶部。

### 压缩算法（折中间段、保首尾，按"轮"，兼容工具消息）

1. `head_end = _turn_end_index(messages, keep_head_turns)`：头部末尾下标（第 `keep_head_turns+1` 个 user 消息处）。
2. `tail_start = _tail_start_by_turns(messages, keep_tail_turns)`：尾部起点（倒数第 `keep_tail_turns` 个 user 消息处）。
3. `fold_from = max(compact_at, head_end)`（头部永不折叠；已折叠部分从 `compact_at` 续上）。`tail_start <= fold_from` → `unchanged`（太短/无中间段）。
4. 拼 `[历史摘要]\n<prior_summary>\n<messages[fold_from:tail_start]>` 为 raw_text（`_format_for_compaction` 把 user/assistant/tool 分别标 `用户：/助手：/工具[name]：`）。
5. 调清洗子图（`build_cleaning_graph().invoke`），用 `COMPACT_SYSTEM` prompt。
6. 写回 `conv["summary"]`、`conv["compact_at"] = tail_start`，**不删 messages**，只移动指针。
7. 压缩后逐字活跃区 = 头(3) + 尾(10) = 13 轮 < `SEND_WINDOW_TURNS(20)`，故 `window_history` 不会再二次裁掉首尾，发送/存储一致。

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

预算 `MAX_CONTEXT_TOKENS = 1_000_000`（模型上下文 1M），阈值 80% = 800k，按 `get_history` 输出（头部 + 中间摘要 + 尾部）+ 当前 query 估算。**现仅作次级兜底**——主触发已是按轮（`should_auto_compact`），头尾方案下活跃区轮数恒受控，token 几乎到不了 80 万。

## get_history 拼接逻辑

```python
def get_history(cid, user_id):
    conv = load_conversation(cid, user_id)
    if conv is None: return []
    messages = conv.get("messages", [])
    summary = (conv.get("summary") or "").strip()
    compact_at = int(conv.get("compact_at", 0) or 0)
    # 未压缩 → 原样回放全部
    if compact_at <= 0 or not summary:
        h = ([{"role":"system","content":f"[历史摘要]\n{summary}"}] if summary else [])
        return h + [_msg_view(m) for m in messages]
    # 已压缩 → 头部原文 + 中间摘要 + 尾部原文（时间顺序）
    head_end = min(_turn_end_index(messages, HEAD_KEEP_TURNS), compact_at)
    return ([_msg_view(m) for m in messages[:head_end]]
            + [{"role":"system","content":f"[中间历史摘要]\n{summary}"}]
            + [_msg_view(m) for m in messages[compact_at:]])
```

注意：摘要 `system` 消息现在位于**头部之后**（时间居中），不再恒在下标 0。QA 图的 `_history_to_messages()` 认 `role=system` 转 `SystemMessage`，多个/任意位置的 system 都兼容。`window_history` 只删 `role=tool`、并按 user 边界算轮，不依赖摘要在首位；压缩后活跃区仅 13 轮 < 20，故其 L1 窗口对首尾无副作用。

## /agent/chat 集成点

```python
# api/agent.py:agent_chat()

# 1) L3 手动：compact 命令早返回
if message.lower() in _COMPACT_CMDS:
    return _compact_response(conversation_id, owner_id)

# 2) L3 自动 + 发送态裁剪（在 generator 里）
def generate():
    if conversation_id:
        turn_trigger = conv_store.should_auto_compact(conversation_id, owner_id)   # 主：按轮
        tokens = conv_store.estimate_history_tokens(state["history"]) + ...
        if turn_trigger or tokens > threshold:                                     # 次：token 兜底
            yield status …
            res = conv_store.compact_conversation(..., keep_tail_turns=keep)       # 头部自动保留
            state["history"] = conv_store.get_history(conversation_id, owner_id)
            yield auto_compacted / circuit_break …
    # L1 窗口 + L2 工具裁剪：测量/压缩之后、发图之前（压缩后活跃区已 ~13 轮，窗口基本是兜底）
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
5. **会话级单飞**：同一会话同一时刻只允许一个生成/压缩/改名/删除；写入口必须取会话锁，取不到回 `409`，绝不并发读改写同一会话文件
6. **取锁与释放对称**：视图里 `acquire_conversation` 成功后，必须保证所有收尾路径都 `release`（生成器用 `finally`；崩溃靠 OS 释放文件锁）

## 给反馈用的"完整历史"

`/feedback` 接收前端 `chatHistory`（完整未压缩版）—— 这是有意为之，反馈写 wiki 用的是原对话不是压缩版。

## 四级压缩方案进度

| 级别 | 功能 | 类型 | 状态 |
|---|---|---|---|
| Phase 0 | 工具轮持久化（`role=tool` 入历史） | 存储 | ✅ |
| L1 | 滑动窗口（`window_history` 最近 20 轮）——压缩后活跃区 ~13 轮，现为兜底 | 发送态 | ✅ |
| L2 | 工具裁剪（窗口内仅最近 10 轮留工具） | 发送态 | ✅ |
| L3 | 滚动摘要（手动 compact + 自动**按轮**触发；**折中间段、保头 3 + 尾 10 轮**） | 存储 | ✅ |
| L4 | 熔断：自动压缩第 3 次 → 清零计数 + `circuit_break` 事件（尾部仍保留，仅"压缩频繁"提示） | 存储 | ✅ |

### L4 熔断实现（`api/agent.py` 自动压缩块）

- 触发判定：本次自动压缩前读 `conv_stats.get_compact_count`，`prior+1 >= CIRCUIT_BREAK_AFTER(3)` → 本次走 L4。
- 头尾方案下 `L4_KEEP_TAIL_TURNS = TAIL_KEEP_TURNS(10)`（**不再清空尾部**）：L4 与 L3 的折叠口径一致，仅多承担"清零计数 + 发 circuit_break 提示"。这样"保留最近 10 轮"不会被熔断周期性破坏。
- 计数：非熔断 → `increment_compact_count`（+1）；熔断 → 压缩成功后 `reset_compact_count`（**写 DB 清零，刷新/重启不丢**），事件 `total_compact_count: 0`。
- 计数周期：自动压缩 1→2→(第 3 次熔断+清零)→1→2→… ，DB 是权威源。
- 事件：非熔断发 `auto_compacted`（level:3）；熔断发 `circuit_break`。前端 `user.html` 收到 `circuit_break` → `_setCount(0)` + 撤掉"建议新开对话"提示 + toast。
- `conv_stats`（MySQL `conversation_stats.auto_compact_count`）三个接口：`get` / `increment`（`ON DUPLICATE KEY UPDATE +1`）/ `reset`（`UPDATE =0`），全持久化。`user.html` 加载会话时用 `conv.auto_compact_count` 初始化本地计数，故刷新不丢。
