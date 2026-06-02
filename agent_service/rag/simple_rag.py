import json
import re
import time
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, Union

import os
import yaml
import chromadb
from chromadb.config import Settings
from rank_bm25 import BM25Okapi
from sklearn.feature_extraction.text import TfidfVectorizer

try:
    from sentence_transformers import SentenceTransformer
except ImportError:  # pragma: no cover
    SentenceTransformer = None

# 新版 OpenAI 客户端
try:
    from openai import OpenAI as OpenAIClient
    OPENAI_AVAILABLE = True
except Exception:
    OPENAI_AVAILABLE = False

# 阿里云百炼 DashScope
try:
    import dashscope
    from dashscope import TextEmbedding
    DASHSCOPE_AVAILABLE = True
except Exception:
    DASHSCOPE_AVAILABLE = False


@dataclass(frozen=True)
class RAGConfig:
    persist_directory: Union[str, Path] = Path("RAG/chroma_persist")
    collection_name: str = "simple_rag"
    embedder_name: str = "all-MiniLM-L6-v2"
    use_sentence_transformers: bool = True
    bm25_weight: float = 0.5
    top_k: int = 5
    bm25_k: int = 8
    vector_k: int = 8
    chunk_size: int = 400
    chunk_overlap: int = 100
    reset_vector_store: bool = False
    source_path: Optional[Union[str, Path]] = None
    allowed_extensions: Tuple[str, ...] = (".txt", ".md", ".rst", ".html")
    # 可选: 'openai' (兼容模式), 'dashscope' (阿里云百炼原生), 或 None (本地)
    api_provider: Optional[str] = None
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    reranker_model: str = "gte-rerank-v2"
    # 分隔符正则列表（优先级从高到低）；None 表示使用 DocumentChunker.DEFAULT_SEPARATORS
    separators: Optional[Tuple[str, ...]] = None
    # 数据源权重表 {source_type: weight}，由 server.py 后置加权使用；None 时全部按 1.0 处理
    source_weights: Optional[Dict[str, float]] = None
    # 三段式 API 配置（chat/cleaner/reranker），由 api/settings 写入，services 层消费
    chat: Optional[Dict[str, str]] = None
    cleaner: Optional[Dict[str, str]] = None
    reranker: Optional[Dict[str, str]] = None
    embedding: Optional[Dict[str, str]] = None

    @classmethod
    def from_dict(cls, data: Dict) -> "RAGConfig":
        data = dict(data)
        # 只保留 dataclass 已知字段，忽略 YAML 中多余的 key，避免 TypeError。
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            print(f"警告: config 中存在未知字段，将被忽略: {sorted(unknown)}")
        data = {k: v for k, v in data.items() if k in known}

        if "allowed_extensions" in data and isinstance(data["allowed_extensions"], list):
            data["allowed_extensions"] = tuple(data["allowed_extensions"])
        if "separators" in data and isinstance(data["separators"], list):
            data["separators"] = tuple(data["separators"])
        if "persist_directory" in data and data["persist_directory"] is not None:
            data["persist_directory"] = Path(data["persist_directory"])
        if "source_path" in data and data["source_path"] is not None:
            data["source_path"] = Path(data["source_path"])
        if "source_weights" in data and data["source_weights"] is not None:
            # 强制 value 为 float，避免 YAML 把 "1" 解析为 int 后续运算出错
            data["source_weights"] = {str(k): float(v) for k, v in data["source_weights"].items()}
        return cls(**data)

    @classmethod
    def load(cls, path: Union[str, Path] = Path("RAG/config.yaml")) -> "RAGConfig":
        path = Path(path)
        if not path.exists():
            return cls()
        with path.open("r", encoding="utf-8") as reader:
            content = yaml.safe_load(reader)
        if not content:
            return cls()
        return cls.from_dict(content)

    def to_dict(self) -> Dict:
        return {
            "persist_directory": str(self.persist_directory),
            "collection_name": self.collection_name,
            "embedder_name": self.embedder_name,
            "use_sentence_transformers": self.use_sentence_transformers,
            "bm25_weight": self.bm25_weight,
            "top_k": self.top_k,
            "bm25_k": self.bm25_k,
            "vector_k": self.vector_k,
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap,
            "reset_vector_store": self.reset_vector_store,
            "source_path": str(self.source_path) if self.source_path is not None else None,
            "allowed_extensions": list(self.allowed_extensions),
            "api_provider": self.api_provider,
            "api_key": self.api_key,
            "api_base": self.api_base,
            "reranker_model": self.reranker_model,
            "separators": list(self.separators) if self.separators is not None else None,
            "source_weights": dict(self.source_weights) if self.source_weights is not None else None,
        }


