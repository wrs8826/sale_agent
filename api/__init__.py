"""api —— Flask 接口层，把 agent_service 暴露为 HTTP 端点。

模块划分：
    app.py        —— 入口，组装蓝图与静态文件路由
    services.py   —— 共享单例（RAG 缓存、Reranker、Agent 模块）
    knowledge.py  —— 知识库管理蓝图：上传、清洗、检索测试、向量库维护
    agent.py      —— Agent 对话蓝图：流式对话、反馈入库
"""
