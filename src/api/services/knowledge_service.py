"""
知识库构建服务层

从 main.py 的 cmd_* 抽取,供 CLI 与 API 后台任务共用(单一数据源,避免逻辑重复)。
每个方法接受可选 progress(stage, message, percent) 回调用于 SSE 进度上报。
阶段百分比契约见 docs/specs/api-contract.md 第 5 节。
"""
import hashlib
import json
import logging
from typing import Callable, Optional

from src.config import get_config

_log = logging.getLogger(__name__)

# 进度回调签名:(stage, message, percent)
ProgressFn = Callable[[str, str, float], None]


class KnowledgeService:
    """知识库构建服务:各阶段可单独执行,也可 build_all 串联并上报全局进度。"""

    def __init__(self, config=None):
        self.config = config or get_config()

    # ============================================================
    # 各阶段(逻辑搬运自 main.py cmd_*,行为保持一致)
    # ============================================================

    def parse_pdfs(self, force: bool = False, progress: Optional[ProgressFn] = None):
        """解析 PDF 为 Markdown"""
        from src.knowledge.pdf_parser import PDFParser

        if progress:
            progress("parse-pdfs", "解析PDF为Markdown...", 10)
        pdf_dir = self.config.paths.course_pdfs_dir
        output_dir = self.config.paths.processed_dir / "parsed_pdfs"
        _log.info(f"开始解析PDF: {pdf_dir}")
        parser = PDFParser(output_dir=output_dir)
        results = parser.parse_and_export_json(
            pdf_dir, output_dir, category="course", force=force
        )
        _log.info(f"解析完成,共处理 {len(results)} 个PDF")
        return results

    def extract_summaries(self, force: bool = False, progress: Optional[ProgressFn] = None):
        """提取课程结构化摘要并合并目录"""
        from src.knowledge.summarizer import CourseSummarizer

        if progress:
            progress("extract-summaries", "提取课程摘要...", 35)
        parsed_dir = self.config.paths.processed_dir / "parsed_pdfs"
        output_dir = self.config.paths.course_summaries_dir
        if not parsed_dir.exists() or not list(parsed_dir.glob("*.json")):
            _log.warning(f"解析结果为空: {parsed_dir},请先运行 parse-pdfs")
            return None
        _log.info(f"开始提取课程摘要: {parsed_dir} -> {output_dir}")
        summarizer = CourseSummarizer(
            provider=self.config.llm.provider, model=self.config.llm.model
        )
        stats = summarizer.process_parsed_dir(parsed_dir, output_dir, force=force)
        summarizer.build_catalog(output_dir, self.config.paths.catalog_path)
        _log.info(f"摘要提取完成: {stats}")
        return stats

    def split_chunks(
        self,
        chunk_size: int = 300,
        chunk_overlap: int = 50,
        force: bool = False,
        progress: Optional[ProgressFn] = None,
    ):
        """文本分块(含解析结果过期检测:源更新则删除旧分块强制重分)"""
        from src.knowledge.text_splitter import TextSplitter

        if progress:
            progress("split-chunks", "文本分块...", 55)
        input_dir = self.config.paths.processed_dir / "parsed_pdfs"
        output_dir = self.config.paths.course_chunks_dir
        _log.info(f"开始分块: {input_dir} -> {output_dir}")
        splitter = TextSplitter()

        if input_dir.exists():
            md_dir = self.config.paths.processed_dir / "markdown_temp"
            md_dir.mkdir(parents=True, exist_ok=True)

            for json_path in input_dir.glob("*.json"):
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if "content" not in data or "markdown" not in data["content"]:
                    continue

                source = data["metainfo"].get("source", json_path.stem)
                md_path = md_dir / f"{source}.md"
                md_path.write_text(data["content"]["markdown"], encoding="utf-8")

                doc_id = hashlib.md5(source.encode()).hexdigest()[:16]
                chunk_path = output_dir / f"{doc_id}.json"
                if chunk_path.exists() and json_path.stat().st_mtime > chunk_path.stat().st_mtime:
                    _log.info(f"解析结果已更新,重新分块: {source}")
                    chunk_path.unlink()

            splitter.split_and_save(
                md_dir, output_dir, category="course",
                chunk_size=chunk_size, chunk_overlap=chunk_overlap, force=force,
            )
        else:
            _log.warning(f"目录不存在: {input_dir},请先运行 parse-pdfs")

    def build_indexes(
        self,
        bm25: bool = True,
        vector: bool = True,
        force: bool = False,
        prune: bool = False,
        progress: Optional[ProgressFn] = None,
    ):
        """构建向量/BM25 索引(增量)"""
        from src.knowledge.ingestion import BM25Ingestor, VectorDBIngestor

        if progress:
            progress("build-indexes", "构建索引...", 90)
        chunks_dir = self.config.paths.course_chunks_dir
        if not chunks_dir.exists() or not list(chunks_dir.glob("*.json")):
            _log.warning(f"分块目录为空: {chunks_dir},请先运行 split-chunks")
            return

        if bm25:
            _log.info("构建BM25索引...")
            BM25Ingestor().process_chunks_dir(
                chunks_dir, self.config.paths.bm25_dbs_dir, force=force, prune=prune
            )
        if vector:
            _log.info("构建FAISS向量索引...")
            VectorDBIngestor(
                embedding_provider=self.config.embedding.provider,
                embedding_model=self.config.embedding.model,
            ).process_chunks_dir(
                chunks_dir, self.config.paths.vector_dbs_dir, force=force, prune=prune
            )
        _log.info("索引构建完成")

    def ingest_highlights(self, force: bool = False, progress: Optional[ProgressFn] = None):
        """项目亮点文档入库(category=project 分块)"""
        from src.knowledge.highlight_ingestor import HighlightIngestor

        if progress:
            progress("ingest-highlights", "项目亮点入库...", 70)
        highlights_dir = self.config.paths.project_highlights_dir
        if not highlights_dir.exists() or not list(highlights_dir.glob("*.md")):
            _log.warning(f"项目亮点目录为空: {highlights_dir},请先放入项目亮点README(.md)")
            return
        _log.info(f"开始入库项目亮点文档: {highlights_dir}")
        HighlightIngestor().ingest(
            highlights_dir, self.config.paths.course_chunks_dir, force=force
        )
        _log.info("入库完成,请运行 build-indexes 建立索引")

    # ============================================================
    # 编排
    # ============================================================

    def build_all(
        self,
        force: bool = False,
        chunk_size: int = 300,
        chunk_overlap: int = 50,
        prune: bool = False,
        progress: Optional[ProgressFn] = None,
    ):
        """一键构建:全局进度由本方法按契约百分比如序上报,子阶段不再重复上报。"""
        def p(stage: str, message: str, percent: float):
            if progress:
                progress(stage, message, percent)

        p("parse-pdfs", "[1/5] 解析PDF...", 10)
        self.parse_pdfs(force=force)

        p("extract-summaries", "[2/5] 提取课程摘要...", 35)
        self.extract_summaries(force=force)

        p("split-chunks", "[3/5] 文本分块...", 55)
        self.split_chunks(chunk_size=chunk_size, chunk_overlap=chunk_overlap, force=force)

        p("ingest-highlights", "[4/5] 项目亮点入库...", 70)
        self.ingest_highlights(force=force)

        p("build-indexes", "[5/5] 构建索引...", 90)
        self.build_indexes(bm25=True, vector=True, force=force, prune=prune)

        _log.info("知识库构建完成")

    def run(
        self,
        task: str,
        force: bool = False,
        chunk_size: int = 300,
        chunk_overlap: int = 50,
        prune: bool = False,
        progress: Optional[ProgressFn] = None,
    ):
        """按任务类型 dispatch(供后台任务线程调用)"""
        if task == "build-all":
            return self.build_all(
                force=force, chunk_size=chunk_size,
                chunk_overlap=chunk_overlap, prune=prune, progress=progress,
            )
        if task == "parse-pdfs":
            return self.parse_pdfs(force=force, progress=progress)
        if task == "extract-summaries":
            return self.extract_summaries(force=force, progress=progress)
        if task == "split-chunks":
            return self.split_chunks(
                chunk_size=chunk_size, chunk_overlap=chunk_overlap,
                force=force, progress=progress,
            )
        if task == "build-indexes":
            return self.build_indexes(force=force, prune=prune, progress=progress)
        if task == "ingest-highlights":
            return self.ingest_highlights(force=force, progress=progress)
        raise ValueError(f"未知任务类型: {task}")
