"""向量数据库管理：ChromaDB 封装"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from loguru import logger

from src.core.config import config
from src.knowledge.embedding import create_embedding_function


class VectorStoreManager:
    COLLECTIONS = {
        "factor_research": "因子研究知识库",
        "strategy_patterns": "策略模式知识库",
        "market_regimes": "市场环境知识库",
        "academic_papers": "学术论文摘要库",
        "trading_rules": "交易规则与纪律",
        "risk_management": "风险管理知识",
        "strategy_history": "历史策略存档",
    }

    def __init__(self, persist_path=None, embedding_mode: str | None = None):
        self.persist_path = Path(persist_path or config.knowledge.chroma_persist_path)
        self.persist_path.mkdir(parents=True, exist_ok=True)
        self.embedding_fn = create_embedding_function(
            embedding_mode or config.knowledge.embedding_mode,
            config.knowledge.embedding_model,
        )
        self.client = None
        self.collections = {}
        try:
            import chromadb

            self.client = chromadb.PersistentClient(path=str(self.persist_path))
            self._init_collections()
        except Exception as e:
            logger.warning(f"ChromaDB 不可用 ({e})，知识库退化为内存模式")
            self.client = None

    def _init_collections(self):
        for name in self.COLLECTIONS:
            try:
                self.collections[name] = self.client.get_or_create_collection(
                    name=name, embedding_function=self.embedding_fn
                )
            except Exception as e:
                logger.warning(f"初始化集合 {name} 失败: {e}")

    @property
    def available(self) -> bool:
        return self.client is not None

    def add_knowledge(self, collection_name: str, docs, metadatas=None, ids=None):
        """向指定集合添加文档"""
        if collection_name not in self.collections or not docs:
            return
        if ids is None:
            ids = [
                f"{collection_name}_{i}_{hash(doc) % 100000}"
                for i, doc in enumerate(docs)
            ]
        self.collections[collection_name].upsert(
            documents=list(docs),
            metadatas=metadatas or [{"source": "seed"} for _ in docs],
            ids=ids,
        )

    def query(self, query_text, collection_names=None, n_results=5, min_similarity=0.0):
        results = {}
        names = collection_names or list(self.collections.keys())
        for name in names:
            if name not in self.collections:
                continue
            try:
                query_result = self.collections[name].query(
                    query_texts=[query_text],
                    n_results=n_results,
                    include=["documents", "metadatas", "distances"],
                )
            except Exception as e:
                logger.debug(f"集合 {name} 查询失败: {e}")
                continue
            formatted = []
            doc_ids = query_result["ids"][0] if query_result.get("ids") else []
            distances = query_result["distances"][0] if query_result.get("distances") else []
            documents = query_result["documents"][0] if query_result.get("documents") else []
            for i in range(len(doc_ids)):
                distance = distances[i]
                similarity = 1 - distance
                if similarity >= min_similarity:
                    formatted.append(
                        {
                            "document": documents[i],
                            "similarity": round(similarity, 4),
                        }
                    )
            if formatted:
                results[name] = formatted
        return results

    def query_combined(self, query_text, n_results=10, min_similarity=0.3) -> str:
        """合并查询结果，返回格式化文本用于 LLM prompt"""
        all_results = self.query(
            query_text, n_results=n_results, min_similarity=min_similarity
        )
        if not all_results:
            return "（未找到相关知识）"
        parts = []
        for name, docs in all_results.items():
            desc = self.COLLECTIONS.get(name, name)
            parts.append(f"\n### {desc}")
            for i, doc in enumerate(docs[:3], 1):
                parts.append(
                    f"{i}. [相似度:{doc['similarity']:.2f}] {doc['document'][:500]}"
                )
        return "\n".join(parts)

    def count(self, collection_name: str) -> int:
        if collection_name not in self.collections:
            return 0
        try:
            return self.collections[collection_name].count()
        except Exception:
            return 0
