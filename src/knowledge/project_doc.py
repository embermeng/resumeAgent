"""
项目文档模块（原「项目亮点」+「项目介绍」合并）

一份 .md 描述一个项目，两种消费方式：
- 简历生成：按「简历描述 → 技术栈 → 量化成果 → 亮点摘句」的优先级拼成素材（见 build_material），
  受单份 token 预算约束；无「简历描述」章节时用一句话简介顶位
- 知识问答：全部二级标题章节分块入库（category=project），走混合检索

文档约定:
- 一级标题 "# 项目名"（项目名的唯一来源）
- 一级标题后可带标签行 "> 标签：简历优先"（必选项目）
- "## 一句话简介" 供轻量目录与 LLM 挑选
- "## 简历描述" 简历素材的开头一节，缺失时降级为一句话简介
- 其余 "##" 章节分块入库

设计要点:
- doc_id 带 "project-" 前缀，避免与课程 PDF 的 doc_id（纯 hex）冲突
- 多用户下 doc_id 再带 "u{user_id}-" 段，避免同名项目互相覆盖
- 按二级标题切块，每块拼上项目名，分块脱离上下文也知道归属
- 超长章节进一步按 token 细分，保证单块不撑爆 embedding 上下文
- 增量判断用文件 mtime（与索引层 mtime 策略一致）
- 项目名重名检测：后放的文档不覆盖先入库的，避免脏数据
- 支持文档内标签行（一级标题后的"标签：xxx"），存入 metainfo.tags，
  检索层可据此给重点定向加权（如"简历优先"）
"""
import json
import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.database.utils.project_docs import upsert_project_doc

_log = logging.getLogger(__name__)

# 项目亮点文档里的"简历优先"标签：介绍文档沿用同一约定（必选项目）
PRIORITY_TAG = "简历优先"

# 一级标题：提取项目名
_H1_PATTERN = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
# 章节标题：二级及以上任意层级都算切分点。
# 实测发现只认 `##` 会让用 `###` 分节的文档退化成"整篇一块"，检索质量很差，故放宽层级。
_HEADING_PATTERN = re.compile(r"^#{2,6}\s+(.+?)\s*$", re.MULTILINE)
# 标签行：一级标题后、第一个章节标题前的"标签：xxx"（可带引用符号），多个标签用、/,/，等分隔
_TAG_PATTERN = re.compile(r"^(?:>\s*)?标签[:：]\s*(.+?)\s*$", re.MULTILINE)
_TAG_SPLIT_PATTERN = re.compile(r"[、,，;；/\s]+")
# "一句话简介"章节标题（兼容"简介"变体）
_SUMMARY_TITLE_PATTERN = re.compile(r"^#{2,6}\s*(一句话简介|项目简介|简介)\s*$", re.MULTILINE)
# "简历描述"章节：简历生成引用区（缺失时降级为全文）
# 只认明确的"简历"字样；"项目描述"太通用（亮点文档常拿它当概述），不纳入以免误匹配
_RESUME_TITLE_PATTERN = re.compile(r"^#{2,6}\s*(简历描述|简历表述)\s*$", re.MULTILINE)
# 章节过长时进一步细分的token阈值
_SECTION_MAX_TOKENS = 400
# 素材拼接只看二级标题：更深的层级属于章节内部排版，不应把一整节切碎
_H2_PATTERN = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)

# 与 _RESUME_TITLE_PATTERN 同一组别名（一处改两处需同步）
_RESUME_ALIASES = ("简历描述", "简历表述")

# 单份文档进简历生成 prompt 的素材预算。旧口径只引用「简历描述」一节，
# 那是百余字的定稿措辞，技术栈/成果/亮点全被关在门外——一万字的素材实际只进 180 字，
# 模型看不到新做的后端体系，生成的简历就翻不出新内容。
MATERIAL_MAX_TOKENS = int(os.getenv("RESUME_MATERIAL_MAX_TOKENS", "1500"))
# 剩余预算小于此值就不再尝试塞下一节（避免截出只有几个字的残块）
_MATERIAL_MIN_LEFT_TOKENS = 60
# 摘句式章节每条保留的字数上限
_MATERIAL_ITEM_MAX_CHARS = 120

