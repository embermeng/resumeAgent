"""
CLI入口 - 知识库构建等批处理命令
用法:
    python main.py parse-pdfs          # 解析PDF为Markdown
    python main.py extract-summaries   # 提取课程结构化摘要与目录
    python main.py split-chunks        # 文本分块
    python main.py build-indexes       # 构建向量/BM25索引
    python main.py ingest-highlights   # 项目亮点文档入库（category=project）
    python main.py build-all           # 一键构建完整知识库
    python main.py chat                # CLI交互模式（测试Agent）
"""
import sys
import argparse
import logging
from pathlib import Path

# 确保项目根目录在sys.path中
sys.path.insert(0, str(Path(__file__).parent))

from src.config import get_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
_log = logging.getLogger(__name__)


def cmd_parse_pdfs(args):
    """解析PDF为Markdown"""
    from src.knowledge.pdf_parser import PDFParser

    config = get_config()
    pdf_dir = config.paths.course_pdfs_dir
    output_dir = config.paths.processed_dir / "parsed_pdfs"

    _log.info(f"开始解析PDF: {pdf_dir}")
    parser = PDFParser(output_dir=output_dir)
    force = getattr(args, "force", False)
    results = parser.parse_and_export_json(pdf_dir, output_dir, category="course", force=force)
    _log.info(f"解析完成，共处理 {len(results)} 个PDF")


def cmd_split_chunks(args):
    """文本分块（增量，默认只分块新文档和源文档更新过的文档）"""
    from src.knowledge.text_splitter import TextSplitter

    config = get_config()
    input_dir = config.paths.processed_dir / "parsed_pdfs"
    output_dir = config.paths.course_chunks_dir

    _log.info(f"开始分块: {input_dir} -> {output_dir}")
    splitter = TextSplitter()

    # 处理parsed_pdfs目录下的markdown文件
    if input_dir.exists():
        # 如果有JSON文件，先提取markdown内容到.md文件
        import json
        import hashlib
        md_dir = config.paths.processed_dir / "markdown_temp"
        md_dir.mkdir(parents=True, exist_ok=True)

        for json_path in input_dir.glob("*.json"):
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "content" not in data or "markdown" not in data["content"]:
                continue

            source = data["metainfo"].get("source", json_path.stem)
            md_path = md_dir / f"{source}.md"
            md_path.write_text(data["content"]["markdown"], encoding="utf-8")

            # 过期检测：解析结果比分块新（源文档更新过）→ 删除旧分块，强制重新分块
            doc_id = hashlib.md5(source.encode()).hexdigest()[:16]
            chunk_path = output_dir / f"{doc_id}.json"
            if chunk_path.exists() and json_path.stat().st_mtime > chunk_path.stat().st_mtime:
                _log.info(f"解析结果已更新，重新分块: {source}")
                chunk_path.unlink()

        splitter.split_and_save(md_dir, output_dir, category="course",
                                chunk_size=args.chunk_size, chunk_overlap=args.chunk_overlap,
                                force=getattr(args, "force", False))
    else:
        _log.warning(f"目录不存在: {input_dir}，请先运行 parse-pdfs")


def cmd_extract_summaries(args):
    """提取课程结构化摘要并合并为目录（增量，默认只处理新文档和解析更新过的文档）"""
    from src.knowledge.summarizer import CourseSummarizer

    config = get_config()
    parsed_dir = config.paths.processed_dir / "parsed_pdfs"
    output_dir = config.paths.course_summaries_dir

    if not parsed_dir.exists() or not list(parsed_dir.glob("*.json")):
        _log.warning(f"解析结果为空: {parsed_dir}，请先运行 parse-pdfs")
        return

    _log.info(f"开始提取课程摘要: {parsed_dir} -> {output_dir}")
    summarizer = CourseSummarizer(
        provider=config.llm.provider,
        model=config.llm.model,
    )
    stats = summarizer.process_parsed_dir(
        parsed_dir, output_dir, force=getattr(args, "force", False)
    )

    # 每次跑完都重新合并目录（纯本地拼接，无API成本）
    summarizer.build_catalog(output_dir, config.paths.catalog_path)
    _log.info(f"摘要提取完成: {stats}")


def cmd_build_indexes(args):
    """构建向量/BM25索引（增量，默认跳过已有索引）"""
    from src.knowledge.ingestion import BM25Ingestor, VectorDBIngestor

    config = get_config()
    chunks_dir = config.paths.course_chunks_dir

    if not chunks_dir.exists() or not list(chunks_dir.glob("*.json")):
        _log.warning(f"分块目录为空: {chunks_dir}，请先运行 split-chunks")
        return

    force = getattr(args, "force", False)
    prune = getattr(args, "prune", False)

    if args.bm25:
        _log.info("构建BM25索引...")
        bm25 = BM25Ingestor()
        bm25.process_chunks_dir(chunks_dir, config.paths.bm25_dbs_dir, force=force, prune=prune)

    if args.vector:
        _log.info("构建FAISS向量索引...")
        vector = VectorDBIngestor(
            embedding_provider=config.embedding.provider,
            embedding_model=config.embedding.model,
        )
        vector.process_chunks_dir(chunks_dir, config.paths.vector_dbs_dir, force=force, prune=prune)

    _log.info("索引构建完成")


