"""
项目亮点文档入库模块
将用户生成的项目亮点README（.md）转换为带category="project"标签的分块文档，
写入course_chunks目录，由build-indexes统一建立BM25/FAISS索引。

设计要点:
- doc_id带"project-"前缀，避免与课程PDF的doc_id（纯hex）冲突，日志/调试一眼可辨
- 按二级标题切块（亮点文档结构规整），每块拼上项目名，分块脱离上下文也知道归属
- 超长章节进一步按token细分，保证单块不撑爆embedding上下文
- 增量判断用文件mtime（与索引层mtime策略一致），文档更新后重新入库即可
- 项目名重名检测：后放的文档不覆盖先入库的，避免脏数据
- 支持文档内标签行（一级标题后的“标签：xxx”），存入metainfo.tags，
  检索层可据此给重点定向加权（如“简历优先”）
"""
import json
import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_log = logging.getLogger(__name__)

# 一级标题：提取项目名
_H1_PATTERN = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
# 二级标题：章节切分点
_H2_PATTERN = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
# 包裹整个内容的markdown代码围栏（用户从AI对话复制时常见）
_FENCE_WRAPPER_PATTERN = re.compile(r"^\s*```(?:markdown|md)?\s*\n(.*?)\n```\s*$", re.DOTALL)
# 标签行：一级标题后、第一个二级标题前的“标签：xxx”（可带引用符号），多个标签用、/,/，等分隔
_TAG_PATTERN = re.compile(r"^(?:>\s*)?标签[:：]\s*(.+?)\s*$", re.MULTILINE)
_TAG_SPLIT_PATTERN = re.compile(r"[、,，;；/\s]+")
# 章节过长时进一步细分的token阈值
_SECTION_MAX_TOKENS = 400


