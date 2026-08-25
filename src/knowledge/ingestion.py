"""
向量化入库模块
支持FAISS向量索引和BM25索引双通道
增量构建：默认跳过已存在且未过期的索引，force全量重建，prune清理孤儿索引
参考 RAG-cy/src/ingestion.py 适配（去掉sha1/company_name依赖）
"""
import json
import os
import pickle
import logging
from pathlib import Path
from typing import List, Union, Optional

import numpy as np
from tqdm import tqdm
from rank_bm25 import BM25Okapi
import faiss
from tenacity import retry, wait_fixed, stop_after_attempt

from src.knowledge.tokenizer import tokenize

_log = logging.getLogger(__name__)


def _need_rebuild(chunk_path: Path, index_path: Path, force: bool) -> bool:
    """判断是否需要重建索引：force / 索引不存在 / 分块文件比索引新（上游更新过）"""
    if force:
        return True
    if not index_path.exists():
        return True
    return chunk_path.stat().st_mtime > index_path.stat().st_mtime


class BM25Ingestor:
    """BM25索引构建与保存"""

    def create_bm25_index(self, chunks: List[str]) -> BM25Okapi:
        """从文本块列表创建BM25索引"""
        # 必须与查询侧使用同一分词器（jieba），中文无空格不能用split()
        tokenized_chunks = [tokenize(chunk) for chunk in chunks]
        return BM25Okapi(tokenized_chunks)

    def process_chunks_dir(
        self,
        chunks_dir: Path,
        output_dir: Path,
        force: bool = False,
        prune: bool = False,
    ):
        """
        批量处理所有分块JSON文件，生成并保存BM25索引
        增量构建：默认跳过已存在且未过期的索引
        参数:
            chunks_dir: 存放分块JSON文件的目录
            output_dir: 保存BM25索引(.pkl)的目录
            force: 强制全量重建
            prune: 清理doc_id在分块目录中已不存在的孤儿索引
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        chunk_paths = list(chunks_dir.glob("*.json"))

        skipped = 0
        built = 0
        valid_doc_ids = set()
        for chunk_path in tqdm(chunk_paths, desc="Building BM25 indexes"):
            with open(chunk_path, "r", encoding="utf-8") as f:
                doc_data = json.load(f)

            # 用doc_id作为文件名
            doc_id = doc_data["metainfo"].get("doc_id", chunk_path.stem)
            valid_doc_ids.add(doc_id)
            output_file = output_dir / f"{doc_id}.pkl"

            if not _need_rebuild(chunk_path, output_file, force):
                _log.info(f"跳过已有BM25索引: {doc_id}")
                skipped += 1
                continue

            text_chunks = [c["text"] for c in doc_data["content"]["chunks"]]
            if not text_chunks:
                _log.warning(f"跳过空文档: {chunk_path.name}")
                continue

            bm25_index = self.create_bm25_index(text_chunks)

            # 原子写入：先写临时文件再替换，避免中断留下半截索引
            tmp_file = output_file.with_suffix(".pkl.tmp")
            with open(tmp_file, "wb") as f:
                pickle.dump(bm25_index, f)
            os.replace(tmp_file, output_file)
            built += 1

        if prune:
            self._prune_orphan_indexes(output_dir, valid_doc_ids)

        _log.info(
            f"BM25索引构建完成，共 {len(chunk_paths)} 个文档，"
            f"跳过 {skipped} 个，新建/重建 {built} 个"
        )

    @staticmethod
    def _prune_orphan_indexes(output_dir: Path, valid_doc_ids: set):
        """删除doc_id在上游已不存在的孤儿索引文件"""
        for index_file in output_dir.glob("*.pkl"):
            if index_file.stem not in valid_doc_ids:
                index_file.unlink()
                _log.warning(f"已清理孤儿BM25索引: {index_file.name}")

    def process_single(self, doc_data: dict, output_dir: Path) -> Path:
        """
        处理单个文档数据，保存BM25索引
        返回索引文件路径
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        text_chunks = [c["text"] for c in doc_data["content"]["chunks"]]
        bm25_index = self.create_bm25_index(text_chunks)

        doc_id = doc_data["metainfo"].get("doc_id", "unknown")
        output_file = output_dir / f"{doc_id}.pkl"
        with open(output_file, "wb") as f:
            pickle.dump(bm25_index, f)
        return output_file


