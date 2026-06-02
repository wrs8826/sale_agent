# 前端模式

## 页面定位

### 用户端（端口 5001）

| 页 | 路由 | 用途 |
|---|---|---|
| `web/login.html` | `/`（未登录） | 登录 + 注册（tab 切换）；注册需填用户名/密码/确认密码/手机号/部门 |
| `web/user.html` | `/user` | 三页：**上传资料** / **智能问答** / **系统设置**；左侧 260px 固定侧栏导航 + 退出登录 |

### 管理员端（端口 5002）

| 页 | 路由 | 用途 |
|---|---|---|
| `web/admin/login.html` | `/`（未登录） | 管理员专属登录（调用 `/auth/admin-login`，只接受 admin 角色） |
| `web/admin/knowledge.html` | `/admin` `/admin/knowledge` | 知识库管理 + RAG 调试 |
| `web/admin/chat.html` | `/admin/chat` | 完整功能 chat |
| `web/admin/users.html` | `/admin/users` | 用户管理：列表 / 搜索 / 编辑 / 封禁 / 删除 |

> `web/index.html`（原角色选择页）已不再作为入口，仍保留文件。

## 共享资产

- `assets/common.css` —— header / panel / tabs / status / drop-zone / **设置抽屉** / **状态圆点**
- `assets/settings.js` —— 共享设置抽屉（齿轮按钮自动注入 header）

引入只需在 `<head>` 加 `<link rel="stylesheet" href="/assets/common.css">`，并在 `</body>` 前加 `<script src="/assets/settings.js" defer></script>`。

## 顶层结构（所有页通用）

```html
<header>
  <h1>...</h1>
  <span class="tag">...</span>
  <nav class="topnav">
    <!-- 管理员端：页面导航链接 -->
    <a href="/admin/knowledge">知识库管理</a>
    <a href="/admin/chat">Agent 对话</a>
    <!-- 所有已登录页必须有退出登录 -->
    <a href="#" id="btn-logout">退出登录</a>
  </nav>
  <!-- 齿轮按钮由 settings.js 自动插入 -->
</header>
<main>
  <section class="panel-full | panel-left | panel-right">
    <!-- 内容 -->
  </section>
</main>
```

**退出登录标准写法**（每个已登录页都有）：
```js
document.getElementById('btn-logout').addEventListener('click', async e => {
  e.preventDefault();
  await fetch('/auth/logout', { method: 'POST' });
  window.location.href = '/';
});
```

## 登录页结构（`web/login.html`）

登录和注册共用一个盒子，用 tab 切换：

```html
<div class="box">
  <div class="tabs">
    <button class="tab-btn active" data-tab="login">登 录</button>
    <button class="tab-btn"        data-tab="register">注 册</button>
  </div>
  <div class="panel active" id="panel-login">...</div>
  <div class="panel"        id="panel-register">...</div>
</div>
```

注册表单字段：`r-username` / `r-password` / `r-password2`（确认密码）/ `r-phone` / `r-dept`

关键 JS 校验顺序：
1. 所有字段非空
2. `password !== password2` → 报错，`r-password2` 加 `.invalid` 红框
3. 提交 `POST /auth/register`，成功后 1.5s 切回登录 tab 并预填用户名

## SSE 消费模板

所有 SSE 处理都长这样：

```js
async function someStreamingCall() {
  const resp = await fetch("/some/endpoint", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!resp.ok) { /* handle */ return; }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const lines = buf.split("\n");
    buf = lines.pop();      // 最后半行留到下次
    for (const line of lines) {
      if (!line.startsWith("data:")) continue;
      let evt;
      try { evt = JSON.parse(line.slice(5).trim()); } catch { continue; }
      if (evt.type === "token") { /* ... */ }
      else if (evt.type === "done") { /* ... */ }
      else if (evt.type === "error") { /* ... */ }
      // ...
    }
  }
}
```

加新事件类型 → 在 `if/else if` 链里加一行即可，**不需要改通信框架**。

## 共享小工具

