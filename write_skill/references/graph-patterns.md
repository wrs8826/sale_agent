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
├── prompts.py   # EXTRACT_SYSTEM, GENERATE_SYSTEM, GENERATE_FALLBACK_SYSTEM
├── nodes.py     # extract_keywords_node, retrieve_node, generate_node
├── edges.py     # after_extract (rag_fn None → skip retrieve)
└── build.py     # build_qa_graph()
```

state：`ChatState{query, history, chat_cfg, rag_fn, top_k, score_threshold, keywords, hits, full_text, error}`

**调用方式（流式 stream）**：
```python
from agent_service.graph import build_qa_graph

state = {
    "query": message,
    "history": history,        # [{role, content}, ...]
    "chat_cfg": chat_cfg,
    "rag_fn": rag_fn,          # callable (q, k) -> list
    "top_k": 5,
    "score_threshold": 0.3,    # 低于该分时忽略命中，用 GENERATE_FALLBACK_SYSTEM 兜底
}

for event in build_qa_graph().stream(state, stream_mode="custom"):
    # event 直接就是节点 writer() 推出的 dict
    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
```

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
4. 如果新事件类型，去 `web/user.html` + `web/admin/chat.html` 的 SSE 消费者里加 `else if (evt.type === "rerank_start") {...}`

## 加子图的步骤

加一张新图（比如"摘要图"）：

1. `agent_service/graph/<name>/` 新建目录
2. 写 `nodes.py` / `edges.py` / `build.py` / `__init__.py`，照 cleaning 的样子
3. 在 `agent_service/graph/state.py` 加新 TypedDict（不要和现有 state 复用）
4. 在 `agent_service/graph/__init__.py` 导出 `build_<name>_graph`

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
