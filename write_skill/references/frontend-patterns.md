# 前端模式

## Markdown 渲染

| 端 | 实现 |
|---|---|
| `web/user.html`（vanilla JS） | CDN 引入 `marked.js@9`，`renderMd(raw)` 调用 `marked.parse(raw)`；开启 `gfm: true, breaks: true` |
| `web-admin/`（React） | `react-markdown`（v9）+ `remark-gfm` 插件（`<ReactMarkdown remarkPlugins={[remarkGfm]}>`），包在 `<div className="prose-chat">` 内，表格/删除线/任务列表等 GFM 语法依赖该插件，缺失则表格会原样显示为 Markdown 文本 |

### user.html 的 renderMd

```html
<!-- </style> 前加载 CDN，保证 <script> 块执行时已就绪 -->
<script src="https://cdn.jsdelivr.net/npm/marked@9/marked.min.js"></script>
```

```js
marked.use({ gfm: true, breaks: true });

function renderMd(raw) {
  if (!raw) return '';
  // 后处理替换：不依赖 renderer API（marked v9 的 table renderer 签名已改为 token 对象，旧写法失效）
  return marked.parse(raw)
    .replace(/<table>/g, '<div class="table-wrap"><table>')
    .replace(/<\/table>/g, '</table></div>');
}
```

**注意**：marked v9 的 `Renderer` 自定义方法签名已从 `table(header, body)` 改为接收 token 对象，旧写法静默失效。用后处理字符串替换是版本无关的稳定方案。

表格 CSS 关键点：`.table-wrap { overflow-x: auto }` + `table { min-width: 100%; white-space: nowrap }`，`table` 自身不能 `overflow-x`，必须由外层 div 承载滚动。

支持能力：标题（h1-h6）、加粗/斜体/删除线、有序/无序列表、任务列表、代码块（含语言标注）、行内代码、表格、引用块、链接、分割线、换行。

CSS 样式在 `user.html` 的 `/* Markdown */` 区块：`p / strong / em / ul / ol / li / code / pre / blockquote / h1-h6 / a / hr / table / th / td`，全部作用在 `.msg-bubble` 内。

#### 消息气泡布局（避免长消息脱离头像）

`.msg-row`（flex；用户行 `flex-direction: row-reverse` 把头像放右）→ `.msg-avatar` + `.msg-content`（气泡 + 时间/复制 meta 的包装）。
⚠️ 宽度约束与对齐**放在 `.msg-content` 上**，不要放在 `.msg-bubble`：`.msg-content { max-width:76%; min-width:0 }`，用户 `align-items:flex-end`、AI `flex-start`；`.msg-bubble { max-width:100%; word-break:break-word; overflow-wrap:anywhere }`。
若把 `max-width` 留在气泡而包装无约束/无对齐，长消息时包装撑满整行、气泡在其中左对齐，会出现「气泡贴左、头像在右、中间大空隙」。

#### 消息气泡下方的时间（`.msg-time`）

气泡 meta 区显示的是**该消息的发送时间**，而非渲染时刻：

- 后端 `append_turn`（`api/conversations.py`）给每条消息写入 `ts`（UTC ISO，`datetime.now(timezone.utc).isoformat`），一轮 user+assistant 共用同一个 `ts`；`GET /conversations/<id>` 把 `messages[].ts` 原样返回。
- 前端用 `fmtTimeFromIso(iso)` 把 UTC `ts` 解析为本地 `年/月/日 时:分`（统一格式常量 `_TIME_FMT`，`toLocaleString('zh-CN', _TIME_FMT)`，解析失败返回空串），**仅在加载历史会话时**用它填充每条消息的 `time` 字段（`web/user.html` 的 `loadConversation` map、`web-admin/src/pages/ChatPage.tsx` 的 `loadConversation` map）。
- **实时发送**时不依赖后端 `ts`：发送入口先 `const sendTime = fmtTime()` 取一次本地时间，user 消息与本轮 assistant 消息（含 token/done/error/网络异常各分支）全部复用 `sendTime`，与后端「一轮共用一个 ts」保持一致，重新加载该会话时时间不跳变。
- ⚠️ 不要在 `appendMsg` / 各推送分支里直接调用 `fmtTime()` 取「当前时刻」——切换标签或重渲染会把时间刷成打开时刻。时间必须随消息对象（`MsgItem.time` / tab 内 `messages[].time`）存储并透传。`appendMsg(role, html, { time })` 缺省回退 `fmtTime()` 仅作兜底。