```js
function esc(s)  // HTML 转义
function setStatus(el, msg, cls)   // cls ∈ "" | "ok" | "err" | "ing"
function now()   // "14:30"
```

`switchTab(name)` —— user.html / admin/knowledge.html 都用。

## user.html 特有模块

### 整体布局

`user.html` 采用**外侧 220px 导航栏 + 右侧内容区**结构，三个页面通过侧栏导航切换：

```
┌─────────────────────────────────────────────────────┐
│ 外侧导航 220px │  内容区（flex: 1）                  │
│  ─ logo        │                                     │
│  ─ 上传资料    │  当前激活页内容                     │
│  ─ 智能问答    │                                     │
│  ─ 系统设置    │                                     │
│  ─ 退出登录    │                                     │
└─────────────────────────────────────────────────────┘
```

**页面切换**：隐藏/显示对应 `div`，不跳转路由。

### 上传资料页

- 拖拽 + 点击 上传区 → `POST /upload`（FormData）→ 得到 `filename`
- 紧接着 `POST /ingest`（**必须带 `{"filename": "..."}` JSON body**）→ 消费 SSE
- SSE 事件类型：`reading` / `cleaning` / `storing` / `result` / `error`

```js
const res = await fetch('/ingest', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ filename: name }),   // ← 必须，否则后端 400
});
```

### 智能问答页（DeepSeek 风格双栏布局）

`#page-chat` 内部为 **flex-direction: row** 双栏：

```
┌──────────────────────────────────────────────────────────┐
│ .conv-sidebar 240px  │  .chat-main（flex: 1）            │
│  ─ 新对话按钮        │  ─ chat-header（标题）            │
│  ─ 分组标签          │  ─ chat-body（消息流）            │
│    今天 / 昨天 / …   │  ─ feedback-bar                   │
│  ─ 会话条目          │  ─ chat-input-wrap                │
│    (悬停显示重命名   │                                   │
│     / 删除按钮)      │                                   │
└──────────────────────────────────────────────────────────┘
```

#### 会话管理函数

| 函数 | 说明 |
|---|---|
| `loadConvList()` | `GET /conversations`，按今天/昨天/7天/更早分组渲染侧栏 |
| `startNewConversation()` | 重置 `convId=null`，清空消息区，移除激活状态 |
| `switchConversation(id)` | `GET /conversations/<id>`，渲染全部历史消息，重建 `chatHist` |
| `deleteConversation(id)` | `DELETE /conversations/<id>`，若删除当前则重置 |
| `startRename(id, btn)` | 原地替换 title 为 `<input>`，失焦/Enter 触发 `PATCH` |

#### 会话状态变化时序

```
用户发送消息
  → 服务端 SSE 返回 conversation_saved {conversation_id, title}
  → 前端 convId = ev.conversation_id
  → loadConvList()  ← 刷新侧栏（含激活态）
```

`conversation_saved` 到达前 `convId` 可为 null（首条消息），`/agent/chat` 请求不传 `conversation_id` 字段，后端自动创建。

#### 智能问答发送

- `POST /agent/chat {message, conversation_id?, top_k?}` → SSE
- token 字段名：**`ev.text`**（不是 `ev.content`）
- done 事件用 `ev.full_text` 定型气泡
- 维护 `chatHist = [{role, content}]` 本地数组，每轮追加 user + assistant

```js
if (ev.type === 'token') {
  agentText += ev.text || '';       // 正确字段名
} else if (ev.type === 'done') {
  if (ev.full_text) agentText = ev.full_text;
}
```

**反馈**：`POST /feedback {rating, comment, history: chatHist, conversation_id}`
- `history` 字段**必须**传，后端校验 `if not history: 400`

```js
body: JSON.stringify({
  rating, comment,
  history: chatHist,          // ← 必须，否则后端返回 400 "对话历史为空"
  conversation_id: convId || '',
})
```

### 系统设置页

