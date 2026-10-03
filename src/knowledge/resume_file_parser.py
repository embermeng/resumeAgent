"""
简历文件解析模块
将用户上传的已有简历（md/txt/docx/pdf）解析为Markdown文本，
供深思路径生成简历时作为事实骨架（个人信息/教育/工作经历等真实内容）
"""
import io
import logging
import re
import tempfile
from pathlib import Path

_log = logging.getLogger(__name__)

# 支持的简历文件扩展名
SUPPORTED_EXTENSIONS = (".md", ".txt", ".docx", ".pdf")

# docx标题样式名（如 "Heading 1" / "标题 1"）中提取级别数字
_HEADING_LEVEL_PATTERN = re.compile(r"(\d+)")

# 手工排版章节标题判定：整段加粗的短行且无句末标点（如"教育背景""项目经验"）
_SECTION_MAX_LEN = 15
_SECTION_PUNCT_PATTERN = re.compile(r"[。，；：、！？,;:!?.]")


def is_bold_section_title(text: str, is_all_bold: bool, is_first: bool = False) -> bool:
    """
    判断docx普通段落是否为手工排版的章节标题（供解析与导出样式采样共用）
    条件：整段加粗 + 长度不超过阈值 + 不含句末标点；首行标题放宽长度限制（如"姓名_web前端开发"）
    """
    if not is_all_bold or not text:
        return False
    if _SECTION_PUNCT_PATTERN.search(text):
        return False
    limit = 25 if is_first else _SECTION_MAX_LEN
    return len(text) <= limit


class ResumeFileParser:
    """按文件类型解析已有简历为Markdown文本"""

    def __init__(self, pdf_parser=None):
        # PDFParser(MinerU)较重，懒加载；允许注入以便测试mock
        self._pdf_parser = pdf_parser

    @property
    def pdf_parser(self):
        if self._pdf_parser is None:
            from src.knowledge.pdf_parser import PDFParser
            self._pdf_parser = PDFParser()
        return self._pdf_parser

    def parse(self, file_name: str, file_bytes: bytes) -> str:
        """
        解析简历文件
        参数:
            file_name: 文件名（用扩展名决定解析方法）
            file_bytes: 文件二进制内容
        返回:
            Markdown文本
        异常:
            ValueError: 不支持的类型或解析结果为空
        """
        ext = Path(file_name).suffix.lower()
        if ext in (".md", ".txt"):
            text = self._parse_text(file_bytes)
        elif ext == ".docx":
            text = self._parse_docx(file_bytes)
        elif ext == ".pdf":
            text = self._parse_pdf(file_name, file_bytes)
        else:
            raise ValueError(
                f"不支持的简历文件格式: {ext or '(无扩展名)'}，"
                f"仅支持 {'/'.join(SUPPORTED_EXTENSIONS)}"
            )

        if not text or not text.strip():
            raise ValueError(f"简历解析结果为空: {file_name}")
        _log.info(f"简历解析成功: {file_name} ({len(text)}字)")
        return text

    @staticmethod
    def _parse_text(file_bytes: bytes) -> str:
        """md/txt直接解码：utf-8优先，失败退gbk（Windows中文文本常见编码）"""
        for encoding in ("utf-8", "gbk"):
            try:
                return file_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        # 两种编码都失败时宽松解码，保证不中断
        return file_bytes.decode("utf-8", errors="replace")

    @staticmethod
    def _parse_docx(file_bytes: bytes) -> str:
        """docx解析：python-docx按文档顺序提取段落与表格，拼为Markdown"""
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        doc = Document(io.BytesIO(file_bytes))
        lines = []
        seen_text = False  # 是否已遇到非空段落（首行加粗短行视为文档主标题）
        # 按body子元素顺序遍历，保证段落与表格的原始排列
        for child in doc.element.body.iterchildren():
            tag = child.tag
            if tag.endswith("}p"):
                para = Paragraph(child, doc)
                text = para.text.strip()
                if not text:
                    continue
                style_name = (para.style.name or "") if para.style is not None else ""
                prefix = ResumeFileParser._docx_style_prefix(style_name)
                if not prefix and ResumeFileParser._para_is_all_bold(para):
                    # 手工排版章节标题：加粗短行转Markdown标题，使章节结构提取可用
                    level = 1 if not seen_text else 2
                    if is_bold_section_title(text, True, is_first=not seen_text):
                        prefix = "#" * level + " "
                seen_text = True
                lines.append(f"{prefix}{text}")
            elif tag.endswith("}tbl"):
                seen_text = True
                table = Table(child, doc)
                for row in table.rows:
                    cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
                    if any(cells):
                        lines.append("| " + " | ".join(cells) + " |")
        return "\n".join(lines)

    @staticmethod
    def _para_is_all_bold(para) -> bool:
        """段落非空run全部加粗才视为加粗段（部分加粗的正文行不算）"""
        runs = [r for r in para.runs if r.text.strip()]
        return bool(runs) and all(r.bold for r in runs)

    @staticmethod
    def _docx_style_prefix(style_name: str) -> str:
        """docx段落样式映射为Markdown前缀（标题→#，列表→-）"""
        lowered = style_name.lower()
        if lowered.startswith("heading") or style_name.startswith("标题"):
            match = _HEADING_LEVEL_PATTERN.search(style_name)
            level = int(match.group(1)) if match else 1
            return "#" * min(max(level, 1), 6) + " "
        if lowered.startswith("list bullet") or "项目符号" in style_name:
            return "- "
        return ""

    def _parse_pdf(self, file_name: str, file_bytes: bytes) -> str:
        """pdf解析：字节写入临时文件后复用PDFParser（MinerU）"""
        suffix = Path(file_name).suffix or ".pdf"
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir) / f"resume{suffix}"
            tmp_path.write_bytes(file_bytes)
            try:
                return self.pdf_parser.parse_single(tmp_path)
            except Exception as e:
                _log.error(f"简历PDF解析失败: {file_name}, error={e}")
                raise ValueError(f"简历PDF解析失败: {file_name}: {e}") from e
