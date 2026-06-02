# Simple RAG System

这是一个最小可用的 RAG（Retrieval-Augmented Generation）示例，使用:

- BM25 作为关键词检索层
- Chroma 向量数据库做语义检索
- TF-IDF 向量作为文本嵌入

## 文件

- `simple_rag.py`：核心实现文件
- `requirements.txt`：依赖列表

## 使用方式

1. 安装依赖：

```bash
pip install -r RAG/requirements.txt
```

2. 使用配置文件运行：

```bash
python RAG/simple_rag.py
```

3. 从外部目录加载文档并使用配置文件：

```python
from RAG.simple_rag import build_rag_from_config

rag = build_rag_from_config("RAG/config.yaml")
results = rag.search("你的查询")
```

## 代码结构

- `SimpleEmbedder`：使用 `TfidfVectorizer` 生成文本与查询向量
- `SentenceTransformerEmbedder`：可选使用 `sentence-transformers` 生成更强语义向量
- `BM25Retriever`：使用 `rank_bm25` 进行关键词检索
- `ChromaVectorStore`：使用 `chromadb` 存储与查询向量，支持持久化
- `HybridRetriever`：融合 BM25 与向量检索得分，返回混合排序结果

## 新功能

- 支持从文件或目录加载外部文档
- 支持 Chroma 持久化存储，向量索引可以保存在 `RAG/chroma_persist`
- 支持可选的更强语义嵌入模型：`sentence-transformers`
- 支持外部嵌入提供者（例如 `openai`）。在 `RAG/config.yaml` 中设置 `api_provider` 和 `api_key` 来使用外部服务。

## 使用方式

1. 安装依赖：

```bash
pip install -r RAG/requirements.txt
```

2. 运行示例：

```bash
python RAG/simple_rag.py
```

3. 可选：直接从配置文件中加载外部文档，并使用配置中的检索超参数：

```python
from RAG.simple_rag import build_rag_from_config

rag = build_rag_from_config("RAG/config.yaml")
results = rag.search("你的查询")
```

4. 使用 OpenAI 作为嵌入提供者

在 `RAG/config.yaml` 中设置：

```yaml
api_provider: "openai"
api_key: "<YOUR_OPENAI_KEY>" # 或通过环境变量 OPENAI_API_KEY 提供
embedder_name: "text-embedding-3-small" # OpenAI 嵌入模型名
```

注意：若不想在文件中明文保存 api_key，请将 `api_key` 留空并在环境变量 `OPENAI_API_KEY` 中设置。

## 说明

该实现保持解耦：

- 文本向量化、BM25 检索、向量检索分别为独立模块
- `HybridRetriever` 只负责组合结果
- 可将 `ChromaVectorStore` 替换为其他向量数据库，或将 `SimpleEmbedder` 替换为更强的嵌入模型