class DocumentChunker:
    """
    语义感知分块器。
    优先在标题、空行、换行、句末等自然边界处切分，再将小片段合并到 chunk_size，
    块间保留 overlap 字符的上下文重叠。若所有分隔符均无法进一步切分，
    则退化为纯字符截断。
    """

    # 默认分隔符（正则，优先级从高到低）
    DEFAULT_SEPARATORS: List[str] = [
        r"\n(?=#{1,6}[ \t])",   # Markdown 标题前（零宽前瞻，保留 # 符号）
        r"\n\n+",                # 空行 / 段落边界
        r"\n",                   # 换行
        r"(?<=[。！？.!?])\s*",  # 中英文句末
        r"(?<=[，,；;])\s*",     # 逗号 / 分号
    ]

    @classmethod
    def chunk(
        cls,
        text: str,
        chunk_size: int = 400,
        overlap: int = 100,
        separators: Optional[List[str]] = None,
    ) -> List[str]:
        seps = separators if separators is not None else list(cls.DEFAULT_SEPARATORS)
        text = text.strip()
        if not text:
            return []
        if len(text) <= chunk_size:
            return [text]
        atoms = cls._split_recursive(text, seps, chunk_size)
        return cls._merge(atoms, chunk_size, overlap)

    # ── 内部方法 ────────────────────────────────────────────

    @classmethod
    def _split_recursive(cls, text: str, seps: List[str], chunk_size: int) -> List[str]:
        """按分隔符优先级递归切分，返回每段长度 <= chunk_size 的原子片段（尽力）。"""
        if len(text) <= chunk_size or not seps:
            return [text.strip()] if text.strip() else []

        sep, rest = seps[0], seps[1:]
        parts = [p.strip() for p in re.split(sep, text) if p.strip()]

        if len(parts) <= 1:
            # 当前分隔符无效，尝试下一级
            return cls._split_recursive(text, rest, chunk_size)

        result: List[str] = []
        for part in parts:
            if len(part) > chunk_size:
                result.extend(cls._split_recursive(part, rest, chunk_size))
            else:
                result.append(part)
        return result

    @classmethod
    def _merge(cls, atoms: List[str], chunk_size: int, overlap: int) -> List[str]:
        """将原子片段合并成不超过 chunk_size 的 chunk，超出时截断并加入 overlap。"""
        chunks: List[str] = []
        buf = ""

        for atom in atoms:
            atom = atom.strip()
            if not atom:
                continue

            # 单个原子本身超限（_split_recursive 无法进一步切分时），强制截断
            if len(atom) > chunk_size:
                if buf:
                    chunks.append(buf)
                    buf = ""
                start = 0
                while start < len(atom):
                    end = min(start + chunk_size, len(atom))
                    piece = atom[start:end].strip()
                    if piece:
                        chunks.append(piece)
                    if end == len(atom):
                        break
                    start = max(0, end - overlap)
                continue

            candidate = (buf + "\n" + atom).strip() if buf else atom
            if len(candidate) <= chunk_size:
                buf = candidate
            else:
                if buf:
                    chunks.append(buf)
                    tail = buf[-overlap:].strip() if overlap > 0 else ""
                    buf = (tail + "\n" + atom).strip() if tail else atom
                else:
                    buf = atom

        if buf:
            chunks.append(buf)
        return [c for c in chunks if c]


class DocumentLoader:
    @staticmethod
    def load(path: Union[str, Path], config: Optional[RAGConfig] = None) -> Tuple[List[str], List[Dict]]:
        config = config or RAGConfig()
        path = Path(path)
        files = []
        if path.is_dir():
            files = [
                file
                for file in sorted(path.rglob("*"))
                if file.is_file() and file.suffix.lower() in config.allowed_extensions
            ]
        elif path.is_file():
            files = [path]
        else:
            raise ValueError(f"路径不存在: {path}")

        documents: List[str] = []
        metadatas: List[Dict] = []
        for file in files:
            text = file.read_text(encoding="utf-8", errors="ignore").strip()
            if not text:
                continue

            seps = list(config.separators) if config.separators else None
            chunks = DocumentChunker.chunk(text, config.chunk_size, config.chunk_overlap, separators=seps)
            for index, chunk in enumerate(chunks):
                documents.append(chunk)
                metadatas.append(
                    {
                        "source": str(file.relative_to(Path.cwd())),
                        "chunk_id": index,
                        "filename": file.name,
                    }
                )

        return documents, metadatas


