"""
项目精华提炼模块 [核心新模块]
用LLM从项目源码/文档中提炼结构化信息（项目名称、描述、技术栈、亮点等）
"""
import json
import logging
from pathlib import Path
from typing import List, Optional, Dict

from src.schemas.project import ProjectExtract
from src.api_client import APIProcessor

_log = logging.getLogger(__name__)

# 项目提炼系统提示词
PROJECT_EXTRACT_SYSTEM_PROMPT = """你是一个资深技术分析师，擅长从项目源码和文档中提炼关键信息。

请分析提供的项目内容，提取以下结构化信息：
1. project_name: 项目名称
2. description: 项目描述（2-3句话，概括项目功能和目标）
3. tech_stack: 技术栈列表（如 Python, LangChain, FAISS 等）
4. highlights: 项目亮点列表（技术亮点、优化手段、架构设计等，3-5条）
5. role_contribution: 个人角色与贡献（如果能推断出来）
6. key_metrics: 可量化的成果指标（如果有的话）

请以JSON格式输出，确保所有字段都有值（如果没有相关信息，highlights和tech_stack可以为较短的列表，其他字段用空字符串）。"""

# 支持读取的文件扩展名
SUPPORTED_CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs",
    ".cpp", ".c", ".h", ".vue", ".html", ".css", ".sql", ".yaml", ".yml",
    ".toml", ".json", ".xml", ".sh", ".bat",
}
SUPPORTED_DOC_EXTENSIONS = {".md", ".txt", ".rst"}
# 忽略的目录
IGNORE_DIRS = {
    "__pycache__", ".git", "node_modules", ".venv", "venv", "env",
    ".idea", ".vscode", "dist", "build", ".next", "__pycache__",
}


class ProjectExtractor:
    """
    项目精华提炼器
    读取项目目录中的源码和文档，用LLM提炼结构化信息
    """

    def __init__(
        self,
        provider: str = "dashscope",
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_file_content_chars: int = 30000,
    ):
        self.api = APIProcessor(provider=provider)
        self.model = model or self.api.default_model
        self.temperature = temperature
        self.max_file_content_chars = max_file_content_chars

    def collect_project_files(
        self,
        project_dir: Path,
        max_files: int = 50,
    ) -> List[Path]:
        """
        收集项目目录中的源码和文档文件
        优先读取README、requirements.txt等关键文件
        """
        all_files = []
        priority_files = []

        for file_path in project_dir.rglob("*"):
            if not file_path.is_file():
                continue
            # 跳过忽略目录
            if any(part in IGNORE_DIRS for part in file_path.parts):
                continue
            # 检查扩展名
            if file_path.suffix.lower() in SUPPORTED_CODE_EXTENSIONS | SUPPORTED_DOC_EXTENSIONS:
                # 优先文件
                if file_path.name.lower() in {"readme.md", "requirements.txt", "package.json", "pyproject.toml"}:
                    priority_files.append(file_path)
                else:
                    all_files.append(file_path)

        # 优先文件排前面，总数限制
        result = priority_files + all_files
        return result[:max_files]

    def build_project_context(
        self,
        project_dir: Path,
        max_files: int = 50,
    ) -> str:
        """
        构建项目上下文文本（用于发送给LLM）
        """
        files = self.collect_project_files(project_dir, max_files)
        context_parts = []

        for file_path in files:
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            # 截断过长文件
            if len(content) > self.max_file_content_chars:
                content = content[: self.max_file_content_chars] + "\n... [内容已截断]"

            rel_path = file_path.relative_to(project_dir)
            context_parts.append(f"=== {rel_path} ===\n{content}")

        return "\n\n".join(context_parts)

    def extract_from_text(self, project_text: str, project_name: str = "") -> ProjectExtract:
        """
        从项目文本内容中提炼结构化信息
        参数:
            project_text: 项目上下文文本
            project_name: 项目名称提示（可选）
        返回:
            ProjectExtract 实例
        """
        name_hint = f"\n项目名称参考: {project_name}" if project_name else ""
        user_prompt = f"请分析以下项目内容并提炼结构化信息。{name_hint}\n\n{project_text}"

        try:
            result = self.api.send_message(
                model=self.model,
                temperature=self.temperature,
                system_content=PROJECT_EXTRACT_SYSTEM_PROMPT,
                human_content=user_prompt,
                is_structured=True,
                response_format=ProjectExtract,
            )

            # 如果返回的是dict，尝试构造ProjectExtract
            if isinstance(result, dict):
                if "parse_error" in result:
                    _log.warning(f"LLM结构化解析失败: {result['parse_error']}，使用降级处理")
                    return self._fallback_extract(project_name)
                return ProjectExtract.model_validate(result)
            else:
                return self._fallback_extract(project_name)

        except Exception as e:
            _log.error(f"项目提炼失败: {e}")
            return self._fallback_extract(project_name)

    def extract_from_directory(
        self,
        project_dir: Path,
        project_name: Optional[str] = None,
    ) -> ProjectExtract:
        """
        从项目目录中提炼结构化信息
        """
        project_name = project_name or project_dir.name
        context = self.build_project_context(project_dir)

        if not context.strip():
            _log.warning(f"项目目录为空或无可读文件: {project_dir}")
            return self._fallback_extract(project_name)

        return self.extract_from_text(context, project_name)

    def extract_batch(
        self,
        projects_dir: Path,
        output_dir: Optional[Path] = None,
    ) -> List[ProjectExtract]:
        """
        批量提炼多个项目的结构化信息
        参数:
            projects_dir: 包含多个项目子目录的根目录
            output_dir: 输出JSON文件的目录（可选）
        返回:
            ProjectExtract列表
        """
        results = []

        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)

        # 遍历子目录
        project_dirs = [d for d in projects_dir.iterdir() if d.is_dir()]

        for project_dir in project_dirs:
            _log.info(f"正在提炼项目: {project_dir.name}")
            extract = self.extract_from_directory(project_dir)
            results.append(extract)

            # 保存单个结果
            if output_dir:
                output_file = output_dir / f"{extract.project_name}.json"
                with open(output_file, "w", encoding="utf-8") as f:
                    json.dump(extract.model_dump(), f, ensure_ascii=False, indent=2)
                _log.info(f"已保存: {output_file.name}")

        _log.info(f"批量提炼完成，共处理 {len(results)} 个项目")
        return results

    @staticmethod
    def _fallback_extract(project_name: str) -> ProjectExtract:
        """降级处理：LLM失败时返回基础信息"""
        return ProjectExtract(
            project_name=project_name or "Unknown Project",
            description="项目信息提取失败，请手动补充。",
            tech_stack=[],
            highlights=[],
            role_contribution="",
            key_metrics="",
        )
