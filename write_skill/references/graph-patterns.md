# LangGraph 节点 / 子图模式

## 现有两张图

### 清洗子图 `agent_service/graph/cleaning/`

```
cleaning/
├── nodes.py     # read_file_node, clean_node
├── edges.py     # route_input, after_read, after_clean
└── build.py     # build_cleaning_graph()
```

state：`CleaningState{file_path?, raw_text?, system_prompt, cleaner_cfg, cleaned_text, error}`

**调用方式（同步 invoke）**：
```python
from agent_service.graph import build_cleaning_graph

out = build_cleaning_graph().invoke({
    "raw_text": "...",                 # 或 file_path
    "system_prompt": SYS_PROMPT,
    "cleaner_cfg": services.load_cleaner_settings(),
})
if out.get("error"):
    # handle
else:
    cleaned = out["cleaned_text"]
```

### QA 主图 `agent_service/graph/qa/`

```
qa/
├── prompts.py   # EXTRACT_SYSTEM, PLAN_SYSTEM, PLAN_INJECT_PREFIX, build_generate_system()
├── nodes.py     # call_tools_node, extract_keywords_node, retrieve_node, generate_node, plan_node, agent_react_node
├── edges.py     # after_extract (rag_fn None → skip retrieve)
└── build.py     # build_qa_graph(agent_mode)
```

图流程（由 feature flag `agent_mode` 决定，`build_qa_graph(agent_mode)`）：
- **single**（默认）：`START → call_tools → extract_keywords → retrieve? → generate → END`
- **react**：`START → extract_keywords → retrieve? → plan → agent_react → END`（`agent_react_node` 用 `create_react_agent` 多步自主工具循环，预检索作 grounding，`max_tool_rounds`（`api/agent.py` 网页端传 15；节点默认 5），`astream_events(v2)` 映射回 SSE）

**plan 节点（先列方案再执行）**：仅 react 形态含 `plan` 节点，位于 retrieve 与 agent_react 之间。它**自身按 `state['enable_planning']` 门控**——未启用时直接 `return {"plan": ""}`、不调 LLM、不推事件；启用时单次 LLM 调用产出一份执行方案（任务拆分），流式推 `plan_start`/`plan_token`/`plan_end`，方案全文写入 `state['plan']`。`agent_react_node` 把 `state['plan']` 经 `PLAN_INJECT_PREFIX` 追加到 system prompt 末尾作执行指令。**方案不写入持久化历史**（仅当轮指令，不污染压缩预算）。开关：`services.get_plan_first()`（env `PLAN_FIRST` > config.yaml 顶层 `enable_planning` > False），与 `agent_mode` 正交；仅 react 生效，single 图无 plan 节点。飞书路径不传 `enable_planning`（默认 False），故不列方案。

切换：`services.get_agent_mode()`（env `AGENT_MODE` > config.yaml 顶层 `agent_mode` > `single`）。网页端 `api/agent.py` 与飞书降级 `lark_bot._query()` 都按此 flag 编译图。

state：`ChatState{query, history, chat_cfg, rag_fn, top_k, score_threshold, skill_system_prompt, skill_table, web_tools, agent_mode, max_tool_rounds, enable_planning, tool_results, keywords, hits, plan, full_text, error}`

**调用方式（流式 stream）**：
```python
from agent_service.graph import build_qa_graph
from agent_service.skill_loader import detect_skill, build_skill_table

skill = detect_skill(message)

state = {
    "query": message,
    "history": history,               # [{role, content}, ...]
    "chat_cfg": chat_cfg,
    "rag_fn": rag_fn,                 # callable (q, k) -> list
    "top_k": top_k,                   # 请求体 top_k 优先，缺省兜底 cfg.top_k（config.yaml）

    "score_threshold": 0.3,
    "skill_system_prompt": skill.system_prompt if skill else "",
    "skill_table": build_skill_table(),
}

for event in build_qa_graph().stream(state, stream_mode="custom"):
    # event 直接就是节点 writer() 推出的 dict
    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
```