四段配置（Chat / Cleaner / Embedding / Reranker），每段有：
- "继承上一段"开关（隐藏/显示字段）
- api_key 密码框 + 明文切换眼睛按钮
- base_url 输入框
- model_name 输入框
- 单段"测试连接"按钮

**读取**：`GET /settings` → `data.settings.<section>.{api_key_mask, api_key_set, base_url, model_name}`

**保存**：`POST /settings { chat: {...}, cleaner: {...}, ... }`（api_key 留空 = 保留原值）

**测试**：`POST /settings/test { <section>: {api_key, base_url, model_name} }` → `data.results.<section>.{ok, latency_ms, error}`

### 会话侧栏（左侧 220px 固定宽，智能问答页内）

DOM 结构：
```html
<div class="tab-pane" id="pane-chat">
  <div class="chat-shell">          <!-- flex row -->
    <aside class="conv-sidebar">
      <button class="btn new-conv-btn" id="newConvBtn">＋ 新对话</button>
      <div class="conv-list" id="convList"></div>
    </aside>
    <div class="chat-container">     <!-- flex column -->
      <div class="chat-msgs" id="chatMsgs"></div>
      <div class="chat-footer">...</div>
    </div>
  </div>
</div>
```

核心函数：
- `loadConvList({keepActive})` —— GET /conversations，渲染
- `createConversation({silent})` —— POST /conversations，激活新会话
- `switchConversation(id)` —— GET /conversations/<id>，清空气泡再渲染
- `renameConversation(id, oldTitle)` —— prompt + PATCH
- `deleteConversation(id)` —— confirm + DELETE，自动 fallback
- `renderConversationMessages(messages)` —— 从持久化 messages 还原气泡（永远完整版，与 summary 无关）

切换会话时 **必须**：
1. `chatMsgs.innerHTML = ""`，清空旧气泡
2. 重置反馈面板（评分 + 评语）
3. 更新 sidebar 的 `.active` 类
4. `chatHistory = messages.map(...)`，本地镜像同步

### 压缩 badge

`appendCompactBadge(evt)` —— 紫色居中提示条，**不进 chatHistory**。`compact_done`（手动）和 `auto_compacted`（自动）共用，根据 `evt.type` 调文案。

按用户要求：前端**不显示 L1/L2 级别字样**。

### compact 命令本地识别

```js
function isCompactCommand(text) {
  const t = text.trim().toLowerCase();
  return t === "compact" || t === "/compact";
}
```

匹配时：
- 不 `appendUserMsg(text)` —— 用户气泡不出现
- chatStat 改成"正在压缩历史…"
- 不入 `chatHistory`

## 设置抽屉（`assets/settings.js`）

齿轮按钮自动注入 `<header>` 末尾（`.topnav` 之后）。抽屉 DOM 注入 `<body>` 末尾。

### 添加新设置字段

1. 改 `SECTIONS` 数组，在对应 group 加：
   ```js
   {
     key: "chat",
     subtitle: "Chat Model",
     hint: "...",
   }
   ```
2. `buildSubsection(group)` 自动渲染 api_key / base_url / model_name 三字段。**如要加新字段**（比如 `temperature`），改 `buildSubsection` 函数加 `buildField()`
3. `fillForm(masked)` / `saveSettings()` 用 `data-section` + `data-field` 自动收集 / 填充，**不需要为新字段额外写代码**
4. 后端 `services.py:_DEFAULT_<SECTION>` + `load_<section>_settings()` 同步

### Storage 段（wiki_dir）

抽屉 DOM 结构在四段模型区块之后额外追加了一个 Storage section：

```js
// buildStorageSection() 生成：
<div class="settings-section">
  <h3>Storage</h3>
  <div class="settings-subsection">
    <h4><span class="status-dot" id="dot-storage">Wiki 目录</h4>
    <div class="hint">反馈 wiki 文件的存储路径…</div>
    <div class="settings-field">
      <label for="settings-storage-wiki_dir">目录路径</label>
      <input id="settings-storage-wiki_dir" type="text"
             data-section="storage" data-field="wiki_dir"
             placeholder="留空使用默认路径（agent_service/wiki/）">
    </div>
  </div>
</div>
```