class BaseEmbedder:
    def fit(self, texts: List[str]):
        raise NotImplementedError

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        raise NotImplementedError

    def embed_query(self, query: str) -> List[float]:
        raise NotImplementedError


class SimpleEmbedder(BaseEmbedder):
    """基于 TF-IDF 的简单嵌入。"""

    def __init__(self):
        self.vectorizer = TfidfVectorizer(
            lowercase=True,
            stop_words="english",
            norm="l2",
            strip_accents="unicode",
        )

    def fit(self, texts: List[str]):
        self.vectorizer.fit(texts)

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        matrix = self.vectorizer.transform(texts)
        return matrix.toarray().tolist()

    def embed_query(self, query: str) -> List[float]:
        return self.vectorizer.transform([query]).toarray()[0].tolist()


class SentenceTransformerEmbedder(BaseEmbedder):
    """使用 Sentence Transformers 生成更强的语义向量。"""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        if SentenceTransformer is None:
            raise RuntimeError("sentence-transformers 未安装，请先安装它或改用 SimpleEmbedder。")
        self.model = SentenceTransformer(model_name)

    def fit(self, texts: List[str]):
        pass

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        embeddings = self.model.encode(texts, show_progress_bar=False, convert_to_numpy=True)
        return embeddings.tolist()

    def embed_query(self, query: str) -> List[float]:
        embedding = self.model.encode([query], show_progress_bar=False, convert_to_numpy=True)
        return embedding[0].tolist()


class OpenAIEmbedder(BaseEmbedder):
    """使用 OpenAI 兼容的 Embedding API（新版 openai 客户端）。"""

    def __init__(
        self,
        model_name: str = "text-embedding-3-small",
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        batch_size: int = 10,
        timeout: int = 30,
        retries: int = 2,
    ):
        if not OPENAI_AVAILABLE:
            raise RuntimeError("openai 包未安装或无法导入，请安装 openai>=1.0.0")
        self.model_name = model_name
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.api_base = api_base or os.getenv("OPENAI_API_BASE")
        if not self.api_key:
            raise RuntimeError("OpenAI API key 未配置，请在 config 中设置 api_key 或通过 OPENAI_API_KEY 环境变量提供。")
        self.client = OpenAIClient(
            api_key=self.api_key,
            base_url=self.api_base,
            timeout=timeout,
        )
        self.batch_size = batch_size
        self.retries = retries

    def fit(self, texts: List[str]):
        pass

    def _embed_batch_with_retry(self, batch: List[str]) -> List[List[float]]:
        for attempt in range(1, self.retries + 1):
            try:
                resp = self.client.embeddings.create(model=self.model_name, input=batch)
                return [item.embedding for item in resp.data]
            except Exception as e:
                if attempt == self.retries:
                    raise
                print(f"OpenAI embed batch failed (attempt {attempt}), retrying in {attempt}s: {e}")
                time.sleep(attempt)
        return []  # never reached

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        embeddings: List[List[float]] = []
        total = len(texts)
        for i in range(0, total, self.batch_size):
            batch = texts[i : i + self.batch_size]
            batch_embs = self._embed_batch_with_retry(batch)
            embeddings.extend(batch_embs)
        return embeddings

    def embed_query(self, query: str) -> List[float]:
        return self._embed_batch_with_retry([query])[0]


