"""
检索器模块
支持向量检索、BM25检索、混合检索三种模式
参考 RAG-cy/src/retrieval.py 适配（去掉按公司名检索，改为按知识类型检索）
"""
import json
import pickle
import logging
import shutil
import tempfile
from pathlib import Path
from typing import List, Dict, Optional
import random

import numpy as np
import faiss

from src.config import get_config
from src.knowledge.tokenizer import tokenize
from src.cache.retrieval_cache import build_key, cache_get, cache_set

_log = logging.getLogger(__name__)
settings = get_config()


def read_faiss_index(faiss_path: Path):
    """
    加载FAISS索引（兼容中文路径）
    FAISS C++层fopen不支持中文路径，先复制到临时文件再读取
    """
    path_str = str(faiss_path)
    try:
        return faiss.read_index(path_str)
    except Exception:
        # 中文路径失败时降级到临时文件方案
        with tempfile.NamedTemporaryFile(suffix=".faiss", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            shutil.copyfile(path_str, tmp_path)
            return faiss.read_index(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)


class BM25Retriever:
    """BM25检索器"""

    def __init__(self, bm25_db_dir: Path, documents_dir: Path):
        self.bm25_db_dir = bm25_db_dir
        self.documents_dir = documents_dir
        # 内存缓存：索引/文档只加载一次，避免每次查询重复反序列化（实测省~0.4s/次）
        # 索引重建后需重启应用才能生效
        self._index_cache = {}
        self._doc_cache = {}

    def _load_document(self, doc_id: str) -> dict:
        """加载指定doc_id的分块文档（带缓存）"""
        if doc_id in self._doc_cache:
            return self._doc_cache[doc_id]
        doc_path = self.documents_dir / f"{doc_id}.json"
        if not doc_path.exists():
            raise ValueError(f"文档不存在: {doc_path}")
        with open(doc_path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        self._doc_cache[doc_id] = doc
        return doc

    def _load_bm25_index(self, doc_id: str):
        """加载指定doc_id的BM25索引（带缓存）"""
        if doc_id in self._index_cache:
            return self._index_cache[doc_id]
        index_path = self.bm25_db_dir / f"{doc_id}.pkl"
        if not index_path.exists():
            raise ValueError(f"BM25索引不存在: {index_path}")
        with open(index_path, "rb") as f:
            index = pickle.load(f)
        self._index_cache[doc_id] = index
        return index

    def retrieve(
        self,
        query: str,
        doc_id: str = None,
        category: str = None,
        top_n: int = 5,
        doc_ids: List[str] = None,
    ) -> List[Dict]:
        """
        检索文本块
        参数:
            query: 查询文本
            doc_id: 指定单个文档ID（可选）
            category: 按类别过滤 (course/project/interview)
            top_n: 返回结果数
            doc_ids: 限定只检索这些文档（分层检索定向召回用）
        返回:
            [{"text": str, "score": float, "source": str, "doc_id": str}, ...]
        """
        results = []

        # 确定要检索的文档
        if doc_id:
            target_doc_ids = [doc_id]
        elif doc_ids:
            target_doc_ids = doc_ids
        else:
            target_doc_ids = self._find_doc_ids(category)

        # 必须与索引构建侧使用同一分词器（jieba），中文无空格不能用split()
        # 分词只做一次，全部文档复用
        tokenized_query = tokenize(query)

        for did in target_doc_ids:
            try:
                document = self._load_document(did)
                bm25_index = self._load_bm25_index(did)
            except ValueError:
                continue

            chunks = document["content"]["chunks"]
            scores = bm25_index.get_scores(tokenized_query)

            actual_top_n = min(top_n, len(scores))
            top_indices = sorted(
                range(len(scores)), key=lambda i: scores[i], reverse=True
            )[:actual_top_n]

            for idx in top_indices:
                # 过滤零分结果：无任何词匹配时不返回无关分块
                if scores[idx] <= 0:
                    continue
                results.append({
                    "text": chunks[idx]["text"],
                    "score": round(float(scores[idx]), 4),
                    "source": document["metainfo"].get("source", ""),
                    "doc_id": did,
                    "chunk_id": idx,
                })

        # 全局排序取top_n
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_n]

    def _find_doc_ids(self, category: str = None) -> List[str]:
        """根据类别查找文档ID"""
        doc_ids = []
        for path in self.documents_dir.glob("*.json"):
            if category:
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        doc = json.load(f)
                    if doc["metainfo"].get("category") == category:
                        doc_ids.append(path.stem)
                except Exception:
                    continue
            else:
                doc_ids.append(path.stem)
        return doc_ids


class VectorRetriever:
    """向量检索器"""

    def __init__(
        self,
        vector_db_dir: Path,
        documents_dir: Path,
        embedding_provider: str = "dashscope",
        embedding_model: str = "text-embedding-v1",
    ):
        self.vector_db_dir = vector_db_dir
        self.documents_dir = documents_dir
        self.embedding_provider = embedding_provider
        self.embedding_model = embedding_model
        # 复用同一个API客户端（连接池保持TLS长连接），避免每次embedding新建连接
        from src.api_client import APIProcessor
        self._embedding_api = APIProcessor(provider=embedding_provider)
        self.all_dbs = self._load_dbs()

    def _load_dbs(self) -> List[Dict]:
        """加载所有向量库和文档映射"""
        all_dbs = []
        for doc_path in self.documents_dir.glob("*.json"):
            try:
                with open(doc_path, "r", encoding="utf-8") as f:
                    document = json.load(f)
            except Exception as e:
                _log.error(f"加载文档失败 {doc_path.name}: {e}")
                continue

            doc_id = document.get("metainfo", {}).get("doc_id", doc_path.stem)
            faiss_path = self.vector_db_dir / f"{doc_id}.faiss"
            if not faiss_path.exists():
                _log.warning(f"向量库不存在: {faiss_path.name}")
                continue

            try:
                vector_db = read_faiss_index(faiss_path)
            except Exception as e:
                _log.error(f"加载向量库失败 {faiss_path.name}: {e}")
                continue

            all_dbs.append({
                "doc_id": doc_id,
                "vector_db": vector_db,
                "document": document,
            })
        return all_dbs

    def _get_embedding(self, text: str) -> List[float]:
        """获取文本embedding（复用持久客户端，避免每次新建连接）"""
        return self._embedding_api.get_embedding(
            text, provider=self.embedding_provider, model=self.embedding_model
        )

    def retrieve(
        self,
        query: str,
        category: str = None,
        top_n: int = 5,
        doc_ids: List[str] = None,
    ) -> List[Dict]:
        """
        向量检索
        参数:
            query: 查询文本
            category: 按类别过滤
            top_n: 返回结果数
            doc_ids: 限定只检索这些文档（分层检索定向召回用）
        返回:
            [{"text": str, "distance": float, "source": str, "doc_id": str}, ...]
        """
        embedding = self._get_embedding(query)
        embedding_array = np.array(embedding, dtype=np.float32).reshape(1, -1)

        all_results = []
        for db_info in self.all_dbs:
            # doc_ids过滤（优先于category）
            if doc_ids and db_info["doc_id"] not in doc_ids:
                continue

            document = db_info["document"]
            # 类别过滤
            if category and document["metainfo"].get("category") != category:
                continue

            vector_db = db_info["vector_db"]
            chunks = document["content"]["chunks"]
            actual_top_n = min(top_n, len(chunks))

            if actual_top_n == 0:
                continue

            distances, indices = vector_db.search(
                x=embedding_array, k=actual_top_n)

            for distance, index in zip(distances[0], indices[0]):
                all_results.append({
                    "text": chunks[index]["text"],
                    "distance": round(float(distance), 4),
                    "source": document["metainfo"].get("source", ""),
                    "doc_id": db_info["doc_id"],
                    "chunk_id": index,
                })

        # 按distance降序排序
        all_results.sort(key=lambda x: x["distance"], reverse=True)
        return all_results[:top_n]


class HybridRetriever:
    """混合检索器：结合向量和BM25"""

    def __init__(
        self,
        vector_db_dir: Path,
        bm25_db_dir: Path,
        documents_dir: Path,
        embedding_provider: str = "dashscope",
        embedding_model: str = "text-embedding-v1",
    ):
        self.vector_retriever = VectorRetriever(
            vector_db_dir, documents_dir, embedding_provider, embedding_model
        )
        self.bm25_retriever = BM25Retriever(bm25_db_dir, documents_dir)

    def _retrieve_uncached(
        self,
        query: str,
        category: str = None,
        top_n: int = 5,
        vector_weight: float = 0.6,
        doc_ids: List[str] = None,
    ) -> List[Dict]:
        """
        混合检索：合并向量和BM25结果
        参数:
            doc_ids: 限定只检索这些文档（分层检索定向召回用）
        """
        vector_results = self.vector_retriever.retrieve(
            query, category, top_n=top_n * 2, doc_ids=doc_ids)
        bm25_results = self.bm25_retriever.retrieve(
            query, category=category, top_n=top_n * 2, doc_ids=doc_ids)

        # 合并结果（简单加权）
        merged = {}
        for r in vector_results:
            key = (r["doc_id"], r["chunk_id"])
            merged[key] = {
                "text": r["text"],
                "source": r["source"],
                "doc_id": r["doc_id"],
                "chunk_id": r["chunk_id"],
                "score": vector_weight * r["distance"],
            }

        bm25_weight = 1 - vector_weight
        for r in bm25_results:
            key = (r["doc_id"], r["chunk_id"])
            if key in merged:
                merged[key]["score"] += bm25_weight * r["score"]
            else:
                merged[key] = {
                    "text": r["text"],
                    "source": r["source"],
                    "doc_id": r["doc_id"],
                    "chunk_id": r["chunk_id"],
                    "score": bm25_weight * r["score"],
                }

        results = sorted(
            merged.values(), key=lambda x: x["score"], reverse=True)
        return results[:top_n]

    def retrieve(
        self,
        query: str,
        category: str = None,
        top_n: int = 5,
        vector_weight: float = 0.6,
        doc_ids: List[str] = None,
    ) -> List[Dict]:
        if not settings.redis.cache_enabled:
            return self._retrieve_uncached(query, category, top_n, vector_weight, doc_ids)

        key = build_key(query, category, top_n, vector_weight, doc_ids)
        hit = cache_get(key)
        if hit is not None:
            # 命中-> json.loads 返回
            return hit

        # 没命中-> 检索-> json.dumps-> cache.set-> 返回
        results = self._retrieve_uncached(
            query, category, top_n, vector_weight, doc_ids)
        if not results:
            # 空结果也缓存，防止穿透
            cache_set(key, results, settings.redis.empty_ttl)
        else:
            cache_set(key, results, settings.redis.cache_ttl +
                      random.randint(0, settings.redis.cache_ttl_jitter))
        return results