class VectorDBIngestor:
    """FAISS向量索引构建与保存"""

    def __init__(self, embedding_provider: str = "dashscope", embedding_model: str = "text-embedding-v1"):
        self.embedding_provider = embedding_provider
        self.embedding_model = embedding_model

    def _get_embeddings(self, text: Union[str, List[str]]) -> List[List[float]]:
        """
        获取文本嵌入向量，支持重试
        支持dashscope和openai两种provider
        """
        if self.embedding_provider not in ("dashscope", "openai"):
            raise ValueError(f"不支持的embedding provider: {self.embedding_provider}")

        if isinstance(text, str):
            text_chunks = [text] if text.strip() else []
        else:
            text_chunks = [x for x in text if isinstance(x, str) and x.strip()]

        if not text_chunks:
            raise ValueError("输入文本为空或无有效内容")

        embeddings = []
        max_batch_size = 25

        if self.embedding_provider == "dashscope":
            import dashscope
            import os
            dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")

            for i in range(0, len(text_chunks), max_batch_size):
                batch = text_chunks[i:i + max_batch_size]
                resp = dashscope.TextEmbedding.call(
                    model=self.embedding_model,
                    input=batch,
                )
                if "output" in resp and "embeddings" in resp["output"]:
                    for emb in resp["output"]["embeddings"]:
                        if emb["embedding"] is None or len(emb["embedding"]) == 0:
                            raise RuntimeError(
                                f"DashScope返回embedding为空, text_index={getattr(emb, 'text_index', None)}"
                            )
                        embeddings.append(emb["embedding"])
                else:
                    raise RuntimeError(f"DashScope embedding API返回格式异常: {resp}")

        elif self.embedding_provider == "openai":
            from openai import OpenAI
            import os
            client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

            for i in range(0, len(text_chunks), max_batch_size):
                batch = text_chunks[i:i + max_batch_size]
                resp = client.embeddings.create(input=batch, model=self.embedding_model)
                for item in resp.data:
                    embeddings.append(item.embedding)
        else:
            raise ValueError(f"不支持的embedding provider: {self.embedding_provider}")

        return embeddings

    def _create_vector_db(self, embeddings: List[List[float]]) -> "faiss.Index":
        """用FAISS构建向量索引（内积=余弦距离）"""
        embeddings_array = np.array(embeddings, dtype=np.float32)
        dimension = len(embeddings[0])
        index = faiss.IndexFlatIP(dimension)
        index.add(embeddings_array)
        return index

    @retry(wait=wait_fixed(20), stop=stop_after_attempt(2))
    def _process_document(self, doc_data: dict) -> faiss.Index:
        """处理单个文档数据，提取文本块并生成向量索引"""
        text_chunks = [c["text"] for c in doc_data["content"]["chunks"]]
        # 过滤空内容，截断超长内容
        max_len = 2048
        text_chunks = [t[:max_len] for t in text_chunks if len(t) > 0]

        if not text_chunks:
            raise ValueError("文档中无有效文本块")

        embeddings = self._get_embeddings(text_chunks)
        return self._create_vector_db(embeddings)

    def process_chunks_dir(
        self,
        chunks_dir: Path,
        output_dir: Path,
        force: bool = False,
        prune: bool = False,
    ):
        """
        批量处理所有分块JSON文件，生成并保存FAISS向量索引
        增量构建：默认跳过已存在且未过期的索引（节省embedding API调用）
        参数:
            chunks_dir: 存放分块JSON文件的目录
            output_dir: 保存FAISS索引(.faiss)的目录
            force: 强制全量重建
            prune: 清理doc_id在分块目录中已不存在的孤儿索引
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        chunk_paths = list(chunks_dir.glob("*.json"))

        skipped = 0
        built = 0
        valid_doc_ids = set()
        for chunk_path in tqdm(chunk_paths, desc="Building FAISS indexes"):
            with open(chunk_path, "r", encoding="utf-8") as f:
                doc_data = json.load(f)

            doc_id = doc_data["metainfo"].get("doc_id", chunk_path.stem)
            valid_doc_ids.add(doc_id)
            faiss_path = output_dir / f"{doc_id}.faiss"

            if not _need_rebuild(chunk_path, faiss_path, force):
                _log.info(f"跳过已有FAISS索引: {doc_id}")
                skipped += 1
                continue

            try:
                index = self._process_document(doc_data)
            except Exception as e:
                _log.error(f"处理文档失败 {chunk_path.name}: {e}")
                continue

            # FAISS C++ fopen不支持中文路径，先写临时文件再移动
            import tempfile, shutil
            with tempfile.NamedTemporaryFile(suffix=".faiss", delete=False) as tmp:
                tmp_path = tmp.name
            try:
                faiss.write_index(index, tmp_path)
                shutil.move(tmp_path, str(faiss_path))
            except Exception:
                # 如果临时文件方式也失败，尝试直接写入（路径无中文时可能成功）
                try:
                    faiss.write_index(index, str(faiss_path))
                except RuntimeError:
                    raise
            finally:
                if Path(tmp_path).exists():
                    Path(tmp_path).unlink(missing_ok=True)
            built += 1

        if prune:
            self._prune_orphan_indexes(output_dir, valid_doc_ids)

        _log.info(
            f"FAISS索引构建完成，共 {len(chunk_paths)} 个文档，"
            f"跳过 {skipped} 个，新建/重建 {built} 个"
        )

    @staticmethod
    def _prune_orphan_indexes(output_dir: Path, valid_doc_ids: set):
        """删除doc_id在上游已不存在的孤儿索引文件"""
        for index_file in output_dir.glob("*.faiss"):
            if index_file.stem not in valid_doc_ids:
                index_file.unlink()
                _log.warning(f"已清理孤儿FAISS索引: {index_file.name}")

    def process_single(self, doc_data: dict, output_dir: Path) -> Path:
        """
        处理单个文档，保存FAISS索引
        返回索引文件路径
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        index = self._process_document(doc_data)

        doc_id = doc_data["metainfo"].get("doc_id", "unknown")
        faiss_path = output_dir / f"{doc_id}.faiss"
        # FAISS C++ fopen不支持中文路径，先写临时文件再移动
        import tempfile, shutil
        with tempfile.NamedTemporaryFile(suffix=".faiss", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            faiss.write_index(index, tmp_path)
            shutil.move(tmp_path, str(faiss_path))
        except Exception:
            try:
                faiss.write_index(index, str(faiss_path))
            except RuntimeError:
                raise
        finally:
            if Path(tmp_path).exists():
                Path(tmp_path).unlink(missing_ok=True)
        return faiss_path
