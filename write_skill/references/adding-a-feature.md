# 加新功能 — 端到端 recipe

## 通用思考清单

加任何新功能前，先回答这 5 个问题：

1. **是配置还是行为？** 配置走 `config.yaml` + settings 抽屉；行为走蓝图 / 图节点。
2. **要不要持久化？** 走 `agent_service/<新目录>/<id>.json` 或扩 chroma 集合。
3. **要不要 SSE？** 短 / 同步用 JSON 返回；长 / 多阶段用 SSE，照协议加事件类型。
4. **要不要 RAG / Chat / Cleaner / Reranker？** 直接 `services.get_*()`，不要重新建。
5. **前端是用户端还是管理员端？** 用户端无参数；管理员端可以露各种调试钮。

## Recipe A：加一个新 API 路由（最常见）

### A1. 选 / 建蓝图
- 涉及检索 / 文件 / chroma → `api/knowledge.py`
- 涉及 chat / 反馈 → `api/agent.py`
- 涉及配置 → `api/settings.py`
- 涉及会话 → `api/conversations.py`
- 涉及用户认证 / 账号 → `api/auth.py`
- 涉及用户管理（列表 / 编辑 / 封禁） → `api/users.py`（仅在 `app_admin.py` 注册）
- 其他业务 → 新建 `api/<name>.py` + 在 **`app_user.py` 和 `app_admin.py`** 都注册（`app.py` 只是 shim，不要改它）

### A2. 写函数
```python
@bp.route("/<my-path>", methods=["POST"])
def my_handler():
    data = request.get_json(silent=True) or {}
    # 1) 校验
    if not data.get("foo"):
        return jsonify({"error": "foo 不能为空"}), 400
    # 2) 取依赖
    cfg = services.load_chat_settings()         # 或其他段
    if not cfg["api_key"]:
        return jsonify({"error": "未配 chat key"}), 400
    # 3) 业务（调图 / 调 services / 直接处理）
    try:
        result = do_work(...)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(result)
```

### A3. （可选）SSE
照 `api-protocols.md` 的模板。

### A4. 测
```bash
curl -X POST http://127.0.0.1:5001/<my-path> -H "Content-Type: application/json" -d '{"foo":"bar"}'
```

## Recipe B：加一个 LLM 调用步骤

**永远 通过清洗子图或 QA 图，不要直接 `openai.OpenAI()` 调**（除非纯粹一次性的兼容调用，比如 settings 连通测试）。

### 场景：在 /feedback 之外加另一种"对话整理"路径

1. 在 `api/agent.py` 顶部加新 system prompt：
   ```python
   _ANOTHER_SYSTEM = "..."
   ```
2. 加路由：
   ```python
   @bp.route("/another", methods=["POST"])
   def another():
       data = request.get_json(silent=True) or {}
       raw = data.get("raw", "")
       cleaner_cfg = services.load_cleaner_settings()
       out = build_cleaning_graph().invoke({
           "raw_text": raw,
           "system_prompt": _ANOTHER_SYSTEM,
           "cleaner_cfg": cleaner_cfg,
       })
       if out.get("error"):
           return jsonify({"error": out["error"]}), 500
       return jsonify({"cleaned": out["cleaned_text"]})
   ```
3. 完事。**不需要新建子图。**

## Recipe C：加一个 settings 字段

参见 `settings-encryption.md` "加新配置字段" 节。

## Recipe D：加一个前端 SSE 事件类型

1. 后端 yield 新事件，比如 `{type: "my_event", payload: ...}`
2. `web/user.html` 或 `admin/chat.html` 的 SSE 消费循环加分支：
   ```js
   else if (evt.type === "my_event") {
     // 渲染逻辑
   }
   ```
3. 如果是会话事件（不属于本轮回答），考虑做成 badge（参考 `appendCompactBadge`）

## Recipe E：加一个会话维度的功能（比如"导出会话"）

1. `api/conversations.py` 加路由：
   ```python
   @bp.route("/conversations/<cid>/export", methods=["GET"])
   def export_conv(cid):
       conv = load_conversation(cid)
       if conv is None: return jsonify({"error":"..."}), 404
       md = render_markdown(conv)
       return Response(md, mimetype="text/markdown",
           headers={"Content-Disposition": f"attachment; filename={cid}.md"})
   ```
2. 前端会话项的右键 / 按钮触发 `window.location = "/conversations/<id>/export"`

## Recipe F：加一个 RAG 增强能力

加新 source 类型 / 加 query 扩展 / 加结果后处理，都先看 `api/services.py:apply_source_weights()` 模式：在 hits 上后置修饰，不要改 chroma collection 结构。

如果非要动 chroma metadata schema，记得：
- 旧数据没新字段 → 用 `.get("new_field", default)` 兜底
- 提示用户 `/vectordb/clear` 重建（如果数据迁移代价高）

## Recipe G：加新的 langgraph 节点

参见 `graph-patterns.md` "加节点的步骤"。

## 测试约定

项目当前**没有自动化测试**。修改后：
1. `python -m api.app` 启动
2. 手动跑相关流程（上传 / 提问 / 反馈）
3. 看浏览器 DevTools 的 Network 看 SSE 事件流
4. 修复后必要时给用户列出"端到端验证清单"

## 提交前自检

- [ ] 改了 settings → settings 抽屉对应字段也加了？
- [ ] 加了 SSE 事件 → 前端有消费分支？
- [ ] 涉及 RAG 数据 → `invalidate_rag()` 调了？
- [ ] 涉及 api_key → 走 `services.load_*_settings()` 不直接读 yaml？
- [ ] 路径用 `DOCS_DIR` / `WIKI_DIR` / `CONVERSATIONS_DIR` 这些常量？动态 wiki 目录用 `services.get_wiki_dir()` 而非裸 `WIKI_DIR`？
- [ ] 中文 SSE → `ensure_ascii=False` 加了？
- [ ] 加新蓝图 → 在 **`app_user.py` 和 `app_admin.py`** 的 `create_app()` 都注册了（不是 `app.py`）？
- [ ] 涉及 MySQL users 表 schema 变更 → `api/auth.py` 和 `api/users.py` 的读写 SQL 同步更新了？
- [ ] 调用 `services.save_settings()` 的地方 → 改为解包二元组 `masked, embedding_changed = services.save_settings(...)`？
- [ ] 加了新的需要登录才能访问的页面路由 → session 校验逻辑加了（参考 `app_user.py` / `app_admin.py` 现有路由）？

## 不要做的事

- ❌ 不要在节点 / 图里直接读写 chroma / 文件 —— 副作用留给 api 层
- ❌ 不要绕过 settings 直接 `openai.OpenAI(api_key=os.getenv(...))` —— 永远走 `services.load_*_settings()`
- ❌ 不要新建第三套加密方案 —— 用 `agent_service.security`
- ❌ 不要为单次小修改"顺手重构"周边代码 —— 提案改动越小越好
- ❌ 不要写新的 README.md / 注释文档 —— 除非用户明确要求；改动直接落地
