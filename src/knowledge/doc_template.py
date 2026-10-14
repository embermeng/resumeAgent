"""
项目文档模板：格式标准的**唯一真理来源**。

同一份 templates/project_doc_template.md 同时充当三个角色：
1. 用户下载后填写的空白模板（后续 GET /api/project/template）
2. LLM 归一化时下发的标准格式
3. 上传文档的合规校验基准

因此增删章节、调整是否必填，只需改那一个 md 文件，本模块无需改动。

设计原则：**只判断达标与否，不诊断具体问题，更不用规则去修**。
不合规一律交给 LLM 按模板重写——格式问题的形态是无限的，
用代码穷举只会不断追赶；而"是否符合模板"是一个有限的、可回答的问题。
"""
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import List, Tuple

_log = logging.getLogger(__name__)

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "project_doc_template.md"

_H1_PATTERN = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
# 二级标题；行尾可用 HTML 注释标注 optional，注释不参与章节名
_SECTION_PATTERN = re.compile(r"^##\s+(.+?)\s*(?:<!--(.*?)-->)?\s*$", re.MULTILINE)
# 三级及以下标题：标准格式只有 # 和 ## 两级
_DEEP_HEADING_PATTERN = re.compile(r"^#{3,6}\s+", re.MULTILINE)
# 代码块围栏：简历文档不写代码
_FENCE_PATTERN = re.compile(r"^\s*```", re.MULTILINE)

_OPTIONAL_MARK = "optional"


@lru_cache(maxsize=1)
def template_path() -> Path:
    """模板文件路径（供下载端点与测试使用）"""
    return _TEMPLATE_PATH


@lru_cache(maxsize=1)
def load_template() -> str:
    """读取模板原文。供下载、提示词下发共用同一份文本。"""
    return _TEMPLATE_PATH.read_text(encoding="utf-8").strip()


@lru_cache(maxsize=1)
def template_sections() -> Tuple[Tuple[str, bool], ...]:
    """
    从模板解析章节定义，返回有序元组 ((章节名, 是否可选), ...)。
    模板里行尾带 <!-- optional --> 的章节允许省略。
    """
    sections = []
    for m in _SECTION_PATTERN.finditer(load_template()):
        note = (m.group(2) or "").strip().lower()
        sections.append((m.group(1).strip(), _OPTIONAL_MARK in note))
    return tuple(sections)


def required_sections() -> List[str]:
    """必填章节名（按模板顺序）"""
    return [name for name, optional in template_sections() if not optional]


def _is_subsequence(seq: List[str], superset: List[str]) -> bool:
    """seq 是否为 superset 的保序子序列（用于校验章节顺序）"""
    it = iter(superset)
    return all(item in it for item in seq)


def matches_template(text: str) -> bool:
    """
    判断文档是否严格符合模板格式。**只判断，不修改**。

    全部为正向断言——描述"合模板的文档长什么样"，不涉及任何具体缺陷类型，
    因此未见过的新形态噪音（AI 开场白、抄来的时间戳、奇怪的包裹）只要破坏了
    下面任一条件，就会自然落到"不合规"，不需要为它们单独写规则。

    断言：
    1. 首行即 `# 项目名`（前置的任何内容都会让它失配）
    2. 标题只有 # 与 ## 两级
    3. 无代码围栏
    4. 必填章节齐全
    5. 模板章节的出现顺序与模板一致（允许插入自定义章节）
    """
    text = text.strip()
    if not text:
        return False

    # 1. 必须以一级标题开头
    if not _H1_PATTERN.match(text):
        return False

    # 2. 不得出现三级及以下标题
    if _DEEP_HEADING_PATTERN.search(text):
        return False

    # 3. 不得有代码块（简历文档不该写代码）
    if _FENCE_PATTERN.search(text):
        return False

    # 4/5. 必填章节齐全且顺序一致
    doc_sections = [m.group(1).strip() for m in _SECTION_PATTERN.finditer(text)]
    tpl_names = [name for name, _ in template_sections()]

    unseen = set(required_sections()) - set(doc_sections)
    if unseen:
        _log.debug(f"缺少必填章节: {sorted(unseen)}")
        return False

    # 模板章节在文档中的出现顺序需与模板一致（自动忽略文档里的自定义章节）
    ordered = []
    for name in doc_sections:
        if name in tpl_names and (not ordered or ordered[-1] != name):
            ordered.append(name)
    if not _is_subsequence(ordered, tpl_names):
        _log.debug(f"章节顺序与模板不一致: 文档 {ordered} vs 模板 {tpl_names}")
        return False

    return True
