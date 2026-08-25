"""
课程摘要提取模块（分层检索第一层）
为每篇课程PDF生成结构化摘要（课程定位+简历价值知识点），合并为目录catalog.json
运行期LLM根据目录挑选高价值知识点，再定向检索对应课程，解决单次查询覆盖率低的问题
增量构建：摘要比解析结果新则跳过，force全量重建
"""
import json
import logging
import os
from pathlib import Path
from typing import List, Optional

from tqdm import tqdm

from src.api_client import APIProcessor
from src.prompts.knowledge_prompts import COURSE_SUMMARY_SYSTEM, COURSE_SUMMARY_USER
from src.schemas.knowledge import CourseSummary

_log = logging.getLogger(__name__)

# 喂给摘要LLM的正文上限（字符数），防止超长课程撑爆上下文
MAX_CONTENT_CHARS = 30000


def _need_rebuild(source_path: Path, target_path: Path, force: bool) -> bool:
    """判断是否需要重建摘要：force / 摘要不存在 / 解析结果比摘要新（上游更新过）"""
    if force:
        return True
    if not target_path.exists():
        return True
    return source_path.stat().st_mtime > target_path.stat().st_mtime


class CourseSummarizer:
    """课程结构化摘要提取器"""

    def __init__(self, provider: str = "dashscope", model: str = None):
        self.provider = provider
        self.model = model

    def summarize_markdown(self, markdown_text: str, course_name: str = "") -> Optional[dict]:
        """
        调LLM将课程正文提炼为结构化摘要
        返回校验后的dict，失败返回None
        """
        content = markdown_text[:MAX_CONTENT_CHARS]
        api = APIProcessor(provider=self.provider)
        result = api.send_message(
            model=self.model,
            temperature=0.3,
            system_content=COURSE_SUMMARY_SYSTEM,
            human_content=COURSE_SUMMARY_USER.format(content=content),
            is_structured=True,
            response_format=CourseSummary,
        )

        # 解析失败（parse_error）或字段缺失视为失败
        if result.get("parse_error"):
            _log.error(f"摘要解析失败: {result['parse_error'][:200]}")
            return None
        try:
            CourseSummary.model_validate(result)
        except Exception as e:
            _log.error(f"摘要字段校验失败: {e}")
            return None

        if not result.get("course_name") and course_name:
            result["course_name"] = course_name
        return result

    def process_parsed_dir(
        self,
        parsed_dir: Path,
        output_dir: Path,
        force: bool = False,
    ) -> dict:
        """
        批量提取课程摘要（增量）
        参数:
            parsed_dir: 解析结果目录（parsed_pdfs，含{doc_id}.json）
            output_dir: 摘要输出目录（course_summaries）
            force: 强制全量重建
        返回统计信息
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        parsed_paths = list(parsed_dir.glob("*.json"))

        skipped = 0
        built = 0
        failed = 0
        for parsed_path in tqdm(parsed_paths, desc="Extracting course summaries"):
            doc_id = parsed_path.stem
            summary_path = output_dir / f"{doc_id}.json"

            if not _need_rebuild(parsed_path, summary_path, force):
                _log.info(f"跳过已有摘要: {doc_id}")
                skipped += 1
                continue

            with open(parsed_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            markdown = data.get("content", {}).get("markdown", "")
            source = data.get("metainfo", {}).get("source", doc_id)
            if not markdown.strip():
                _log.warning(f"跳过空文档: {source}")
                continue

            try:
                summary = self.summarize_markdown(markdown, course_name=source)
            except Exception as e:
                _log.error(f"摘要生成失败 {source}: {e}")
                failed += 1
                continue

            if summary is None:
                failed += 1
                continue

            payload = {
                "doc_id": doc_id,
                "source": source,
                "summary": summary,
            }
            # 原子写入：先写临时文件再替换，避免中断留下半截摘要
            tmp_path = summary_path.with_suffix(".json.tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, summary_path)
            built += 1

        _log.info(
            f"摘要提取完成，共 {len(parsed_paths)} 个文档，"
            f"跳过 {skipped} 个，新建/重建 {built} 个，失败 {failed} 个"
        )
        return {"total": len(parsed_paths), "skipped": skipped, "built": built, "failed": failed}

    @staticmethod
    def build_catalog(summaries_dir: Path, catalog_path: Path) -> List[dict]:
        """
        合并所有摘要为课程目录catalog.json
        目录条目: {doc_id, source, course_name, one_line_intro, key_points, tech_keywords}
        """
        entries = []
        for path in sorted(summaries_dir.glob("*.json")):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                summary = payload["summary"]
                entries.append({
                    "doc_id": payload["doc_id"],
                    "source": payload.get("source", ""),
                    "course_name": summary.get("course_name", ""),
                    "one_line_intro": summary.get("one_line_intro", ""),
                    "key_points": summary.get("key_points", []),
                    "tech_keywords": summary.get("tech_keywords", []),
                })
            except Exception as e:
                _log.warning(f"摘要文件损坏，跳过 {path.name}: {e}")

        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = catalog_path.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump({"courses": entries}, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, catalog_path)
        _log.info(f"课程目录已生成: {catalog_path}（共 {len(entries)} 门课程）")
        return entries
