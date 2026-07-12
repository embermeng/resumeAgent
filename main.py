"""
CLI入口 - 知识库构建、项目提炼等批处理命令
用法:
    python main.py parse-pdfs          # 解析PDF为Markdown
    python main.py split-chunks        # 文本分块
    python main.py build-indexes       # 构建向量/BM25索引
    python main.py extract-projects    # 提炼项目精华
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
    results = parser.parse_and_export_json(pdf_dir, output_dir, category="course")
    _log.info(f"解析完成，共处理 {len(results)} 个PDF")


def cmd_split_chunks(args):
    """文本分块"""
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
        md_dir = config.paths.processed_dir / "markdown_temp"
        md_dir.mkdir(parents=True, exist_ok=True)

        for json_path in input_dir.glob("*.json"):
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "content" in data and "markdown" in data["content"]:
                source = data["metainfo"].get("source", json_path.stem)
                md_path = md_dir / f"{source}.md"
                md_path.write_text(data["content"]["markdown"], encoding="utf-8")

        splitter.split_and_save(md_dir, output_dir, category="course",
                                chunk_size=args.chunk_size, chunk_overlap=args.chunk_overlap)
    else:
        _log.warning(f"目录不存在: {input_dir}，请先运行 parse-pdfs")


def cmd_build_indexes(args):
    """构建向量/BM25索引"""
    from src.knowledge.ingestion import BM25Ingestor, VectorDBIngestor

    config = get_config()
    chunks_dir = config.paths.course_chunks_dir

    if not chunks_dir.exists() or not list(chunks_dir.glob("*.json")):
        _log.warning(f"分块目录为空: {chunks_dir}，请先运行 split-chunks")
        return

    if args.bm25:
        _log.info("构建BM25索引...")
        bm25 = BM25Ingestor()
        bm25.process_chunks_dir(chunks_dir, config.paths.bm25_dbs_dir)

    if args.vector:
        _log.info("构建FAISS向量索引...")
        vector = VectorDBIngestor(
            embedding_provider=config.embedding.provider,
            embedding_model=config.embedding.model,
        )
        vector.process_chunks_dir(chunks_dir, config.paths.vector_dbs_dir)

    _log.info("索引构建完成")


def cmd_extract_projects(args):
    """提炼项目精华"""
    from src.knowledge.project_extractor import ProjectExtractor

    config = get_config()
    projects_dir = config.paths.project_sources_dir
    output_dir = config.paths.project_extracts_dir

    if not projects_dir.exists() or not list(projects_dir.iterdir()):
        _log.warning(f"项目目录为空: {projects_dir}，请先放入项目源码")
        return

    _log.info(f"开始提炼项目精华: {projects_dir}")
    extractor = ProjectExtractor(
        provider=config.llm.provider,
        model=config.llm.model,
    )
    results = extractor.extract_batch(projects_dir, output_dir=output_dir)
    _log.info(f"提炼完成，共处理 {len(results)} 个项目")


def cmd_build_all(args):
    """一键构建完整知识库"""
    _log.info("=" * 50)
    _log.info("开始一键构建知识库")
    _log.info("=" * 50)

    # Step 1: 解析PDF
    _log.info("[1/4] 解析PDF...")
    cmd_parse_pdfs(args)

    # Step 2: 分块
    _log.info("[2/4] 文本分块...")
    cmd_split_chunks(args)

    # Step 3: 构建索引
    _log.info("[3/4] 构建索引...")
    args.bm25 = True
    args.vector = True
    cmd_build_indexes(args)

    # Step 4: 提炼项目
    _log.info("[4/4] 提炼项目精华...")
    cmd_extract_projects(args)

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
    subparsers.add_parser("parse-pdfs", help="解析PDF为Markdown")

    # split-chunks
    sp_split = subparsers.add_parser("split-chunks", help="文本分块")
    sp_split.add_argument("--chunk-size", type=int, default=300, help="分块大小(token)")
    sp_split.add_argument("--chunk-overlap", type=int, default=50, help="重叠token数")

    # build-indexes
    sp_idx = subparsers.add_parser("build-indexes", help="构建向量/BM25索引")
    sp_idx.add_argument("--bm25", action="store_true", default=True, help="构建BM25索引")
    sp_idx.add_argument("--vector", action="store_true", default=True, help="构建FAISS索引")

    # extract-projects
    subparsers.add_parser("extract-projects", help="提炼项目精华")

    # build-all
    sp_all = subparsers.add_parser("build-all", help="一键构建完整知识库")
    sp_all.add_argument("--chunk-size", type=int, default=300)
    sp_all.add_argument("--chunk-overlap", type=int, default=50)

    # chat
    subparsers.add_parser("chat", help="CLI交互模式")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    commands = {
        "parse-pdfs": cmd_parse_pdfs,
        "split-chunks": cmd_split_chunks,
        "build-indexes": cmd_build_indexes,
        "extract-projects": cmd_extract_projects,
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