# 素材章节优先级。headlines 模式对长章节每条只取首句，同样预算内能覆盖更多条目。
# 「个人职责与贡献」排在「技术亮点」前：前者已是简历"责任描述"字段的成品表述，
# 后者是过程性论证；预算只够一边时先保住能直接落笔的那份。
_MATERIAL_SECTIONS = (
    ("full", ("技术栈", "技术选型")),
    ("full", ("量化成果", "成果")),
    ("full", ("个人职责与贡献", "个人职责", "职责与贡献", "我的职责")),
    ("headlines", ("技术亮点", "亮点")),
    ("headlines", ("核心功能", "功能")),
    ("full", ("项目难点", "难点")),
)


class ProjectDocParser:
    """项目文档纯文本解析（无状态，全部静态方法）"""

    @staticmethod
    def clean_markdown(text: str) -> str:
        """
        只做首尾空白清理。

        **不用规则修正用户文档的格式**（不剥代码围栏、不改写标题层级）——
        那些交给 project_normalizer 按模板处理。这里若动手去修，反而会把
        不合模板的文档"修成"看似合规的样子，导致本该交给模型整理的被放过。

        BOM 例外：它是编码层面的字节标记而非文档格式，会让标题正则失配
        （Windows 记事本保存的 md 普遍带 BOM，不剥会导致这些文档全被判为
        不合模板，白白跑一遍大模型）。
        """
        return text.lstrip("\ufeff").strip()

    @staticmethod
    def extract_project_name(text: str, fallback: str = "") -> str:
        """从一级标题提取项目名；无标题时返回fallback（空串表示提取失败）"""
        m = _H1_PATTERN.search(text)
        if m:
            return m.group(1).strip()
        return fallback

    @staticmethod
    def project_name_from_filename(md_path: Path) -> str:
        """
        从文件名推断项目名（无一级标题时的兜底）。
        去掉常见后缀（-项目亮点/-项目介绍）与扩展名，空结果回退为文件名主干。
        """
        stem = Path(md_path).stem.strip()
        for suffix in ("-项目亮点", "-项目介绍", "-亮点", "-介绍"):
            if stem.endswith(suffix) and len(stem) > len(suffix):
                stem = stem[: -len(suffix)].strip()
                break
        return stem or Path(md_path).stem or "未命名项目"

    @staticmethod
    def make_doc_id(project_name: str, user_id: Optional[int] = None) -> str:
        """
        doc_id = project-[u{user_id}-] + 项目名哈希，避免与课程文档（纯hex）冲突。
        user_id 为 None 表示公共素材（不带 u 段，兼容单用户时期已入库的文档）。
        """
        digest = hashlib.md5(project_name.encode("utf-8")).hexdigest()[:12]
        return f"project-u{user_id}-{digest}" if user_id is not None else f"project-{digest}"

    @staticmethod
    def extract_tags(text: str) -> Tuple[List[str], str]:
        """
        提取文档头部标签行，返回 (去重后的标签列表, 移除标签行后的文本)。
        只认第一个二级标题之前的标签行，避免误伤正文；标签行不进分块，
        标签信息统一存metainfo.tags，检索层按doc_id维度使用。
        """
        h2 = _HEADING_PATTERN.search(text)
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

    @staticmethod
    def extract_summary(text: str) -> str:
        """提取"一句话简介"章节首段；无该章节时退化为一级标题后的首段正文"""
        m = _SUMMARY_TITLE_PATTERN.search(text)
        if m:
            start = m.end()
            nxt = _HEADING_PATTERN.search(text, start)
            body = text[start:nxt.start() if nxt else len(text)]
        else:
            h1 = _H1_PATTERN.search(text)
            body = text[h1.end():] if h1 else text

        for para in body.split("\n\n"):
            para = para.strip()
            if para:
                return para.replace("\n", " ")
        return ""

    @staticmethod
    def extract_resume_section(text: str) -> Optional[str]:
        """
        提取"简历描述"章节（含标题行），供简历生成直接引用。
        无该章节返回 None；resume_text 字段据此降级为全文，
        素材通道 build_material 则降级为一句话简介（不灌全文）。
        """
        m = _RESUME_TITLE_PATTERN.search(text)
        if not m:
            return None
        nxt = _HEADING_PATTERN.search(text, m.end())
        end = nxt.start() if nxt else len(text)
        section = text[m.start():end].strip()
        return section or None

    @staticmethod
    def iter_sections(text: str) -> List[Tuple[str, str]]:
        """按二级标题切分文档，返回 [(章节名, 正文)]；正文不含标题行，空节跳过"""
        matches = list(_H2_PATTERN.finditer(text))
        sections: List[Tuple[str, str]] = []
        for i, m in enumerate(matches):
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            body = text[start:end].strip()
            if body:
                sections.append((m.group(1).strip(), body))
        return sections

    @staticmethod
    def find_section(
        sections: List[Tuple[str, str]], aliases: Tuple[str, ...]
    ) -> Optional[Tuple[str, str]]:
        """
        在章节列表里按别名定位，返回 (章节名, 正文)。
        先按别名顺序找完全同名，全都没有再找包含关系（兼容"项目技术栈"这类写法）。
        """
        for alias in aliases:
            for title, body in sections:
                if title == alias:
                    return title, body
        for alias in aliases:
            for title, body in sections:
                if alias in title:
                    return title, body
        return None

    @staticmethod
    def section_headlines(body: str) -> str:
        """
        长章节摘句：每条只留首句，让预算覆盖更多条目而非一条读到底。
        兼容 `- **标签**：整段说明` 与 `-  plain 描述` 两种写法，标题行也能处理。
        """
        lines: List[str] = []
        for raw in body.split("\n"):
            line = re.sub(r"\s+", " ", raw).strip()
            line = re.sub(r"^#+\s*", "", line).lstrip("- ").strip()
            if not line or line in ("---", "***"):
                continue
            first = re.split(r"[。；;]", line, maxsplit=1)[0].strip()
            snippet = first if len(first) >= 8 else line
            if len(snippet) > _MATERIAL_ITEM_MAX_CHARS:
                snippet = snippet[:_MATERIAL_ITEM_MAX_CHARS].rstrip() + "…"
            lines.append(f"- {snippet}")
        return "\n".join(lines)