### 注意

- `marked.parse()` 返回完整 HTML 字符串，直接赋给 `element.innerHTML`
- 不需要额外的 XSS 库：AI 输出不含用户提供的原始 HTML；如有需要可接入 `DOMPurify`
- `breaks: true` 让单个换行变 `<br>`，与旧版 `.replace(/\n/, '<br>')` 行为一致，防止段落合并

## 页面定位

### 用户端（端口 5001）

| 页 | 路由 | 用途 |
|---|---|---|
| `web/login.html` | `/`（未登录） | 登录 + 注册（tab 切换）；注册需填用户名/密码/确认密码/手机号/部门 |
| `web/user.html` | `/user` | 三页：**上传资料** / **智能问答** / **系统设置**；左侧 260px 固定侧栏导航 + 退出登录 |

### 管理员端（端口 5002）

管理员端已迁为 **React + TypeScript（`web-admin/`）**，旧的 vanilla `web/admin/*.html` 已删除。
开发用 Vite（:5173，`vite.config.ts` 代理 API 到 Flask :5002），生产用 `web-admin/dist/`（nginx 托管）。

| 页面（React） | 用途 |
|---|---|
| `web-admin/src/pages/LoginPage.tsx` | 管理员登录（`/auth/admin-login`，仅 admin 角色） |
| `web-admin/src/pages/KnowledgePage.tsx` | 知识库管理 + RAG 调试 |
| `web-admin/src/pages/ChatPage.tsx` | Agent 对话（SSE 流式 + 评分反馈） |
| `web-admin/src/pages/UsersPage.tsx` | 用户管理：列表 / 搜索 / 编辑 / 封禁 / 删除 |
| `web-admin/src/pages/SettingsPage.tsx` | 系统设置（5 张配置卡片） |

> 详见 SKILL.md 的 `web-admin/` 模块地图与约定。

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

### 执行方案卡片（`plan_*`，先列方案再执行）

react + `enable_planning` 时，后端在 `token` 之前先流式推 `plan_start` / `plan_token` / `plan_end`。两端都把方案渲染成独立「📋 执行方案」折叠卡片，**置于回答气泡之前**：

- **`plan_start`**：若助手消息尚未建（方案早于 token 到达），**提前建占位助手消息**（`content:''`, `plan:''`, `streaming:true`），并把 `started=true`，避免随后 `token` 再 push 重复消息。
- **`plan_token`**：累加到该助手消息的 `plan` 字段，流式中卡片展开。
- **`plan_end`**：定型；`plan` 为空（规划失败/跳过）则删除 `plan` 字段、撤掉卡片；非空则折叠卡片。

把 `plan` 存在助手消息对象上（而非游离 DOM），切 tab 重渲染时方案卡片不丢。`user.html`：`renderPlanCard(assistantId, planText, {open})` + `renderTab` 里 `if (m.plan) renderPlanCard(...)`。`ChatPage.tsx`：消息渲染处 `msg.plan && <details>…<ReactMarkdown>{msg.plan}</ReactMarkdown></details>`，类型 `ChatMessage.plan?: string`。方案**不持久化**到后端，刷新后不再出现（仅当轮 thinking 产物）。

### 工具执行实时清单（`tool_start` / `tool_end`，可折叠）