class DashScopeEmbedder(BaseEmbedder):
    """阿里云百炼原生 Embedding API (DashScope)。"""

    def __init__(
        self,
        model_name: str = "text-embedding-v1",
        api_key: Optional[str] = None,
        batch_size: int = 25,
        retries: int = 2,
    ):
        if not DASHSCOPE_AVAILABLE:
            raise RuntimeError("dashscope 包未安装，请安装: pip install dashscope")
        self.model_name = model_name
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        if not self.api_key:
            raise RuntimeError("DashScope API key 未配置，请在 config 中设置 api_key 或通过 DASHSCOPE_API_KEY 环境变量提供。")
        dashscope.api_key = self.api_key
        self.batch_size = batch_size
        self.retries = retries

    def _embed_batch_with_retry(self, batch: List[str]) -> List[List[float]]:
        for attempt in range(1, self.retries + 1):
            try:
                resp = TextEmbedding.call(model=self.model_name, input=batch)
                if resp.status_code != 200:
                    raise RuntimeError(f"DashScope error: {resp.code} - {resp.message}")
                # 返回顺序可能与输入一致，按索引取
                emb_map = {item["text_index"]: item["embedding"] for item in resp.output["embeddings"]}
                return [emb_map[i] for i in range(len(batch))]
            except Exception as e:
                if attempt == self.retries:
                    raise
                print(f"DashScope embed batch failed (attempt {attempt}), retrying in {attempt}s: {e}")
                time.sleep(attempt)
        return []

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        embeddings: List[List[float]] = []
        total = len(texts)
        for i in range(0, total, self.batch_size):
            batch = texts[i : i + self.batch_size]
            batch_embs = self._embed_batch_with_retry(batch)
            embeddings.extend(batch_embs)
        return embeddings

    def embed_query(self, query: str) -> List[float]:
        return self._embed_batch_with_retry([query])[0]

    def fit(self, texts: List[str]):
        pass


class DashScopeReranker:
    """使用 DashScope gte-rerank-v2 对候选文档重排序。通过原生 HTTP API 调用，无需 dashscope SDK。"""

    _URL = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"

    def __init__(
        self,
        model_name: str = "gte-rerank-v2",
        api_key: Optional[str] = None,
        retries: int = 2,
    ):
        self.model_name = model_name
        self.api_key = api_key or os.getenv("OPENAI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
        if not self.api_key:
            raise RuntimeError("Reranker: 未找到 API Key，请配置 OPENAI_API_KEY 或 DASHSCOPE_API_KEY")
        self.retries = retries

    def rerank(self, query: str, candidates: List[Dict], top_n: int) -> List[Dict]:
        """对 candidates（含 'text' 字段的 hit 列表）重排序，返回 top_n 个结果并附加 rerank_score。"""
        if not candidates:
            return []
        try:
            import httpx
        except ImportError:
            raise RuntimeError("httpx 未安装，请执行: pip install httpx")

        texts = [h["text"] for h in candidates]
        n = min(top_n, len(texts))
        payload = {
            "model": self.model_name,
            "input": {"query": query, "documents": texts},
            "parameters": {"return_documents": False, "top_n": n},
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        last_err: Exception = RuntimeError("未知错误")
        for attempt in range(1, self.retries + 1):
            try:
                resp = httpx.post(self._URL, json=payload, headers=headers, timeout=30)
                resp.raise_for_status()
                results = resp.json()["output"]["results"]
                reranked = []
                for r in results:
                    hit = dict(candidates[r["index"]])
                    hit["rerank_score"] = float(r["relevance_score"])
                    reranked.append(hit)
                return reranked
            except Exception as e:
                last_err = e
                if attempt < self.retries:
                    print(f"Reranker 请求失败 (attempt {attempt}), retrying: {e}")
                    time.sleep(attempt)
        raise last_err


class EmbedderFactory:
    @staticmethod
    def create(config: RAGConfig) -> BaseEmbedder:
        if config.api_provider is not None:
            provider = config.api_provider.lower()
            if provider == "openai":
                return OpenAIEmbedder(
                    model_name=config.embedder_name,
                    api_key=config.api_key,
                    api_base=config.api_base,
                )
            elif provider == "dashscope":
                return DashScopeEmbedder(
                    model_name=config.embedder_name,
                    api_key=config.api_key,
                )
            else:
                raise ValueError(f"未知的 api_provider: {provider}，支持: openai, dashscope")

        if config.use_sentence_transformers and SentenceTransformer is not None:
            return SentenceTransformerEmbedder(model_name=config.embedder_name)
        return SimpleEmbedder()


class BM25Retriever:
    """BM25 检索器，负责词项检索。"""

    def __init__(self, documents: List[str]):
        self.documents = documents
        self.tokenized_corpus = [self._tokenize(doc) for doc in documents]
        self.bm25 = BM25Okapi(self.tokenized_corpus)

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        return re.findall(r"\w+", text.lower())

    def retrieve(self, query: str, top_k: int = 5) -> List[Dict]:
        tokens = self._tokenize(query)
        scores = self.bm25.get_scores(tokens)
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:top_k]
        return [
            {
                "doc_id": idx,
                "score": float(score),
                "text": self.documents[idx],
            }
            for idx, score in ranked
        ]


class ChromaVectorStore:
    """Chroma 向量存储，支持持久化。"""

    def __init__(
        self,
        documents: List[str],
        embeddings: List[List[float]],
        metadatas: Optional[List[Dict]] = None,
        config: Optional[RAGConfig] = None,
    ):
        config = config or RAGConfig()
        self.documents = documents
        self.ids = [str(i) for i in range(len(documents))]
        self.metadatas = [
            dict(metadatas[idx]) if metadatas and idx < len(metadatas) else {}
            for idx in range(len(documents))
        ]

        self.persist_directory = Path(config.persist_directory)
        self.persist_directory.mkdir(parents=True, exist_ok=True)
        settings = Settings(is_persistent=True, persist_directory=str(self.persist_directory))
        self.client = chromadb.Client(settings=settings)

        # 始终用本次传入的文档重建集合内容。
        # 这样可以保证向量库里的 doc_id（0..n-1）与 BM25Retriever 使用的下标完全一致，
        # 否则复用旧集合会导致两路检索的 doc_id 错位、结果错误甚至 IndexError。
        collection_names = [col.name for col in self.client.list_collections()]
        if config.collection_name in collection_names:
            self.client.delete_collection(name=config.collection_name)
        self.collection = self.client.create_collection(name=config.collection_name)
        if documents:
            self.collection.add(
                ids=self.ids,
                documents=documents,
                embeddings=embeddings,
                metadatas=self.metadatas,
            )

    def query(self, query_embedding: List[float], top_k: int = 5) -> List[Dict]:
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            include=["distances", "documents", "metadatas"],
        )
        # Chroma 返回的是嵌套结构 [[...]]，空结果时为 [[]]，需判断内层是否为空。
        ids = results.get("ids") or [[]]
        if not ids[0]:
            return []

        hits = []
        for idx, doc_id in enumerate(ids[0]):
            hits.append(
                {
                    "doc_id": int(doc_id),
                    "score": float(1.0 / (1.0 + results["distances"][0][idx])),
                    "text": results["documents"][0][idx],
                    "metadata": results["metadatas"][0][idx],
                    "distance": float(results["distances"][0][idx]),
                }
            )
        return hits


