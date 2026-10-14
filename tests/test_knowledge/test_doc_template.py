"""
模板合规校验测试。

核心守卫：**只判断达标与否，不诊断具体问题**。
因此用例的重点不是"某种缺陷能被识别"，而是"不达标的输入一律交给 LLM"——
包括从未预设过的形态（AI 开场白、复制时的时间戳等），它们应被正向断言自然挡下。
"""
from pathlib import Path

import pytest

from src.knowledge.doc_template import (
    matches_template,
    required_sections,
    template_sections,
)

# 符合模板的文档（含一个模板外的自定义章节，应放行）
CONFORMING = """# 智能问答助手

> 标签：简历优先

## 一句话简介
面向内部文档的检索增强问答助手。

## 简历描述
负责检索链路的设计与实现。

## 技术栈
Python、FAISS

## 检索关键词
RAG、混合检索
"""


class TestTemplateDefinition:
    def test_sections_parsed_from_template_file(self):
        """章节定义必须来自模板文件，代码里不另写一份"""
        names = [n for n, _ in template_sections()]
        assert "一句话简介" in names
        assert "简历描述" in names

    def test_required_and_optional_split(self):
        optional = {n for n, opt in template_sections() if opt}
        assert "简历描述" not in optional, "简历描述缺失则简历无可引用内容，必须必填"
        assert required_sections() == ["一句话简介", "简历描述"]

    def test_optional_mark_not_part_of_name(self):
        """<!-- optional --> 只作为标记，不能混进章节名"""
        for name, _ in template_sections():
            assert "optional" not in name
            assert "<!--" not in name


class TestMatchesTemplate:
    def test_conforming_doc_passes(self):
        assert matches_template(CONFORMING) is True

    def test_allows_extra_custom_section(self):
        """模板外的自定义章节不应阻止合规——那是用户自己的内容"""
        assert matches_template(CONFORMING) is True

    def test_empty_text_fails(self):
        assert matches_template("") is False

    # ---- 以下用例验证"未知形态"也被挡下，而非针对某个已知缺陷 ----

    @pytest.mark.parametrize("prefix", [
        "```markdown\n",                    # 代码围栏包裹
        "好的，这是整理好的文档：\n",          # AI 开场白
        "2026-09-01 复制\n",                # 时间戳
        "> 引用块开头\n",                     # 前置引用
        "以下是项目内容\n\n",                 # 口语化引导语
    ])
    def test_leading_noise_fails(self, prefix):
        """文档必须以 H1 开头——一条断言覆盖所有形态的前置噪音"""
        assert matches_template(prefix + CONFORMING) is False

    def test_trailing_note_after_fence_fails(self):
        """围栏后还跟着说明文字（曾漏判的真实样例）"""
        wrapped = "```markdown\n" + CONFORMING + "\n```\n\n上面是从AI对话复制的。"
        assert matches_template(wrapped) is False

    def test_deep_heading_level_fails(self):
        """标题只能有 # 与 ## 两级；### 及以上不合模板"""
        assert matches_template("# P\n\n### 技术栈\nPython\n") is False

    def test_missing_required_section_fails(self):
        assert matches_template("# P\n\n## 技术栈\nPython\n") is False

    def test_missing_resume_section_fails(self):
        """缺「简历描述」：结构再规范，简历主路径也无可引用内容"""
        assert matches_template(
            "# P\n\n## 一句话简介\n做推荐的系统。\n\n## 技术栈\nPython\n"
        ) is False

    def test_missing_intro_section_fails(self):
        assert matches_template("# P\n\n## 简历描述\n负责召回层。\n") is False

    def test_wrong_section_order_fails(self):
        """简历描述排在简介之前：顺序不合模板"""
        assert matches_template(
            "# P\n\n## 简历描述\n负责召回层。\n\n## 一句话简介\n做推荐的系统。\n"
        ) is False

    def test_code_block_fails(self):
        """简历文档不写代码"""
        assert matches_template(
            "# P\n\n## 一句话简介\n问答系统。\n\n## 简历描述\n负责检索。\n\n"
            "```python\nprint(1)\n```\n"
        ) is False

    def test_no_h1_fails(self):
        assert matches_template("## 技术栈\nPython\n") is False


class TestTemplatePackaged:
    """
    模板是运行时资产，必须在镜像里。
    曾因 .dockerignore 的 `*.md` 被排掉——本地全绿，部署后上传接口全崩。
    """

    def test_not_excluded_by_dockerignore(self):
        ignore = Path(__file__).resolve().parents[2] / ".dockerignore"
        rules = [
            line.strip()
            for line in ignore.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
        # *.md 排除了一切 md，必须有针对模板的取反规则把它捞回来
        assert any(
            rule.startswith("!") and "templates" in rule for rule in rules
        ), "模板文件会被 .dockerignore 排除，镜像内 load_template() 将失败"


class TestTemplateIsSingleSourceOfTruth:
    """改模板即可调整校验口径，无需改代码"""

    def test_new_required_section_takes_effect(self, monkeypatch, tmp_path):
        template = (
            "# 项目名\n\n## 一句话简介\n…\n\n## 简历描述\n…\n\n## 技术栈\n…\n"
        )
        (tmp_path / "t.md").write_text(template, encoding="utf-8")
        monkeypatch.setattr(
            "src.knowledge.doc_template._TEMPLATE_PATH", tmp_path / "t.md")
        monkeypatch.setattr(
            "src.knowledge.doc_template.load_template", lambda: template)
        monkeypatch.setattr(
            "src.knowledge.doc_template.template_sections",
            lambda: (("一句话简介", False), ("简历描述", False), ("技术栈", False)),
        )

        # 未加新必填章节时合规
        doc = "# P\n\n## 一句话简介\n问答。\n\n## 简历描述\n负责检索。\n"
        # 模板把「技术栈」设为必填后，同一份文档即变为不合规
        assert matches_template(doc) is False

    def test_marking_section_optional_relaxes_check(self, monkeypatch):
        monkeypatch.setattr(
            "src.knowledge.doc_template.template_sections",
            lambda: (("一句话简介", False), ("简历描述", True)),
        )
        # 「简历描述」改为可选后，缺它的文档也能通过
        assert matches_template("# P\n\n## 一句话简介\n问答。\n") is True