live 流式时把工具执行渲染成一个**可折叠清单**，每个工具一行：未完成 `[ ]`、成功 `[✅]`、失败 `[❌]`，每个工具事件刷新一次（含 `提取关键词`/`检索知识库` 等管线步骤）。

- 数据存助手消息 `m.tools = [{name, status:'running'|'ok'|'error'}]`。`tool_start` → push `{name, status:'running'}`；`tool_end` → 从后往前找**同名且 running** 的项，置 `ok`/`error`（有 `error` 字段则 `error`）。
- `user.html`：`renderToolChecklist(assistantId, tools, {open})` + `applyToolEvent(m, ev)`；SSE 循环里 `tool_start`/`tool_end` 分支调用；`done` 时把清单 `<details>` 折叠（`open=false`）；`renderTab` 里 `m.tools` 重渲染。
- `ChatPage.tsx`：`msg.tools && <details open={!!msg.streaming}>…</details>`，类型 `ChatMessage.tools?`。
- 置于**回答气泡之前**（顺序：执行方案卡片 → 工具清单 → 回答气泡 → 下载按钮）。
- **重载历史也用同一套清单 UI**（旧的 per-tool 折叠卡片 `appendToolMsg` / `.tool-msg` / `Wrench` 卡片已删除）：`renderTab`（user.html）/ 渲染前预处理（ChatPage.tsx）把**连续的 `role=tool` 消息合并成一个清单**，status 由内容判定（含 `[工具执行失败]` → `[❌]`，否则 `[✅]`，重载无 running 态），并对其中的 `generate_word_document` 渲染下载按钮。live 的 `m.tools` 不持久化，重载时从持久化的 `role=tool` 消息重建。

### 下载按钮（`download` 事件，文件生成）

**绝不依赖模型把下载链接写进回答**——实测模型会把链接写错/编造。后端从真实工具结果抽出 `/download/...docx` 下发 `download` 事件（`{url, filename}`），前端据此渲染一个真实 `<a download href={url}>` 按钮：

- **live**：收到 `download` 事件 → 存到助手消息 `m.download = {url, filename}`；按钮在回答气泡之后渲染（`user.html` 在 `done` 时 `renderDownloadBtn(...)`；`ChatPage.tsx` 渲染 `msg.download && <a>`）。
- **重载历史**：`download` 不持久化，但持久化的 `role=tool`（`generate_word_document`）消息内容含链接——两端在渲染该工具消息时用正则 `/download\/[^\s)\]]+\.docx/` 抽链接、渲染同款按钮。
- 按钮 `href` 是**相对路径** `/download/...`，依赖入口（Flask 直服 / nginx / vite 代理）把 `/download` 转发到后端；`web-admin` 开发态需在 `vite.config.ts` 代理 **`/download`**（顺带补 **`/admin`**，否则政策 Skill 页 `/admin/policy-*` 也断）。类型 `ChatMessage.download?: {url; filename}`。相对链接会自动指向当前访问地址——本机用 `http://localhost:5002` 访问管理端即走本地下载，不经任何公网入口（详见 common-pitfalls #38）。

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

**上传进度弹窗**：上传开始时自动弹出，处理中"确认"按钮禁用，完成/失败后按钮启用，用户点击确认后关闭。

| SSE 事件 | 进度 | 说明 |
|---|---|---|
| 上传开始（fetch 前） | 15% | 正在上传… |
| 上传完成（fetch 后） | 30% | 上传成功，AI 清洗中… |
| `reading` | 50% | 正在读取文件… |
| `cleaning` | 70% | AI 清洗中… |
| `storing` | 85% | 正在写入知识库… |
| `result` | 100% | 成功（绿色进度条） |
| `error` | 当前% | 失败（红色进度条） |

