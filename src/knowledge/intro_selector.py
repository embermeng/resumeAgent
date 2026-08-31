"""
项目介绍文档扫描与目录构建
介绍文档是用户离线生成的"简历措辞成品"（一个项目一份 .md），放在
knowledge_base/project_intros/ 下，不入库、不分块、不向量化；
生成简历时由 AgentTools 直接遍历挑选、读全文引用。

文档约定（与亮点文档一致）:
- 必须有一级标题 "# 项目名"（项目名的唯一来源）
- 一级标题后可带标签行 "> 标签：简历优先"（必选项目）
- 建议有 "## 一句话简介" 章节（供轻量目录与 LLM 挑选）
"""
import logging
import re
from pathlib import Path
from typing import Dict, List

_log = logging.getLogger(__name__)

# 项目亮点文档里的"简历优先"标签：介绍文档沿用同一约定（必选项目）
PRIORITY_TAG = "简历优先"

# "一句话简介"章节标题（兼容"简介"变体）
_SUMMARY_TITLE_PATTERN = re.compile(r"^##\s*(一句话简介|项目简介|简介)\s*$", re.MULTILINE)
_H2_PATTERN = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


class IntroSelector:
    """扫描项目介绍目录，产出轻量目录条目（项目名/标签/一句话简介/全文）"""

    def scan(self, intros_dir: Path) -> List[Dict]:
        """
        遍历目录下所有 .md，解析为条目列表（按文件名排序，重名项目跳过后放的）
        条目结构: {"name", "tags", "summary", "text", "path"}
        """
        from src.knowledge.highlight_ingestor import HighlightIngestor

        if not intros_dir.exists():
            return []

        entries: List[Dict] = []
        seen_names = set()
        for md_path in sorted(Path(intros_dir).glob("*.md")):
            try:
                raw = md_path.read_text(encoding="utf-8")
            except Exception as e:
                _log.warning(f"介绍文档读取失败: {md_path.name}: {e}")
                continue

            text = HighlightIngestor.clean_markdown(raw)
            name = HighlightIngestor.extract_project_name(text)
            if not name:
                _log.warning(f"跳过介绍文档（无一级标题）: {md_path.name}")
                continue
            if name in seen_names:
                _log.warning(f"介绍文档项目名重复，跳过: {md_path.name}（{name}）")
                continue
            seen_names.add(name)

            tags, text = HighlightIngestor.extract_tags(text)
            entries.append({
                "name": name,
                "tags": tags,
                "summary": self._extract_summary(text),
                "text": text,
                "path": md_path,
            })
        return entries

    @staticmethod
    def _extract_summary(text: str) -> str:
        """提取"一句话简介"章节首段；无该章节时退化为一级标题后的首段正文"""
        m = _SUMMARY_TITLE_PATTERN.search(text)
        if m:
            start = m.end()
            nxt = _H2_PATTERN.search(text, start)
            body = text[start:nxt.start() if nxt else len(text)]
        else:
            h1 = re.search(r"^#\s+.+?$", text, re.MULTILINE)
            body = text[h1.end():] if h1 else text

        for para in body.split("\n\n"):
            para = para.strip()
            if para:
                return para.replace("\n", " ")
        return ""

    @staticmethod
    def build_catalog(entries: List[Dict]) -> str:
        """轻量目录文本（供 LLM 挑选）：项目名 | 标签 | 一句话简介"""
        lines = []
        for i, e in enumerate(entries, 1):
            tag_mark = " [简历优先]" if PRIORITY_TAG in e["tags"] else ""
            summary = e["summary"] or "（无简介）"
            lines.append(f"{i}. {e['name']}{tag_mark} —— {summary}")
        return "\n".join(lines)
