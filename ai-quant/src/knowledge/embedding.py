"""内置轻量 Embedding 实现

基于字符 n-gram 哈希 + 哈希桶，无需下载任何模型即可本地生成固定维度向量。
用于没有联网/不便下载大模型时的兜底方案。可通过 EMBEDDING_MODE=sentence-transformers 切换。
"""
from __future__ import annotations

import hashlib
from typing import List, Sequence, Union

try:
    from chromadb.api.types import EmbeddingFunction, Documents, Embeddings
except ImportError:  # 兜底类型
    class EmbeddingFunction:  # type: ignore
        pass

    Documents = Sequence[str]  # type: ignore
    Embeddings = List[List[float]]  # type: ignore


class HashEmbeddingFunction(EmbeddingFunction):
    def __init__(self, dim: int = 768, ngram: int = 2):
        self.dim = dim
        self.ngram = ngram

    def __call__(self, input: Documents) -> Embeddings:
        return [self._embed_one(doc) for doc in input]

    def _embed_one(self, text: str) -> List[float]:
        text = (text or "").lower()
        vec = [0.0] * self.dim
        # 字符 n-gram 哈希
        for i in range(len(text) - self.ngram + 1):
            gram = text[i : i + self.ngram]
            h = int(hashlib.md5(gram.encode("utf-8")).hexdigest(), 16)
            idx = h % self.dim
            sign = 1.0 if (h >> 8) % 2 == 0 else -1.0
            vec[idx] += sign
        # 简单长度归一化
        norm = sum(v * v for v in vec) ** 0.5
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


def create_embedding_function(mode: str = "builtin", model_name: str = ""):
    """根据配置创建 Embedding 函数"""
    if mode == "sentence-transformers":
        try:
            from chromadb.utils.embedding_functions import (
                SentenceTransformerEmbeddingFunction,
            )

            return SentenceTransformerEmbeddingFunction(
                model_name=model_name or "BAAI/bge-large-zh-v1.5",
                normalize_embeddings=True,
            )
        except Exception:
            pass
    return HashEmbeddingFunction()