两端实现：
- `web/user.html`：纯 CSS/JS 弹窗，函数 `umOpen / umUpdate / umDone / umError`，DOM 挂在 `</body>` 前
- `web-admin/KnowledgePage.tsx`：React state `ModalState`，`modal.phase` 三态（`processing/done/error`），Tailwind 样式

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
│ .conv-sidebar 240px ›│  .chat-main（flex: 1）            │
│  ─ 新对话按钮        │  ─ chat-header（标题）            │
│  ─ 分组标签          │  ─ chat-body（消息流）            │
│    今天 / 昨天 / …   │  ─ feedback-bar                   │
│  ─ 会话条目          │  ─ chat-input-wrap                │
│    (悬停显示重命名   │                                   │
│     / 删除按钮)      │                                   │
└──────────────────────────────────────────────────────────┘
```

#### 抽拉式会话侧栏（收起/展开）

`.conv-sidebar-wrap`（`position: relative`，不裁剪）包住两个子元素：

```html
<div class="conv-sidebar-wrap">
  <div class="conv-sidebar" id="conv-sidebar">…新对话按钮 + #conv-list…</div>
  <button class="conv-sidebar-toggle" id="conv-sidebar-toggle">‹ 或 › 图标</button>
</div>
```

- `.conv-sidebar` 宽度 `240px ⇄ 0`（`transition: width`）由 `.collapsed` 类切换；拉手按钮**是 `.conv-sidebar` 的兄弟节点**、不是子节点——若塞进 `.conv-sidebar` 内部会被它的 `overflow: hidden` 裁掉，收起后彻底看不见、拉不回来。
- 拉手固定 `position: absolute; right: -13px`（相对 wrap 定位），宽度收起到 0 时会自然移动到侧栏与主聊天区的分界线上，视觉上像一个贴边小拉杆。
- 图标方向：CSS 用 `.conv-sidebar.collapsed + .conv-sidebar-toggle svg { transform: rotate(180deg) }`（同一个 `<polyline>` 左箭头旋转 180° 变右箭头），**不是**两套图标切换。
- 状态持久化：`localStorage.setItem('convSidebarCollapsed', '0'|'1')`，页面加载时读取并应用，刷新后记住上次的收起/展开状态。

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

#### 对话内文件上传（回形针）

对话输入框右侧（发送按钮旁）有回形针按钮，用于在聊天界面直接上传文件让助手读取。两端实现一致：

- **复用 `/upload`**：点击回形针 → 选择文件（`accept=".txt,.md,.rst,.html,.pdf,.docx"`）→ `POST /upload`（FormData，无需改后端）。文件落 `DOCS_DIR`，与「上传资料」页同一套；上传成功后调 `checkKbStatus()` 刷新知识库状态。
- **每标签一份待发附件**：状态存 tab（`web/user.html` 的 `t.attachment={filename}`、`ChatPage.tsx` 的 `TabState.attachment`），切标签互不干扰；输入框上方渲染附件 chip（含 ✕ 移除）。
- **附件并入下一条消息**：发送时把文件名拼进发往后端的 `message`——有正文时追加「📎 附件文件：<名>（如需文件内容，请用 read_document 读取）」，无正文时用「请阅读并总结我上传的文件：<名>」。空输入但有附件也允许发送。发送后清空 `attachment`。
- **闭环**：消息里的文件名 + `.pdf/.docx` 等会命中 `文档读取` skill（触发词含「附件」「上传的文件」「pdf」「docx」等），且 `read_document` 工具在网页端 `WEB_TOOLS` 中常驻可用，模型据此读取整篇原文作答。**纯前端改动，后端零改动。**

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

`ChatPage.tsx` 采用 **双栏** 布局，整体占满 `h-full`，主对话区顶部新增**并行对话标签栏**：

```
┌──────────────────────────────────────────────────────────────────┐
│ aside 224px（对话历史侧边栏）›│  主对话区（flex: 1）              │
│  ─ [+ 新对话] 按钮          │  ─ 标签栏（多个并行对话 tab + ➕） │
│  ─ 分组标签                 │  ─ Topbar（标题 + 清空按钮）      │
│    今天 / 昨天 /            │  ─ Info banner（可关闭）           │
│    最近7天 / 更早           │  ─ 消息流（overflow-y-auto）       │
│  ─ 会话条目                 │  ─ 反馈栏（可折叠，仅有消息时显示）│
│    (悬停：重命名 / 删除)    │  ─ KB状态 + 输入框                 │
└──────────────────────────────────────────────────────────────────┘
```

**抽拉式侧栏**（与 `web/user.html` 同一模式，Tailwind 实现）：`<aside>` 外包一层 `<div className="relative flex-shrink-0 flex">`（不裁剪），`<aside>` 自身用 `transition-[width]` 在 `w-56 ⇄ w-0`（配 `border-r-0`）间切换；拉手 `<button>` 是 `<aside>` 的**兄弟节点**、`absolute -right-[13px]` 定位，图标用 `lucide-react` 的 `ChevronLeft`/`ChevronRight` 按 `convSidebarCollapsed` 状态条件渲染（两个真实图标互换，不是旋转同一个）。状态存 `useState(() => localStorage.getItem('convSidebarCollapsed') === '1')`，`useEffect` 里回写 `localStorage`，与用户端的 key 同名但各自独立 storage（不同源不共享）。

### 多对话并行（tabs）

`ChatPage.tsx` 不再是单一对话状态，而是 **每个并行对话一个 tab**：

```ts
interface TabState {
  tabId: string
  convId?: string
  convUserId?: number
  title: string
  sub: string
  input: string
  messages: MsgItem[]
  streaming: boolean
  thinking: boolean
  feedbackOpen: boolean
  feedbackExpanded: boolean
  feedbackComment: string
  rating: number
}