class HybridRetriever:
    """混合检索器：BM25 + 向量检索。"""

    def __init__(
        self,
        documents: List[str],
        metadatas: Optional[List[Dict]] = None,
        config: Optional[RAGConfig] = None,
    ):
        self.config = config or RAGConfig()
        self.documents = documents
        self.metadatas = metadatas or [{} for _ in documents]
        self.bm25_retriever = BM25Retriever(documents)

        # 尝试创建配置的嵌入器，失败则回退到 SimpleEmbedder 并重建向量库
        try:
            self.embedder = EmbedderFactory.create(self.config)
            if not isinstance(self.embedder, SentenceTransformerEmbedder):
                self.embedder.fit(documents)
            embeddings = self.embedder.embed_documents(documents)
            self.vector_store = ChromaVectorStore(documents, embeddings, self.metadatas, config=self.config)
        except Exception as e:
            print(f"主嵌入器失败: {e}\n回退到 SimpleEmbedder 并重建向量存储。")
            fallback = SimpleEmbedder()
            fallback.fit(documents)
            embeddings = fallback.embed_documents(documents)
            self.embedder = fallback
            # ChromaVectorStore 现在总是按当前文档重建集合，
            # 因此回退时无需再手动切换 reset_vector_store，旧的错误维度集合会被自动覆盖。
            self.vector_store = ChromaVectorStore(documents, embeddings, self.metadatas, config=self.config)

    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        bm25_k: Optional[int] = None,
        vector_k: Optional[int] = None,
        bm25_weight: Optional[float] = None,
        reranker: Optional[DashScopeReranker] = None,
    ) -> List[Dict]:
        top_k = self.config.top_k if top_k is None else top_k
        bm25_k = self.config.bm25_k if bm25_k is None else bm25_k
        vector_k = self.config.vector_k if vector_k is None else vector_k
        bm25_weight = self.config.bm25_weight if bm25_weight is None else bm25_weight

        bm25_hits = self.bm25_retriever.retrieve(query, top_k=bm25_k)
        query_embedding = self.embedder.embed_query(query)
        vector_hits = self.vector_store.query(query_embedding, top_k=vector_k)

        combined_scores: Dict[int, Dict] = {}
        for hit in bm25_hits:
            combined_scores[hit["doc_id"]] = {
                "doc_id": hit["doc_id"],
                "text": hit["text"],
                "metadata": self.metadatas[hit["doc_id"]],
                "bm25_score": hit["score"],
                "vector_score": 0.0,
                "hybrid_score": hit["score"] * bm25_weight,
            }

        for hit in vector_hits:
            entry = combined_scores.get(hit["doc_id"])
            if entry is None:
                entry = {
                    "doc_id": hit["doc_id"],
                    "text": hit["text"],
                    "metadata": hit.get("metadata", self.metadatas[hit["doc_id"]]),
                    "bm25_score": 0.0,
                    "vector_score": hit["score"],
                    "hybrid_score": hit["score"] * (1 - bm25_weight),
                }
                combined_scores[hit["doc_id"]] = entry
            else:
                entry["vector_score"] = hit["score"]
                entry["hybrid_score"] += hit["score"] * (1 - bm25_weight)

        # 将所有候选按混合分排序；若启用重排序则送入 reranker，否则截取 top_k
        all_candidates = sorted(combined_scores.values(), key=lambda x: x["hybrid_score"], reverse=True)
        if reranker is not None:
            return reranker.rerank(query, all_candidates, top_n=top_k)
        return all_candidates[:top_k]