`fillForm(masked)` 从 `masked.storage.wiki_dir` 填充；`saveSettings()` 发送 `payload.storage = { wiki_dir: ... }`。

### Embedding 变更后自动重建向量库

`saveSettings()` 收到响应后判断 `data.embedding_changed`：

```js
if (data.embedding_changed) {
  setStatus("Embedding 已变更，正在重建向量库…", "ing");
  // 调用 POST /vectordb/rebuild → SSE
  const rb = await fetch("/vectordb/rebuild", { method: "POST" });
  // 读 SSE 流，evt.type === "done" 时显示 "已保存 ✓ 向量库已重建（N 块）"
  // evt.type === "error" 时显示错误
}
```

`/vectordb/rebuild` SSE 事件类型：`status`（开始）/ `done`（`{rebuilt, count}`）/ `error`。

### 状态圆点

- 灰 `#d0d3da` —— 未测试
- 蓝色脉冲 —— 测试中（CSS 动画）
- 绿 `#2cc36b` —— 连通
- 红 `#e55353` —— 失败

`setDot(key, state, tip)` 改一个，`resetDots(state)` 改全部。

## 已知 CSS 陷阱

1. **flex 项默认 `min-width: auto`** —— 滑块、长文本、按钮被挤压消失的元凶。受影响必加 `min-width: 0`（输入框、grid 列、`.sep-item`）
2. **`padding: X% Y%`** —— 百分比相对父宽度，嵌套 flex 时会传染抖动。统一改 rem（参见 `admin/knowledge.html` 的 `.query-row` / `details.settings`）
3. **`flex-shrink: 0`** —— 按钮、图标必须显式声明，否则会被相邻 `flex: 1` 元素挤掉
4. **多个 `<style>` 块的 `.settings-body` 冲突** —— admin/knowledge.html 自带 grid 用 `.settings-body`，common.css 的设置抽屉也用了同名 class。后写的 admin 局部样式赢；如果改 common.css 的抽屉样式，注意别污染 admin

## 共享反馈面板

user.html 和 admin/chat.html 各自有一份星级反馈实现，**没有抽出共享**。如要改协议（比如加新字段），两份都得改。考虑要不要抽公共，但当前规模不必。

## 添加新前端页面的步骤

1. 在 `web/` 下加 `<name>.html`
2. `<link rel="stylesheet" href="/assets/common.css">`
3. 标准 header / main 结构
4. 引入 `<script src="/assets/settings.js" defer></script>`（自动挂齿轮）
5. `api/app.py` 加路由：
   ```python
   @app.route("/<name>")
   def name_page():
       return send_from_directory(str(WEB_DIR), "<name>.html")
   ```

---

## React 管理员端（web-admin/）

### ChatPage 布局

`ChatPage.tsx` 采用 **双栏** 布局，整体占满 `h-full`：

```
┌──────────────────────────────────────────────────────────────────┐
│ aside 224px（对话历史侧边栏）│  主对话区（flex: 1）              │
│  ─ [+ 新对话] 按钮          │  ─ Topbar（标题 + 清空按钮）      │
│  ─ 分组标签                 │  ─ Info banner（可关闭）           │
│    今天 / 昨天 /            │  ─ 消息流（overflow-y-auto）       │
│    最近7天 / 更早           │  ─ 反馈栏（可折叠，仅有消息时显示）│
│  ─ 会话条目                 │  ─ KB状态 + 输入框                 │
│    (悬停：重命名 / 删除)    │                                    │
└──────────────────────────────────────────────────────────────────┘
```

### 关键实现细节

#### `/conversations` 返回字段
`GET /conversations` 响应体是 `{ "items": [...] }`，**不是 `conversations`**。
```ts
const data = await res.json()
setConvList(data.items ?? [])   // ← 必须用 items
```

