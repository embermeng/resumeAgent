"""
项目文档格式归一化（上传预处理的第 2 层）。

触发判据只有一个：**是否符合项目文档模板**（src/knowledge/templates/project_doc_template.md）。
不列举"有什么问题"——格式问题的形态是无限的，用代码穷举必然不断追赶；
只需问一个有限的问题："这份文档达标了吗？"，不达标就交给模型按模板重写。

安全边界：
- 提示词硬约束禁止编造（见 project_normalize_prompts），因为简历不能虚构；
- 任何失败（调用异常、输出为空、输出缺一级标题）都**回退原文**，绝不因归一化失败而拒收文档；
- 原文始终保留，调用方需能同时拿到原文本与归一化结果。
"""
import logging
import re

from dataclasses import dataclass

from src.knowledge.doc_template import matches_template
from src.prompts.project_normalize_prompts import (
    PROJECT_NORMALIZE_USER,
    build_system_prompt,
)

_log = logging.getLogger(__name__)

# 模型偶尔仍用代码围栏包裹输出。提示词已明令禁止，这里把它兑现掉——
# 对象是自家模型的输出契约（形态确定），而非用户输入的未知格式，
# 与"不用规则修用户文档"并不冲突。
_FENCE_WRAPPER = re.compile(r"^\s*```(?:markdown|md)?\s*\n(.*?)\n```\s*$", re.DOTALL)
_H1_PATTERN = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


@dataclass
class NormalizeResult:
    """归一化结果。normalized 为最终采用文本，applied=False 表示未改动或已回退。"""

    original: str
    normalized: str
    applied: bool
    reason: str


def needs_normalize(text: str) -> bool:
    """是否需要走 LLM：不符合模板即需要。"""
    return not matches_template(text)


def _strip_fence(text: str) -> str:
    m = _FENCE_WRAPPER.match(text.strip())
    return m.group(1).strip() if m else text.strip()


def normalize_project_doc(
    raw_text: str,
    fallback_name: str,
    config=None,
) -> NormalizeResult:
    """
    把不符合模板的文档整理成模板格式。失败一律回退原文。
    """
    original = raw_text.strip()

    if not needs_normalize(original):
        return NormalizeResult(
            original=original, normalized=original, applied=False,
            reason="符合项目文档模板，无需整理",
        )

    if config is None:
        from src.config import get_config
        config = get_config()

    try:
        from src.api_client import APIProcessor

        api = APIProcessor(provider=config.llm.provider)
        result = api.send_message(
            model=config.llm.model,
            temperature=0.2,   # 低温度：本环节要稳定复述，不要创造性
            system_content=build_system_prompt(),
            human_content=PROJECT_NORMALIZE_USER.format(
                fallback_name=fallback_name, raw_text=original,
            ),
        )
    except Exception as e:
        _log.error(f"项目文档归一化调用失败，回退原文: {e}")
        return NormalizeResult(
            original=original, normalized=original, applied=False,
            reason=f"LLM 调用失败: {e}",
        )

    if not isinstance(result, dict) or result.get("parse_error"):
        _log.error("项目文档归一化解析失败，回退原文")
        return NormalizeResult(
            original=original, normalized=original, applied=False,
            reason="LLM 输出解析失败",
        )

    content = (result.get("content") or "").strip()
    content = _strip_fence(content)

    # 兜底校验：归一化结果必须能提取出项目名，否则视为不可用
    if not content or not _H1_PATTERN.search(content):
        _log.warning("项目文档归一化结果缺少一级标题，回退原文")
        return NormalizeResult(
            original=original, normalized=original, applied=False,
            reason="归一化结果缺少一级标题",
        )

    _log.info(f"项目文档已归一化: {len(original)} -> {len(content)} 字符")
    return NormalizeResult(
        original=original, normalized=content, applied=True, reason="ok",
    )
