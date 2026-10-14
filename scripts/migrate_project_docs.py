"""
一次性迁移：合并项目亮点 + 项目介绍 -> 单一项目文档，并按用户归属落盘。

背景：合并前是两套目录、两种消费方式——
  project_highlights/  入库分块，供知识问答检索（详尽版）
  project_intros/      不入库存目录，简历生成时全文引用（精炼版）
两者内容高度重合，需用户维护两份。合并后为 projects/ 单目录，一份文档两种取法：
  简历生成 -> 「简历描述」章节（缺失降级全文）
  知识问答 -> 全部章节分块入库

合并规则：
- 以亮点文档为底（内容详尽，检索素材更全）
- 有同名介绍文档时，取其「项目描述」章节作为「简历描述」插入「一句话简介」之后
- 无同名介绍文档的，原样落盘（简历生成走全文降级）
- 只有介绍文档没有亮点文档的，整份落盘

用法：
  python scripts/migrate_project_docs.py --user-id 9            # 预演
  python scripts/migrate_project_docs.py --user-id 9 --apply    # 执行
落盘后需重建索引（doc_id 带 u{user_id} 段，与旧索引不同）：
  python main.py ingest-projects --force
  python main.py build-indexes --force
"""
from __future__ import annotations

import argparse
import logging
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import get_config  # noqa: E402
from src.knowledge.project_doc import ProjectDocParser  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
_log = logging.getLogger("migrate")

_H2_PATTERN = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
# 介绍文档里承载简历措辞的章节（亮点文档也可能已带「简历描述」）
_INTRO_SOURCE_TITLES = re.compile(r"^##\s*(简历描述|简历表述|项目描述)\s*$", re.MULTILINE)
_SUMMARY_TITLE = re.compile(r"^##\s*(一句话简介|项目简介|简介)\s*$", re.MULTILINE)


def _extract_section(text: str, title_pattern: re.Pattern) -> str | None:
    """取出匹配标题的整段（含标题行），无匹配返回 None"""
    m = title_pattern.search(text)
    if not m:
        return None
    nxt = _H2_PATTERN.search(text, m.end())
    end = nxt.start() if nxt else len(text)
    section = text[m.start():end].strip()
    return section or None


def _merge(highlight_text: str, intro_text: str | None) -> str:
    """把介绍文档的措辞章节作为「简历描述」插入亮点文档"""
    if not intro_text:
        return highlight_text

    # 亮点文档自己已有简历章节则不覆盖
    if _extract_section(highlight_text, re.compile(
            r"^##\s*(简历描述|简历表述)\s*$", re.MULTILINE)):
        return highlight_text

    source = _extract_section(intro_text, _INTRO_SOURCE_TITLES)
    if not source:
        return highlight_text

    body = "\n".join(source.split("\n")[1:]).strip()   # 去掉原标题行
    section = f"## 简历描述\n{body}"

    # 插到「一句话简介」之后，没有就插到一级标题之后
    anchor = _SUMMARY_TITLE.search(highlight_text)
    if anchor:
        nxt = _H2_PATTERN.search(highlight_text, anchor.end())
        pos = nxt.start() if nxt else len(highlight_text)
        head, tail = highlight_text[:pos].rstrip(), highlight_text[pos:].lstrip()
        return f"{head}\n\n{section}\n\n{tail}".strip() + "\n"

    h1 = re.search(r"^#\s+.+?$", highlight_text, re.MULTILINE)
    pos = h1.end() if h1 else 0
    head, tail = highlight_text[:pos].rstrip(), highlight_text[pos:].lstrip()
    return f"{head}\n\n{section}\n\n{tail}".strip() + "\n"


def _index_docs(directory: Path) -> dict[str, Path]:
    """扫描目录，按项目名索引（需有一级标题）"""
    index: dict[str, Path] = {}
    if not directory.exists():
        return index
    for p in sorted(directory.glob("*.md")):
        text = ProjectDocParser.clean_markdown(p.read_text(encoding="utf-8"))
        name = ProjectDocParser.extract_project_name(text)
        if not name:
            _log.warning(f"  跳过（无一级标题）: {p.name}")
            continue
        if name in index:
            _log.warning(f"  项目名重复，保留先扫描到的: {name}（{p.name}）")
            continue
        index[name] = p
    return index


def main() -> int:
    ap = argparse.ArgumentParser(description="合并项目亮点与介绍文档并归属到用户")
    ap.add_argument("--user-id", type=int, required=True, help="归属的用户 id")
    ap.add_argument("--apply", action="store_true", help="真正写盘；默认只预演")
    args = ap.parse_args()

    cfg = get_config()
    highlights_dir = cfg.paths.knowledge_base_dir / "project_highlights"
    intros_dir = cfg.paths.knowledge_base_dir / "project_intros"
    out_dir = cfg.paths.user_projects_dir(args.user_id)

    hl = _index_docs(highlights_dir)
    intro = _index_docs(intros_dir)
    _log.info(f"扫描到亮点文档 {len(hl)} 份，介绍文档 {len(intro)} 份")

    merged_names = sorted(set(hl) | set(intro))
    _log.info(f"合并后共 {len(merged_names)} 个项目 -> {out_dir}")

    if args.apply:
        out_dir.mkdir(parents=True, exist_ok=True)

    with_resume, without_resume, intro_only = 0, 0, 0
    for name in merged_names:
        hl_path = hl.get(name)
        intro_path = intro.get(name)

        if hl_path is not None:
            text = ProjectDocParser.clean_markdown(hl_path.read_text(encoding="utf-8"))
            intro_text = (ProjectDocParser.clean_markdown(
                intro_path.read_text(encoding="utf-8")) if intro_path else None)
            merged = _merge(text, intro_text)
            out_name = hl_path.name
            has_resume = "## 简历描述" in merged
        else:
            merged = ProjectDocParser.clean_markdown(intro_path.read_text(encoding="utf-8"))
            out_name = intro_path.name
            has_resume = "## 简历描述" in merged
            intro_only += 1

        if has_resume:
            with_resume += 1
        else:
            without_resume += 1

        mark = "有简历描述" if has_resume else "降级全文  "
        _log.info(f"  [{mark}] {name}")
        if args.apply:
            (out_dir / out_name).write_text(merged, encoding="utf-8")

    _log.info(
        f"汇总：有「简历描述」章节 {with_resume} 份，走降级 {without_resume} 份，"
        f"仅介绍文档 {intro_only} 份"
    )

    if not args.apply:
        _log.info("预演结束（未写盘）。确认无误后加 --apply 执行。")
        return 0

    # 旧目录保留作备份，改名为 .bak 避免后续被再次扫描
    for old in (highlights_dir, intros_dir):
        if old.exists():
            backup = old.with_name(old.name + ".bak")
            if backup.exists():
                shutil.rmtree(backup)
            old.rename(backup)
            _log.info(f"旧目录已备份: {old.name} -> {backup.name}")

    _log.info("迁移完成。接下来重建索引：")
    _log.info("  python main.py ingest-projects --force")
    _log.info("  python main.py build-indexes --force")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
