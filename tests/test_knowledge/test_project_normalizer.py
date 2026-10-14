"""
项目文档归一化测试（预处理第 2 层）

核心守卫：**任何失败都必须回退原文**，绝不因归一化失败而拒收用户文档；
且已规范的文档不应被无谓改写（省一次 LLM，也避免模型擅改原文）。
"""
from unittest.mock import MagicMock, patch

import pytest

from src.knowledge.project_normalizer import (
    NormalizeResult,
    needs_normalize,
    normalize_project_doc,
)

MESSY = "我做了个RAG问答项目，用FAISS做检索，还用了BM25混合召回。主要负责检索链路。"
TIDY = """# 智能问答助手

## 一句话简介
基于大模型的问答系统。

## 简历描述
负责检索链路设计。

## 技术栈
Python, FAISS
"""


class TestNeedsNormalize:
    """触发判据只有一个：是否符合模板。具体断言由 test_doc_template 覆盖。"""

    def test_messy_text_needs_it(self):
        assert needs_normalize(MESSY) is True

    def test_template_conforming_text_does_not(self):
        assert needs_normalize(TIDY) is False

    def test_missing_required_section_needs_it(self):
        assert needs_normalize("# P\n\n## 技术栈\nPython") is True


class TestNormalize:
    def _config(self):
        cfg = MagicMock()
        cfg.llm.provider = "dashscope"
        cfg.llm.model = "test-model"
        return cfg

    def test_skips_when_template_conforming(self):
        r = normalize_project_doc(TIDY, "x.md", config=self._config())
        assert r.applied is False
        assert r.normalized == TIDY.strip()
        assert "模板" in r.reason

    def test_applies_when_messy(self):
        out = "# 智能问答助手\n\n## 技术栈\nFAISS, BM25\n"
        with patch("src.api_client.APIProcessor") as MockAPI:
            MockAPI.return_value.send_message.return_value = {"content": out}
            r = normalize_project_doc(MESSY, "智能问答助手.md", config=self._config())

        assert r.applied is True
        assert r.normalized == out.strip()
        assert r.original == MESSY

    def test_strips_fence_from_output(self):
        """模型偶尔会用代码围栏包住输出"""
        raw_out = "```markdown\n# P\n\n## 技术栈\nPython\n```"
        with patch("src.api_client.APIProcessor") as MockAPI:
            MockAPI.return_value.send_message.return_value = {"content": raw_out}
            r = normalize_project_doc(MESSY, "P.md", config=self._config())

        assert r.applied is True
        assert not r.normalized.startswith("```")
        assert r.normalized.startswith("# P")

    def test_api_exception_falls_back(self):
        with patch("src.api_client.APIProcessor") as MockAPI:
            MockAPI.return_value.send_message.side_effect = RuntimeError("api down")
            r = normalize_project_doc(MESSY, "P.md", config=self._config())

        assert r.applied is False
        assert r.normalized == MESSY, "失败必须回退原文，不能返回空或抛错"
        assert "api down" in r.reason

    def test_parse_error_falls_back(self):
        with patch("src.api_client.APIProcessor") as MockAPI:
            MockAPI.return_value.send_message.return_value = {"parse_error": "bad json"}
            r = normalize_project_doc(MESSY, "P.md", config=self._config())
        assert r.applied is False and r.normalized == MESSY

    def test_output_without_h1_falls_back(self):
        """模型输出缺一级标题会导致项目名无法确定，必须回退"""
        with patch("src.api_client.APIProcessor") as MockAPI:
            MockAPI.return_value.send_message.return_value = {
                "content": "## 技术栈\nPython"}
            r = normalize_project_doc(MESSY, "P.md", config=self._config())

        assert r.applied is False
        assert r.normalized == MESSY
        assert "一级标题" in r.reason

    def test_empty_output_falls_back(self):
        with patch("src.api_client.APIProcessor") as MockAPI:
            MockAPI.return_value.send_message.return_value = {"content": "   "}
            r = normalize_project_doc(MESSY, "P.md", config=self._config())
        assert r.applied is False and r.normalized == MESSY

    def test_fallback_name_passed_to_prompt(self):
        """兜底项目名要下发给模型，否则它无从得知该用什么标题"""
        with patch("src.api_client.APIProcessor") as MockAPI:
            MockAPI.return_value.send_message.return_value = {
                "content": "# 智能问答助手\n\n## 技术栈\nPython"}
            normalize_project_doc(MESSY, "智能问答助手.md", config=self._config())

        kwargs = MockAPI.return_value.send_message.call_args.kwargs
        assert "智能问答助手" in kwargs["human_content"]
        assert kwargs["temperature"] <= 0.3, "本环节要稳定复述，不能高温度"

    def test_result_is_dataclass(self):
        r = NormalizeResult(original="a", normalized="b", applied=True, reason="ok")
        assert (r.original, r.normalized, r.applied) == ("a", "b", True)


class TestAntiFabrication:
    """
    提示词约束的回归守卫：简历不能编造是本环节的设计底线，
    若日后有人"优化"提示词删掉这些约束，测试应失败。
    """

    def test_prompt_forbids_fabrication(self):
        from src.prompts.project_normalize_prompts import build_system_prompt

        prompt = build_system_prompt()
        for keyword in ("禁止编造", "禁止夸大", "禁止推断补全", "禁止改数"):
            assert keyword in prompt, f"提示词丢失约束: {keyword}"

    def test_prompt_contains_template_verbatim(self):
        """
        标准格式必须与模版文件逐字一致，而不是提示词里另写一份。
        存在第二处格式定义时，改了模版却不生效是最容易埋雷的地方。
        """
        from src.knowledge.doc_template import load_template
        from src.prompts.project_normalize_prompts import build_system_prompt

        template = load_template()
        assert template in build_system_prompt()
        for section in ("一句话简介", "简历描述"):
            assert section in template

    def test_template_file_loads_and_has_required_sections(self):
        from src.knowledge.doc_template import required_sections, template_path

        assert template_path().exists(), "模板文件缺失，下载与校验都会失效"
        assert "简历描述" in required_sections()