class ProjectDocScanner:
    """扫描项目文档目录，产出轻量目录条目（项目名/标签/简介/简历素材/全文）"""

    def __init__(self, material_max_tokens: Optional[int] = None, splitter=None):
        self.material_max_tokens = (
            MATERIAL_MAX_TOKENS if material_max_tokens is None else material_max_tokens
        )
        # splitter 只为数 token 而存在，懒加载：不生成素材的调用方不必付 tiktoken 的导入代价
        self._splitter = splitter

    def _count_tokens(self, text: str) -> int:
        if self._splitter is None:
            from src.knowledge.text_splitter import TextSplitter
            self._splitter = TextSplitter()
        try:
            return self._splitter.count_tokens(text)
        except Exception as e:
            # 计数失败宁可高估（按字符数），退化成"少给一点"而不是"爆预算"
            _log.warning(f"素材 token 计数失败，按字符数估算: {e}")
            return len(text)

    def build_material(self, text: str) -> str:
        """
        组装单份文档的简历素材：简历描述打头，其余章节按 _MATERIAL_SECTIONS 优先级跟上，
        总量受 token 预算约束；装不下的最后一节按字符比例截断，保住开头而不是整节丢弃。

        输出形如 `【技术栈】\n...`，字段名显式写在块头——模型据此判断这段是能力清单还是
        成果数据，才能把它们放进简历对应的位置，而不是当成一整段经历描述复制过去。
        """
        sections = ProjectDocParser.iter_sections(text)
        candidates: List[Tuple[str, str]] = []

        resume = ProjectDocParser.find_section(sections, _RESUME_ALIASES)
        if resume is not None:
            candidates.append(resume)
        else:
            # 无简历描述时只让一句话简介顶位，不降级为全文：
            # 全文兜底等于把万字文档整篇灌进 prompt，预算形同虚设
            summary = ProjectDocParser.extract_summary(text)
            if summary:
                candidates.append(("项目简介", summary))

        for mode, aliases in _MATERIAL_SECTIONS:
            hit = ProjectDocParser.find_section(sections, aliases)
            if hit is None:
                continue
            title, body = hit
            if mode == "headlines":
                body = ProjectDocParser.section_headlines(body)
            if body:
                candidates.append((title, body))

        blocks: List[str] = []
        used = 0
        for title, body in candidates:
            block = f"【{title}】\n{body}"
            sep = 2 if blocks else 0  # 块之间的空行也占预算，不算会累积超支
            cost = self._count_tokens(block)
            if used + sep + cost <= self.material_max_tokens:
                blocks.append(block)
                used += sep + cost
                continue
            left = self.material_max_tokens - used - sep
            if left < _MATERIAL_MIN_LEFT_TOKENS:
                break
            # 放不下整节：按字符比例截断，保住这节的开头而不是整节丢弃
            keep = max(1, int(len(block) * (left - 1) // max(cost, 1)))
            blocks.append(block[:keep].rstrip() + "…")
            break
        return "\n\n".join(blocks)

    def scan(self, docs_dir: Path) -> List[Dict]:
        """
        遍历目录下所有 .md，解析为条目列表（按文件名排序，重名项目跳过后放的）
        条目结构: {"name", "tags", "summary", "text", "path",
                   "resume_section", "resume_text", "has_resume_section", "material"}
        """
        if not docs_dir.exists():
            return []

        entries: List[Dict] = []
        seen_names = set()
        for md_path in sorted(Path(docs_dir).glob("*.md")):
            try:
                raw = md_path.read_text(encoding="utf-8")
            except Exception as e:
                _log.warning(f"项目文档读取失败: {md_path.name}: {e}")
                continue

            text = ProjectDocParser.clean_markdown(raw)
            name = ProjectDocParser.extract_project_name(text)
            if not name:
                _log.warning(f"跳过项目文档（无一级标题）: {md_path.name}")
                continue
            if name in seen_names:
                _log.warning(f"项目文档项目名重复，跳过: {md_path.name}（{name}）")
                continue
            seen_names.add(name)

            tags, text = ProjectDocParser.extract_tags(text)
            resume_section = ProjectDocParser.extract_resume_section(text)
            entries.append({
                "name": name,
                "tags": tags,
                "summary": ProjectDocParser.extract_summary(text),
                "text": text,
                "path": md_path,
                # 简历生成引用区：有"简历描述"章节用之，否则降级为全文
                "resume_section": resume_section,
                "resume_text": resume_section if resume_section else text,
                "has_resume_section": resume_section is not None,
                # 简历生成实际取用的素材：多章节按优先级拼接并限长
                "material": self.build_material(text),
            })
        return entries

    def scan_visible(self, projects_dir: Path, user_id: Optional[int]) -> List[Dict]:
        """
        扫描该用户可见的项目文档 = 公共素材（根目录下）+ 本人私有（u{user_id}/）。
        同名项目以私有为准（用户自己的版本优先于公共示例）。
        """
        entries = []
        seen_names = set()

        if user_id is not None:
            for e in self.scan(projects_dir / f"u{user_id}"):
                entries.append(e)
                seen_names.add(e["name"])

        for e in self.scan(projects_dir):
            if e["name"] in seen_names:
                _log.warning(f"公共素材与私有文档重名，以私有为准: {e['name']}")
                continue
            entries.append(e)
        return entries

    @staticmethod
    def build_catalog(entries: List[Dict]) -> str:
        """轻量目录文本（供 LLM 挑选）：项目名 | 标签 | 一句话简介"""
        lines = []
        for i, e in enumerate(entries, 1):
            tag_mark = f" [{PRIORITY_TAG}]" if PRIORITY_TAG in e["tags"] else ""
            summary = e["summary"] or "（无简介）"
            lines.append(f"{i}. {e['name']}{tag_mark} —— {summary}")
        return "\n".join(lines)


class ProjectDocIngestor:
    """项目文档入库：读取.md -> 清洗 -> 按标题分块 -> 写出project类别分块JSON"""

    def __init__(self, splitter=None):
        # 延迟导入，避免循环依赖
        if splitter is None:
            from src.knowledge.text_splitter import TextSplitter
            splitter = TextSplitter()
        self.splitter = splitter

    def split_by_headings(self, text: str, project_name: str) -> List[str]:
        """
        按二级标题切块，每块拼上项目名前缀；超长章节再按token细分。
        无二级标题的文档整体按token分块兜底。
        """
        matches = list(_HEADING_PATTERN.finditer(text))
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
        tags: Optional[List[str]] = None, user_id: Optional[int] = None,
    ) -> Dict:
        """构建与课程分块一致格式的文档结构（category=project，可带标签）"""
        chunks = self.split_by_headings(text, project_name)
        return {
            "metainfo": {
                "doc_id": ProjectDocParser.make_doc_id(project_name, user_id),
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

    # ----------------------------------------------------------
    # 单文档入库（Web 上传主路径；批量 ingest 复用本方法）
    # ----------------------------------------------------------
    def parse_text(
        self, raw: str, fallback_name: Optional[str] = None
    ) -> Optional[Dict]:
        """
        解析文档文本，返回 {"text", "project_name", "tags", "name_from_fallback"}。

        fallback_name 是无一级标题时的兜底项目名。此前无 H1 一律判失败——
        用户随手写的纯文本项目说明会被整个拒收，而这本可以靠名字兜住。
        为 None 时保持严格语义（拒绝无法确定项目名的文档）。

        去重口径仍是项目名（doc_id 由它决定），只是项目名的来源多了兜底一路。
        """
        text = ProjectDocParser.clean_markdown(raw)
        project_name = ProjectDocParser.extract_project_name(text)
        name_from_fallback = False
        if not project_name:
            if not fallback_name:
                _log.warning("跳过（无一级标题且无兜底名称，无法确定项目名）")
                return None
            # 只取文件名部分(防上传文件名里混入路径),并按同一规则去后缀
            project_name = ProjectDocParser.project_name_from_filename(
                Path(Path(fallback_name).name)) or "未命名项目"
            name_from_fallback = True
            _log.info(f"无一级标题，项目名回退为: {project_name}")

        tags, text = ProjectDocParser.extract_tags(text)
        return {
            "text": text,
            "project_name": project_name,
            "tags": tags,
            "name_from_fallback": name_from_fallback,
        }

    def parse_file(self, md_path: Path, fallback_name: bool = True) -> Optional[Dict]:
        """
        解析单个 .md。读取失败返回 None。
        fallback_name=True 时以文件名主干作为无一级标题时的兜底项目名。
        """
        try:
            raw = md_path.read_text(encoding="utf-8")
        except Exception as e:
            _log.warning(f"读取失败: {md_path.name}: {e}")
            return None

        return self.parse_text(
            raw,
            fallback_name=ProjectDocParser.project_name_from_filename(md_path)
            if fallback_name else None,
        )

    def ingest_file(
        self,
        md_path: Path,
        chunks_dir: Path,
        user_id: Optional[int] = None,
        persist: bool = True,
    ) -> Optional[str]:
        """
        单文档入库：解析 -> 写分块JSON -> 登记 project_docs。
        同名项目（同 user_id）直接覆盖——上传即"重新上传修正内容"，不做增量跳过。
        返回 doc_id；失败返回 None。
        """
        parsed = self.parse_file(md_path)
        if parsed is None:
            return None

        project_name = parsed["project_name"]
        doc_id = ProjectDocParser.make_doc_id(project_name, user_id)
        chunk_path = chunks_dir / f"{doc_id}.json"

        chunks_dir.mkdir(parents=True, exist_ok=True)
        doc_data = self.build_doc_data(
            parsed["text"], project_name, md_path.name,
            md_path.stat().st_mtime, tags=parsed["tags"], user_id=user_id,
        )

        # 原子写入，防止中断产生损坏的分块文件
        tmp_path = chunk_path.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(doc_data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, chunk_path)

        if persist:
            upsert_project_doc(
                doc_id=doc_id,
                project_name=project_name,
                file_name=md_path.name,
                source_path=str(md_path),
                user_id=user_id,
                tags=parsed["tags"],
                has_resume_section=ProjectDocParser.extract_resume_section(parsed["text"]) is not None,
                mtime=md_path.stat().st_mtime,
            )

        _log.info(
            f"已入库项目文档: {project_name} "
            f"({len(doc_data['content']['chunks'])} chunks, doc_id={doc_id})"
        )
        return doc_id

    def ingest(
        self,
        docs_dir: Path,
        chunks_dir: Path,
        force: bool = False,
        user_id: Optional[int] = None,
        persist: bool = True,
    ) -> Dict[str, int]:
        """
        批量转换项目文档为分块JSON写入chunks_dir，并登记到 project_docs 表。

        persist=False 时只写分块不落库（单测用，避免污染开发库）。
        返回统计: {"total": 总数, "ingested": 入库数, "skipped": 跳过数,
                   "failed": 失败数, "duplicate": 重名数}
        """
        chunks_dir.mkdir(parents=True, exist_ok=True)
        md_files = sorted(docs_dir.glob("*.md"))
        stats = {"total": len(md_files), "ingested": 0, "skipped": 0, "failed": 0, "duplicate": 0}

        seen_projects: Dict[str, str] = {}  # 项目名 -> 首个来源文件名
        for md_path in md_files:
            parsed = self.parse_file(md_path)
            if parsed is None:
                stats["failed"] += 1
                continue

            text = parsed["text"]
            project_name = parsed["project_name"]
            tags = parsed["tags"]

            # 重名检测：先入库的优先，后放的跳过
            if project_name in seen_projects:
                _log.warning(
                    f"项目名重复: '{project_name}' 已入库自 {seen_projects[project_name]}，"
                    f"跳过 {md_path.name}"
                )
                stats["duplicate"] += 1
                continue
            seen_projects[project_name] = md_path.name

            doc_id = ProjectDocParser.make_doc_id(project_name, user_id)
            chunk_path = chunks_dir / f"{doc_id}.json"
            if not self._need_rebuild(chunk_path, md_path, force):
                stats["skipped"] += 1
                continue

            doc_data = self.build_doc_data(
                text, project_name, md_path.name, md_path.stat().st_mtime,
                tags=tags, user_id=user_id,
            )

            # 原子写入，防止中断产生损坏的分块文件
            tmp_path = chunk_path.with_suffix(".json.tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(doc_data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, chunk_path)

            if persist:
                upsert_project_doc(
                    doc_id=doc_id,
                    project_name=project_name,
                    file_name=md_path.name,
                    source_path=str(md_path),
                    user_id=user_id,
                    tags=tags,
                    has_resume_section=ProjectDocParser.extract_resume_section(text) is not None,
                    mtime=md_path.stat().st_mtime,
                )

            stats["ingested"] += 1
            tag_info = f"，标签: {', '.join(tags)}" if tags else ""
            _log.info(
                f"已入库项目文档: {project_name} ({len(doc_data['content']['chunks'])} chunks{tag_info})"
            )

        _log.info(
            f"项目文档入库完成: 共{stats['total']}份，入库{stats['ingested']}，"
            f"跳过{stats['skipped']}，失败{stats['failed']}，重名{stats['duplicate']}"
        )
        return stats