def cmd_ingest_highlights(args):
    """项目亮点文档入库：md -> category=project分块，写入course_chunks待建索引"""
    from src.knowledge.highlight_ingestor import HighlightIngestor

    config = get_config()
    highlights_dir = config.paths.project_highlights_dir

    if not highlights_dir.exists() or not list(highlights_dir.glob("*.md")):
        _log.warning(f"项目亮点目录为空: {highlights_dir}，请先放入项目亮点README（.md）")
        return

    _log.info(f"开始入库项目亮点文档: {highlights_dir}")
    ingestor = HighlightIngestor()
    ingestor.ingest(
        highlights_dir,
        config.paths.course_chunks_dir,
        force=getattr(args, "force", False),
    )
    _log.info("入库完成，请运行 build-indexes 建立索引")


def cmd_build_all(args):
    """一键构建完整知识库"""
    _log.info("=" * 50)
    _log.info("开始一键构建知识库")
    _log.info("=" * 50)

    # Step 1: 解析PDF
    _log.info("[1/5] 解析PDF...")
    cmd_parse_pdfs(args)

    # Step 2: 提取课程摘要与目录（分层检索用）
    _log.info("[2/5] 提取课程摘要...")
    cmd_extract_summaries(args)

    # Step 3: 分块
    _log.info("[3/5] 文本分块...")
    cmd_split_chunks(args)

    # Step 4: 项目亮点文档入库（与课程分块合并建索引）
    _log.info("[4/5] 项目亮点文档入库...")
    cmd_ingest_highlights(args)

    # Step 5: 构建索引
    _log.info("[5/5] 构建索引...")
    args.bm25 = True
    args.vector = True
    cmd_build_indexes(args)

    _log.info("=" * 50)
    _log.info("知识库构建完成！")
    _log.info("=" * 50)


def cmd_chat(args):
    """CLI交互模式"""
    from src.agent.graph import ResumeAgent

    config = get_config()
    agent = ResumeAgent(config=config)

    print("\n" + "=" * 50)
    print("ResumeAgent CLI - 输入问题，输入 'quit' 退出")
    print("=" * 50 + "\n")

    while True:
        try:
            user_input = input("You: ").strip()
            if user_input.lower() in {"quit", "exit", "q"}:
                print("Bye!")
                break
            if not user_input:
                continue

            result = agent.run(user_input)
            print(f"\n[{result.get('intent', 'unknown')}] ")
            print(f"{result.get('final_response', 'No response')}\n")

        except KeyboardInterrupt:
            print("\nBye!")
            break
        except Exception as e:
            print(f"\nError: {e}\n")


def main():
    parser = argparse.ArgumentParser(description="ResumeAgent CLI - 知识库构建与交互")
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # parse-pdfs
    sp_parse = subparsers.add_parser("parse-pdfs", help="解析PDF为Markdown（增量，默认只解析新文件）")
    sp_parse.add_argument("--force", action="store_true", help="强制全量重新解析所有PDF")

    # extract-summaries
    sp_sum = subparsers.add_parser(
        "extract-summaries",
        help="提取课程结构化摘要与目录（增量，分层检索第一层）",
    )
    sp_sum.add_argument("--force", action="store_true", help="强制全量重新提取所有摘要")

    # split-chunks
    sp_split = subparsers.add_parser("split-chunks", help="文本分块（增量，默认只分块新文档）")
    sp_split.add_argument("--chunk-size", type=int, default=300, help="分块大小(token)")
    sp_split.add_argument("--chunk-overlap", type=int, default=50, help="重叠token数")
    sp_split.add_argument("--force", action="store_true", help="强制全量重新分块")

    # build-indexes
    sp_idx = subparsers.add_parser("build-indexes", help="构建向量/BM25索引（增量，默认跳过已有索引）")
    sp_idx.add_argument("--bm25", action="store_true", default=True, help="构建BM25索引")
    sp_idx.add_argument("--vector", action="store_true", default=True, help="构建FAISS索引")
    sp_idx.add_argument("--force", action="store_true", help="强制全量重建所有索引")
    sp_idx.add_argument("--prune", action="store_true", help="清理孤儿索引（对应文档已删除/改名）")

    # ingest-highlights
    sp_hl = subparsers.add_parser(
        "ingest-highlights",
        help="项目亮点文档入库（增量，读取project_highlights目录的md）",
    )
    sp_hl.add_argument("--force", action="store_true", help="强制全量重新入库所有亮点文档")

    # build-all
    sp_all = subparsers.add_parser("build-all", help="一键构建完整知识库（各环节均为增量）")
    sp_all.add_argument("--chunk-size", type=int, default=300)
    sp_all.add_argument("--chunk-overlap", type=int, default=50)
    sp_all.add_argument("--force", action="store_true", help="强制全量重新解析/分块/建索引")
    sp_all.add_argument("--prune", action="store_true", help="清理孤儿索引（对应文档已删除/改名）")

    # chat
    subparsers.add_parser("chat", help="CLI交互模式")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    commands = {
        "parse-pdfs": cmd_parse_pdfs,
        "extract-summaries": cmd_extract_summaries,
        "split-chunks": cmd_split_chunks,
        "build-indexes": cmd_build_indexes,
        "ingest-highlights": cmd_ingest_highlights,
        "build-all": cmd_build_all,
        "chat": cmd_chat,
    }

    cmd_func = commands.get(args.command)
    if cmd_func:
        cmd_func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
