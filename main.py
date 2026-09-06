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

说明:知识库构建逻辑已抽取到 src/api/services/knowledge_service.py,
     CLI 与 FastAPI 后台任务共用同一套实现(单一数据源),此处仅做参数适配。
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


def _service():
    """构造知识库构建服务(CLI 与 API 共用同一套逻辑)"""
    from src.api.services.knowledge_service import KnowledgeService
    return KnowledgeService(config=get_config())


def _cli_progress(stage, message, percent):
    """CLI 进度打印(与 API 的 SSE 进度共用 service 编排)"""
    _log.info(message)


def cmd_parse_pdfs(args):
    """解析PDF为Markdown"""
    _service().parse_pdfs(force=getattr(args, "force", False))


def cmd_split_chunks(args):
    """文本分块（增量，默认只分块新文档和源文档更新过的文档）"""
    _service().split_chunks(
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        force=getattr(args, "force", False),
    )


def cmd_extract_summaries(args):
    """提取课程结构化摘要并合并为目录（增量，默认只处理新文档和解析更新过的文档）"""
    _service().extract_summaries(force=getattr(args, "force", False))


def cmd_build_indexes(args):
    """构建向量/BM25索引（增量，默认跳过已有索引）"""
    _service().build_indexes(
        bm25=args.bm25,
        vector=args.vector,
        force=getattr(args, "force", False),
        prune=getattr(args, "prune", False),
    )


def cmd_ingest_highlights(args):
    """项目亮点文档入库：md -> category=project分块，写入course_chunks待建索引"""
    _service().ingest_highlights(force=getattr(args, "force", False))


def cmd_build_all(args):
    """一键构建完整知识库"""
    _log.info("=" * 50)
    _log.info("开始一键构建知识库")
    _log.info("=" * 50)

    _service().build_all(
        force=getattr(args, "force", False),
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        prune=getattr(args, "prune", False),
        progress=_cli_progress,
    )

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