def build_simple_rag(
    documents: List[str],
    metadatas: Optional[List[Dict]] = None,
    config: Optional[RAGConfig] = None,
) -> HybridRetriever:
    """构建一个简单的混合检索 RAG 实例。"""
    return HybridRetriever(documents, metadatas, config=config)


def build_rag_from_path(path: Union[str, Path], config: Optional[RAGConfig] = None) -> HybridRetriever:
    """从文件或目录加载文本并构建 RAG。"""
    config = config or RAGConfig()
    documents, metadatas = DocumentLoader.load(path, config=config)
    if not documents:
        raise ValueError(f"未加载到任何文档: {path}")
    return build_simple_rag(documents, metadatas, config=config)


def build_rag_from_config(config_path: Union[str, Path] = Path("RAG/config.yaml")) -> HybridRetriever:
    """从 YAML 配置文件中加载参数并构建 RAG。"""
    config_path = Path(config_path)
    config = RAGConfig.load(config_path)
    if config.source_path is None:
        raise ValueError("config.yaml 中必须设置 source_path，用于加载外部文档。")

    source_path = Path(config.source_path)
    if not source_path.is_absolute():
        source_path = config_path.parent / source_path

    return build_rag_from_path(source_path, config=config)


def format_rag_answer(query: str, hits: List[Dict]) -> str:
    """把检索结果组合成一个可读回答。"""
    pieces = [f"问题: {query}\n"]
    for rank, hit in enumerate(hits, start=1):
        pieces.append(
            json.dumps(
                {
                    "rank": rank,
                    "doc_id": hit["doc_id"],
                    "hybrid_score": hit["hybrid_score"],
                    "bm25_score": hit.get("bm25_score"),
                    "vector_score": hit.get("vector_score"),
                    "metadata": hit.get("metadata"),
                    "text": hit["text"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return "\n".join(pieces)


if __name__ == "__main__":
    config_path = Path(__file__).parent / "config.yaml"
    config = RAGConfig.load(config_path)

    if config.source_path is not None:
        source_path = Path(config.source_path)
        if not source_path.is_absolute():
            source_path = config_path.parent / source_path
        rag = build_rag_from_path(source_path, config=config)
    else:
        sample_docs = [
            "飞书是一款企业协作办公软件，包含消息、日历、文档、会议等功能。",
            "Chroma 是一个轻量级向量数据库，可以用于语义检索和向量索引。",
            "BM25 是一种经典的词项分数检索算法，适合关键词匹配场景。",
            "混合检索可以将关键词检索与向量检索结合，提升搜索质量。",
            "Python 可以用来构建简单的 RAG 系统。",
        ]
        rag = build_simple_rag(sample_docs, config=config)

    query_text = "什么是RAG？"
    results = rag.search(query_text)
    print(format_rag_answer(query_text, results))