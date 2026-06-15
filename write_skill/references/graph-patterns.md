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
├── prompts.py   # EXTRACT_SYSTEM, build_generate_system()
├── nodes.py     # call_tools_node, extract_keywords_node, retrieve_node, generate_node
├── edges.py     # after_extract (rag_fn None → skip retrieve)
└── build.py     # build_qa_graph()
```

图流程：`START → call_tools → extract_keywords → retrieve? → generate → END`

state：`ChatState{query, history, chat_cfg, rag_fn, top_k, score_threshold, skill_system_prompt, skill_table, keywords, hits, tool_results, full_text, error}`

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
    "top_k": 5,
    "score_threshold": 0.3,
    "skill_system_prompt": skill.system_prompt if skill else "",
    "skill_table": build_skill_table(),
}

for event in build_qa_graph().stream(state, stream_mode="custom"):
    # event 直接就是节点 writer() 推出的 dict
    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
```

## generate 系统提示组装（`prompts.py: build_generate_system`）

`generate_node` 调 `build_generate_system(skill_prompt, context, tool_results, skill_table, has_hits, tool_table)` 组装 system prompt，顺序：

1. **角色/片段层**：按 (是否命中 skill) × (RAG 是否命中) 四情形选模板。
2. **系统可用能力块（常驻，所有情形都注入）**：`CAPABILITIES_PREFIX` 拼入
   - `skill_table` —— `skill_loader.build_skill_table()`（L1 知识领域表，从 state 传入）
   - `tool_table` —— `builtin_tools.build_tool_table()`（内置工具清单，`generate_node` 内 lazy import 获取）
3. **工具结果层**：若 `tool_results` 非空，追加 `TOOL_RESULTS_PREFIX` + 结果。

> 加新内置工具会自动出现在 tool_table，无需改提示词；新增 skill 自动进 skill_table。两张表都不进 `call_tools_node`（那里靠 `bind_tools` 暴露工具），只进 generate 的 system prompt。

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
2. 将函数追加到 `BUILTIN_TOOLS`：
   ```python
   BUILTIN_TOOLS = [get_current_time, load_policy_file, my_tool]
   ```
3. 工具内 import `api.services` / `agent_service` 模块时必须做**函数内 lazy import**（避免循环依赖）：
   ```python
   def my_tool(...):
       from agent_service import SKILLS_ROOT   # 函数内 import，OK
       ...
   ```
4. 不需要改 `call_tools_node`：节点自动遍历 `BUILTIN_TOOLS` 列表执行工具。
5. 工具结果会通过 `TOOL_RESULTS_PREFIX` 拼入 generate_node 的 system prompt，格式：
   ```
   ──── 工具查询结果 ────
   <tool_name>: <result_text>
   ```

**`load_policy_file` 的特殊约定**：只有在 `skill_system_prompt` 里存在文档地图时模型才会调用它。文档地图必须包含 `skill_name`（等于 skill 目录名）和 `filename`（含 `.md` 后缀）。

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