#### 思考动画
首个 `token` 事件到达前显示三点 bounce 动画，到达后移除并开始渲染气泡：
```tsx
// 状态：thinking=true 时渲染动画气泡，收到第一个 token 时 setThinking(false)
{thinking && (
  <div className="flex gap-3">
    ...
    <span className="flex gap-1">
      {[0,1,2].map(i => (
        <span key={i} className="w-1.5 h-1.5 bg-[#9ca3af] rounded-full animate-bounce"
          style={{ animationDelay: `${i * 0.15}s` }} />
      ))}
    </span>
    Agent 正在思考…
  </div>
)}
```

#### 知识库状态指示器
输入框上方显示 KB 状态，页面挂载时调用 `GET /files` 检查：
```ts
const kbDotColor = { checking:'bg-gray-300', ready:'bg-green-400', empty:'bg-amber-400', error:'bg-red-400' }[kbStatus]
```

#### 消息时间戳 + 复制按钮
每条消息记录发送时刻 `time: fmtTime()`，助手消息非流式状态时显示复制按钮：
```tsx
{msg.role === 'assistant' && !msg.streaming && msg.content && (
  <button onClick={() => copyMessage(msg.id, msg.content)}>
    <Copy size={10} /> {copied === msg.id ? '已复制' : '复制'}
  </button>
)}
```

#### 反馈栏
折叠式设计：`feedbackOpen` 控制显示，`feedbackExpanded` 控制展开：
- 每轮 `done` 事件后 `setFeedbackOpen(true)`，`setFeedbackExpanded(false)`
- 点击 chevron 展开/收起
- 提交时检查 `rating >= 1`，否则 toast 提示

#### 标题更新
`conversation_saved` 事件携带 `title` 时同步更新顶部标题：
```ts
} else if (evt.type === 'conversation_saved') {
  setConvId(evt.conversation_id)
  if (evt.title) setConvTitle(evt.title)
  loadConvList()
}
```

#### 加载历史会话
`GET /conversations/<id>` 返回含 `has_summary` / `user_id` 字段，加载后更新副标题；若归属他人则标注"查看用户 #N 的对话"：
```ts
setConvUserId(data.user_id ?? undefined)
const isOther = data.user_id !== undefined && myUserId !== undefined && data.user_id !== myUserId
setConvSub(isOther
  ? `查看用户 #${data.user_id} 的对话 · 共 ${msgCount} 条消息...`
  : `共 ${msgCount} 条消息...`)
```

#### 只读模式（admin 查看他人会话）
```ts
// 只读条件：当前会话的 user_id ≠ 自己的 user_id
const readOnly = convUserId !== undefined && myUserId !== undefined && convUserId !== myUserId
```
- `sendMessage` 函数首行加 `if (readOnly) return` 守卫
- 输入区 DOM：`readOnly` 为 true 时替换为琥珀色警告横幅，含「新建自己的对话」快捷按钮
- `myUserId` 来自 `useApp().auth.user_id`（需要 `/auth/me` 返回 `user_id` 字段）

后端双重保护：`agent.py` 中检测到 admin 向他人会话发消息时返回 `403 "管理员只能查看用户对话历史，不能代用户发送消息"`。

#### 用户名标注（admin 专属）
ChatPage 挂载时请求 `GET /users` 建立 `userMap: Record<number, string>`（id → username）：
- **侧边栏**：他人会话条目的日期行旁渲染紫色小徽标 `<UserCircle> username`
- **顶栏**：`readOnly` 为 true 时标题旁渲染紫色圆角 badge `<UserCircle> username 的对话`
- `userMap` 未加载或 id 缺失时回退显示 `#id`，非 admin 请求 `/users` 返回 403 时静默忽略

### 在 web-admin 中添加新页面

1. `src/pages/<Name>Page.tsx` 新建页面组件
2. `App.tsx` switch case 加 `case '<name>': return <NamePage />`
3. `Sidebar.tsx` 加导航条目（图标统一用 `lucide-react`）
4. `AppContext.tsx` 的 `currentPage` 类型加新值