const [tabs, setTabs] = useState<Record<string, TabState>>(...)
const [tabOrder, setTabOrder] = useState<string[]>(...)
const [activeTabId, setActiveTabId] = useState<string>(...)
```

- 所有原来的单值 state（`messages`/`convId`/`streaming`/`input`/反馈相关）都迁移到 `TabState`，通过 `updateTab(tabId, updater)` 做函数式更新。
- `abortRefs.current: Record<tabId, () => void>` —— **每个 tab 一个中断函数**，切换 tab **不会** abort 后台流（这是与旧版的关键区别：旧版 `newConversation`/`clearChat`/`loadConversation` 都会 `abortRef.current?.()` 中断当前唯一的流）。
- `sendMessage(tabId)` 绑定具体 tab，SSE 回调全部通过 `updateTab(tabId, ...)` 写回对应 tab，与 `activeTabId` 无关，因此可以同时有多个 tab 处于 `streaming=true`。
- 标签栏：`streaming` 为 true 时 tab 上显示蓝色脉冲圆点；多于一个 tab 时显示关闭按钮（关闭会触发该 tab 的 abort）。
- 侧边栏点击历史会话：先在 `tabs` 中查找 `convId` 是否已打开，已打开则 `setActiveTabId` 直接切换，否则才新建 tab 并 `GET /conversations/<id>`。
- **思考中状态跨 tab 保持**：`TabState`/user.html 的 tab 对象都有 `thinking: boolean` 字段（请求发出到首个 token/done/error/abort 之间为 true，在 finally 中统一置回 false）。`renderTab(tabId)`/React 渲染会依据 `t.thinking` 决定是否显示"Agent 正在思考…"气泡，因此切换 tab 离开再切回时，思考气泡能正确恢复显示，而不是依赖一次性创建、切走就丢失的 DOM 节点。user.html 中 `#thinking-row` 仍是实际 DOM 节点，但其增删只在 `tabId === activeTabId` 时操作；`t.thinking` 才是跨切换保持的真实状态源，`renderTab` 开头会按 `t.thinking` 重新 `appendThinking()`。
- **中断思考/输出（停止生成）**：发送按钮在 `activeTab.streaming` 为 true 时切换为红色停止图标（`Square`），点击调用 `abortRefs.current[activeTabId]?.()`，对应 `AbortController.abort()` 中断 SSE fetch；`Enter` 键在 streaming 时不再触发发送。中断后若已有部分输出，追加 `\n\n*[已停止]*` 标记；finally 块统一清理 `streaming=false` 与 `abortRefs`。user.html 同理，`#send-btn` 在 streaming 时显示 `.icon-stop`（红色方块）并加 `.stopping` class，点击调用 `tabs[activeTabId].abortFn?.()`。
- `readOnly`/`feedbackOpen` 等均改为读 `activeTab.xxx`（`activeTab = tabs[activeTabId]`）。