class HighlightIngestor:
    """项目亮点文档转换器：读取.md -> 清洗 -> 按标题分块 -> 写出project类别分块JSON"""

    def __init__(self, splitter=None):
        # 延迟导入，避免循环依赖
        if splitter is None:
            from src.knowledge.text_splitter import TextSplitter
            splitter = TextSplitter()
        self.splitter = splitter

    # ----------------------------------------------------------
    # 文本清洗与解析
    # ----------------------------------------------------------
    @staticmethod
    def clean_markdown(text: str) -> str:
        """清理AI输出常见噪音：整体代码围栏包裹、首尾空白"""
        m = _FENCE_WRAPPER_PATTERN.match(text)
        if m:
            text = m.group(1)
        return text.strip()

    @staticmethod
    def extract_project_name(text: str, fallback: str = "") -> str:
        """从一级标题提取项目名；无标题时返回fallback（空串表示提取失败）"""
        m = _H1_PATTERN.search(text)
        if m:
            return m.group(1).strip()
        return fallback

    @staticmethod
    def make_doc_id(project_name: str) -> str:
        """doc_id = project- + 项目名哈希，避免与课程文档（纯hex）冲突"""
        return "project-" + hashlib.md5(project_name.encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def extract_tags(text: str) -> Tuple[List[str], str]:
        """
        提取文档头部标签行，返回 (去重后的标签列表, 移除标签行后的文本)。
        只认第一个二级标题之前的标签行，避免误伤正文；标签行不进分块，
        标签信息统一存metainfo.tags，检索层按doc_id维度使用。
        """
        h2 = _H2_PATTERN.search(text)
        head_end = h2.start() if h2 else len(text)

        tags: List[str] = []
        for m in _TAG_PATTERN.finditer(text, 0, head_end):
            for t in _TAG_SPLIT_PATTERN.split(m.group(1)):
                t = t.strip()
                if t and t not in tags:
                    tags.append(t)

        if not tags:
            return [], text

        def _strip(m):
            return "" if m.start() < head_end else m.group(0)

        stripped = _TAG_PATTERN.sub(_strip, text)
        stripped = re.sub(r"\n{3,}", "\n\n", stripped).strip()
        return tags, stripped

    def split_by_headings(self, text: str, project_name: str) -> List[str]:
        """
        按二级标题切块，每块拼上项目名前缀；超长章节再按token细分。
        无二级标题的文档整体按token分块兜底。
        """
        matches = list(_H2_PATTERN.finditer(text))
        chunks: List[str] = []

        if not matches:
            # 兜底：无章节结构，整体细分
            for c in self.splitter.split_text(text):
                chunks.append(f"【项目：{project_name}】{c['text']}")
            return chunks

        for i, m in enumerate(matches):
            title = m.group(1).strip()
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            body = text[start:end].strip()
            if not body:
                continue

            section_text = f"{title}\n{body}"
            if self.splitter.count_tokens(section_text) <= _SECTION_MAX_TOKENS:
                chunks.append(f"【项目：{project_name}】{section_text}")
            else:
                # 长章节细分，每小块仍带项目名与章节名，保证可溯源
                for c in self.splitter.split_text(body):
                    chunks.append(f"【项目：{project_name}｜{title}】{c['text']}")

        return chunks

    # ----------------------------------------------------------
    # 入库
    # ----------------------------------------------------------
    def build_doc_data(
        self, text: str, project_name: str, file_name: str, mtime: float,
        tags: Optional[List[str]] = None,
    ) -> Dict:
        """构建与课程分块一致格式的文档结构（category=project，可带标签）"""
        chunks = self.split_by_headings(text, project_name)
        return {
            "metainfo": {
                "doc_id": self.make_doc_id(project_name),
                "source": project_name,
                "category": "project",
                "file_name": file_name,
                "mtime": mtime,
                "tags": tags or [],
            },
            "content": {
                "chunks": [
                    {"id": i, "text": t, "length_tokens": self.splitter.count_tokens(t)}
                    for i, t in enumerate(chunks)
                ]
            },
        }

    def _need_rebuild(self, chunk_path: Path, md_path: Path, force: bool) -> bool:
        """增量判断：force / 分块结果不存在 / 源文件比已有记录新"""
        if force or not chunk_path.exists():
            return True
        try:
            with open(chunk_path, "r", encoding="utf-8") as f:
                old_mtime = json.load(f)["metainfo"].get("mtime", 0)
        except Exception:
            return True
        return md_path.stat().st_mtime > old_mtime

    def ingest(
        self,
        highlights_dir: Path,
        chunks_dir: Path,
        force: bool = False,
    ) -> Dict[str, int]:
        """
        批量转换亮点文档为分块JSON写入chunks_dir
        返回统计: {"total": 总数, "ingested": 入库数, "skipped": 跳过数,
                   "failed": 失败数, "duplicate": 重名数}
        """
        chunks_dir.mkdir(parents=True, exist_ok=True)
        md_files = sorted(highlights_dir.glob("*.md"))
        stats = {"total": len(md_files), "ingested": 0, "skipped": 0, "failed": 0, "duplicate": 0}

        seen_projects: Dict[str, str] = {}  # 项目名 -> 首个来源文件名
        for md_path in md_files:
            try:
                raw = md_path.read_text(encoding="utf-8")
            except Exception as e:
                _log.warning(f"读取失败: {md_path.name}: {e}")
                stats["failed"] += 1
                continue

            text = self.clean_markdown(raw)
            project_name = self.extract_project_name(text)
            if not project_name:
                _log.warning(f"跳过（无一级标题，无法确定项目名）: {md_path.name}")
                stats["failed"] += 1
                continue

            tags, text = self.extract_tags(text)

            # 重名检测：先入库的优先，后放的跳过
            if project_name in seen_projects:
                _log.warning(
                    f"项目名重复: '{project_name}' 已入库自 {seen_projects[project_name]}，"
                    f"跳过 {md_path.name}"
                )
                stats["duplicate"] += 1
                continue
            seen_projects[project_name] = md_path.name

            doc_id = self.make_doc_id(project_name)
            chunk_path = chunks_dir / f"{doc_id}.json"
            if not self._need_rebuild(chunk_path, md_path, force):
                stats["skipped"] += 1
                continue

            doc_data = self.build_doc_data(
                text, project_name, md_path.name, md_path.stat().st_mtime, tags=tags
            )

            # 原子写入，防止中断产生损坏的分块文件
            tmp_path = chunk_path.with_suffix(".json.tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(doc_data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, chunk_path)

            stats["ingested"] += 1
            tag_info = f"，标签: {', '.join(tags)}" if tags else ""
            _log.info(
                f"已入库项目亮点: {project_name} ({len(doc_data['content']['chunks'])} chunks{tag_info})"
            )

        _log.info(
            f"项目亮点入库完成: 共{stats['total']}份，入库{stats['ingested']}，"
            f"跳过{stats['skipped']}，失败{stats['failed']}，重名{stats['duplicate']}"
        )
        return stats