## generate 系统提示组装（`prompts.py: build_generate_system`）

`generate_node` / `agent_react_node` 调 `build_generate_system(skill_prompt, context, tool_results, skill_table, has_hits, tool_table)` 组装 system prompt。**采用 key:value 分段结构**：每段渲染为 `<key>:\n<内容>`（由 `_section()` 生成），段间空行分隔。段顺序与来源：

1. **`identity`**：命中 skill 时用 `skill_prompt`（L2 body）作角色；未命中用 `_IDENTITY_GENERIC`（通用政策顾问 + 行为原则）。
2. **`knowledge`**（常驻）：`skill_table` —— `skill_loader.build_skill_table()`（L1 知识领域表，从 state 传入）。
3. **`tools`**（常驻）：`tool_table` —— `builtin_tools.build_tool_table()`（`generate_node` 内 lazy import）+ 下载按钮等调用约定。
4. **`workspace`**：`has_hits` 时填入 `context`（RAG 片段）；否则填降级策略 `_WORKSPACE_NO_HITS`。
5. **`memory`**（条件）：`tool_results` 非空时追加（single 模式；react 走循环内 ToolMessage，故 react 传空 → 无此段）。
6. **`plan`**（条件，仅 react + enable_planning）：`agent_react_node` 把 `state['plan']` 经 `PLAN_INJECT_PREFIX`（已是 `plan:` 段）追加到末尾。

> 加新内置工具会自动出现在 `tools` 段，无需改提示词；新增 skill 自动进 `knowledge` 段。两张表都不进 `call_tools_node`（那里靠 `bind_tools` 暴露工具），只进 generate 的 system prompt。改段名/新增段时，`prompts.py` 的模块 docstring 也要同步。

## 节点推自定义事件的方式

QA 图节点用 `get_stream_writer()`，从节点内部推 SSE 事件给外层。需要 langgraph >= 0.2.34。

```python
from langgraph.config import get_stream_writer

def extract_keywords_node(state):
    writer = get_stream_writer()
    writer({"type": "tool_start", "name": "提取关键词"})
    # ...do work...
    keywords = "..."
    writer({"type": "tool_end", "name": "提取关键词", "keywords": keywords})
    return {"keywords": keywords}
```

外层 `graph.stream(state, stream_mode="custom")` 直接拿到这些 dict。

## 加节点的步骤（以"加入 rerank 节点到 QA 图"为例）

1. 在 `qa/nodes.py` 加 `rerank_node(state) -> ChatState`，签名同其他节点；用 writer 推 `rerank_start` / `rerank_end`
2. 在 `qa/edges.py` 加路由函数（如果是条件边）
3. 改 `qa/build.py`：
   ```python
   g.add_node("rerank", rerank_node)
   g.add_edge("retrieve", "rerank")     # 替换原来的 retrieve→generate
   g.add_edge("rerank", "generate")
   ```
4. 如果新事件类型，去 `web/user.html`（用户端）+ `web-admin/src/pages/ChatPage.tsx`（管理员端 React）的 SSE 消费者里加 `else if (evt.type === "rerank_start") {...}`

## 加子图的步骤

加一张新图（比如"摘要图"）：

1. `agent_service/graph/<name>/` 新建目录
2. 写 `nodes.py` / `edges.py` / `build.py` / `__init__.py`，照 cleaning 的样子
3. 在 `agent_service/graph/state.py` 加新 TypedDict（不要和现有 state 复用）
4. 在 `agent_service/graph/__init__.py` 导出 `build_<name>_graph`

## 加内置工具的步骤

内置工具供 `call_tools_node` 使用，LLM 在每次对话开始时按需调用（工具调用在 extract_keywords 之前执行）。

1. 在 `agent_service/mcp/builtin_tools.py` 加 `@tool` 函数：
   ```python
   @tool
   def my_tool(param: str) -> str:
       """工具说明（LLM 根据这段描述决定是否调用）。

       Args:
           param: 参数说明
       Returns:
           结果说明
       """
       # 实现
       return result
   ```