#### user.html（vanilla JS）的对应实现

`web/user.html` 已按同样思路完成多对话并行改造，思路一致，命名略有差异：

- 全局 `tabs = {}`（`tabId -> {tabId, convId, title, sub, messages, rating, comment, compactCount, streaming, input, abortFn}`）+ `tabOrder = []` + `activeTabId`，用 `createTab(opts)` 新建。
- 共享 DOM（`#chat-body`/`#chat-title`/`#chat-input`/反馈栏/侧栏）通过 `renderTab(tabId)` 整体重渲染当前激活 tab 的状态；`renderTabBar()` 渲染 `#chat-tabs` 标签栏（streaming 中显示 `.tab-dot` 脉冲点，多于一个 tab 时显示关闭按钮）。
- `doSend()` 在调用时捕获 `tabId = activeTabId`，所有 SSE 回调（`token`/`done`/`auto_compacted`/`conversation_saved`/`error`）写入 `tabs[tabId]`；仅当 `tabId === activeTabId` 时才同步操作共享 DOM（通过 `[data-msg-id]` 定位/更新消息气泡）。因此可以多个 tab 同时 `streaming=true`。
- 每个 tab 有独立的 `abortFn`（`AbortController`），`closeTab` 会调用它中断该 tab 的流；切换 tab 不会中断后台流。
- **发送按钮即停止按钮**：`#send-btn` 在 `streaming` 时切到红色"停止"态（`.stopping` + 切 `.icon-stop`），点击触发 `abortFn()`。⚠️ `doSend()` 启动流式时**必须把按钮设为可点的停止态**（`disabled=false` + 加 `stopping`），不能 `disabled=true`——否则同一 tab 流式期间按钮被禁用，用户无法中断（切 tab 时的 `renderTab` 复位逻辑此时不会触发）。`finally` 块负责复位为发送态。
- `switchConversation(id)` 先在 `tabs` 中查找该 `convId` 是否已打开，已打开则 `switchTab` 直接切换，否则 `createTab` 新建并 `GET /conversations/<id>`。
- 压缩提示计数 `_compactCounts` 由 `convId` 为 key 改为 `tabId` 为 key（`_getCount(tabId)` / `_setCount` / `_incCount`），随 tab 关闭自然释放。
- HTML 中新增 `<div class="chat-tabs" id="chat-tabs"></div>`（位于 `.chat-header` 之前），CSS 新增 `.chat-tabs` / `.chat-tab` / `.tab-dot` / `.tab-close` / `.chat-tab-new` 等样式（含 `tab-pulse` 脉冲动画）。

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
`conversation_saved` 事件携带 `title` 时同步更新对应 tab 的标题（注意是 `updateTab(tabId, ...)`，不是全局 state）：
```ts
} else if (evt.type === 'conversation_saved') {
  updateTab(tabId, t => ({ ...t, convId: evt.conversation_id, title: evt.title || t.title }))
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
// 只读条件：当前激活 tab 的会话 user_id ≠ 自己的 user_id
const readOnly = !!activeTab?.convUserId && myUserId !== undefined && activeTab.convUserId !== myUserId
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
