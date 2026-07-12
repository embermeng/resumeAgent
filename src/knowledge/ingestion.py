"""
向量化入库模块
支持FAISS向量索引和BM25索引双通道
参考 RAG-cy/src/ingestion.py 适配（去掉sha1/company_name依赖）
"""
import json
import pickle
import logging
from pathlib import Path
from typing import List, Union, Optional

import numpy as np
from tqdm import tqdm
from rank_bm25 import BM25Okapi
import faiss
from tenacity import retry, wait_fixed, stop_after_attempt

_log = logging.getLogger(__name__)


class BM25Ingestor:
    """BM25索引构建与保存"""

    def create_bm25_index(self, chunks: List[str]) -> BM25Okapi:
        """从文本块列表创建BM25索引"""
        tokenized_chunks = [chunk.split() for chunk in chunks]
        return BM25Okapi(tokenized_chunks)

    def process_chunks_dir(self, chunks_dir: Path, output_dir: Path):
        """
        批量处理所有分块JSON文件，生成并保存BM25索引
        参数:
            chunks_dir: 存放分块JSON文件的目录
            output_dir: 保存BM25索引(.pkl)的目录
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        chunk_paths = list(chunks_dir.glob("*.json"))

        for chunk_path in tqdm(chunk_paths, desc="Building BM25 indexes"):
            with open(chunk_path, "r", encoding="utf-8") as f:
                doc_data = json.load(f)

            text_chunks = [c["text"] for c in doc_data["content"]["chunks"]]
            if not text_chunks:
                _log.warning(f"跳过空文档: {chunk_path.name}")
                continue

            bm25_index = self.create_bm25_index(text_chunks)

            # 用doc_id作为文件名
            doc_id = doc_data["metainfo"].get("doc_id", chunk_path.stem)
            output_file = output_dir / f"{doc_id}.pkl"
            with open(output_file, "wb") as f:
                pickle.dump(bm25_index, f)

        _log.info(f"BM25索引构建完成，共处理 {len(chunk_paths)} 个文档")

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

    def process_chunks_dir(self, chunks_dir: Path, output_dir: Path):
        """
        批量处理所有分块JSON文件，生成并保存FAISS向量索引
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        chunk_paths = list(chunks_dir.glob("*.json"))

        for chunk_path in tqdm(chunk_paths, desc="Building FAISS indexes"):
            with open(chunk_path, "r", encoding="utf-8") as f:
                doc_data = json.load(f)

            try:
                index = self._process_document(doc_data)
            except Exception as e:
                _log.error(f"处理文档失败 {chunk_path.name}: {e}")
                continue

            doc_id = doc_data["metainfo"].get("doc_id", chunk_path.stem)
            faiss_path = output_dir / f"{doc_id}.faiss"
            faiss.write_index(index, str(faiss_path))

        _log.info(f"FAISS索引构建完成，共处理 {len(chunk_paths)} 个文档")

    def process_single(self, doc_data: dict, output_dir: Path) -> Path:
        """
        处理单个文档，保存FAISS索引
        返回索引文件路径
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        index = self._process_document(doc_data)

        doc_id = doc_data["metainfo"].get("doc_id", "unknown")
        faiss_path = output_dir / f"{doc_id}.faiss"
        faiss.write_index(index, str(faiss_path))
        return faiss_path