2. 追加到对应工具集（两套）：
   ```python
   # 核心集：网页端 + 飞书 QA 降级共用
   BUILTIN_TOOLS = [get_current_time, load_policy_file, generate_word_document, my_tool]
   # 网页端专属集 = 核心 + 文档读取工具；仅用户端/管理员端启用
   WEB_TOOLS = BUILTIN_TOOLS + [read_document, list_documents]
   ```
   - 全局通用（飞书也要）→ 加 `BUILTIN_TOOLS`；仅网页端 → 只加 `WEB_TOOLS`。
   - 区分靠 `ChatState.web_tools` 标志：`api/agent.py` 构建 state 时置 `True`；飞书路径（`lark_bot._query()` 两条）都不带，故只拿核心集。
3. 工具内 import `api.services` / `agent_service` 模块时必须做**函数内 lazy import**（避免循环依赖）：
   ```python
   def my_tool(...):
       from agent_service import SKILLS_ROOT   # 函数内 import，OK
       ...
   ```
4. 不需要改 `call_tools_node` / `generate_node`：两者都按 `state.get("web_tools")` 选 `WEB_TOOLS`/`BUILTIN_TOOLS`，自动 `bind_tools` 并把清单注入 `build_tool_table(tools)`。
5. 要给**飞书 MCP 路径**也用 → 还需在 `builtin_mcp_server.py` 加 `@mcp_server.tool()` 转发（仅网页端工具**不要**转发）。
6. 工具结果会作为 `memory:` 段拼入 generate_node 的 system prompt，格式：
   ```
   memory:
   以下是本轮工具调用返回的内容…：
   <tool_name>: <result_text>
   ```

**`load_policy_file` 的特殊约定**：只有在 `skill_system_prompt` 里存在文档地图时模型才会调用它。文档地图必须包含 `skill_name`（等于 skill 目录名）和 `filename`（含 `.md` 后缀）。

- 路径解析：先 `SKILLS_ROOT/<skill>/references/<file>`，再回退 `SKILLS_ROOT/<skill>/<file>`（**支持读 skill 根目录的 `SKILL.md`**）。
- `filename` 会先 `Path(filename).name` 取末段——既**自动剥掉 `references/` 前缀**（文档地图里常带），又防目录穿越。
- docstring 已明确「文档地图已在系统提示中，无需再读 SKILL.md」，避免模型为拿地图而去 `load_policy_file("SKILL.md")` 扑空（详见 common-pitfalls #39）。

## 节点写作约定

- **纯函数**：不直接调 `chromadb.add`，不写文件；副作用留给 api 层
- **错误处理**：失败时 `return {"error": "..."}`，不抛异常（除非 edges 想用 try 路由）
- **返回值是 partial state**：只返回这个节点改的字段，langgraph 自动合并
- **LLM 客户端在节点内构建**：从 state 拿 `chat_cfg` / `cleaner_cfg`，不要用 module-level 全局
- **流式事件命名**：`<动作>_start` / `<动作>_end`，与现有 `tool_start` / `tool_end` 风格一致

## 边路由约定

```python
def after_something(state) -> str:
    if state.get("error"):
        return "end"            # 字符串要在 add_conditional_edges 的 map 里出现
    return "next_node"
```

`build.py` 里：
```python
g.add_conditional_edges(
    "current_node",
    after_something,
    {"next_node": "next_node", "end": END},
)
```

## 常见陷阱

- **`get_stream_writer` 找不到**：langgraph < 0.2.34，升级 `pip install -U "langgraph>=0.2.34"`
- **同步 invoke 看不到 writer 事件**：必须用 `stream(state, stream_mode="custom")`
- **TypedDict 字段类型不匹配**：state 字段定义为 `Optional[Dict]` 而不是 `Dict = {}`，后者在 frozen=True 时哈希失败（虽然现在 state 不是 frozen，但养成习惯）
- **节点内 import 顶层模块循环**：节点要 import `services` / `conversations` 时做函数内 lazy import
