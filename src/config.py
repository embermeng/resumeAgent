"""
全局配置模块
管理项目路径、模型参数、检索参数等配置
"""
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Literal
from dotenv import load_dotenv
from pyprojroot import here


# 加载 .env 文件
load_dotenv()


@dataclass
class PathConfig:
    """项目路径配置"""
    root_path: Path = field(default_factory=lambda: here())

    # 知识库原始数据
    @property
    def knowledge_base_dir(self) -> Path:
        return self.root_path / "data" / "knowledge_base"

    @property
    def course_pdfs_dir(self) -> Path:
        return self.knowledge_base_dir / "course_pdfs"

    @property
    def project_highlights_dir(self) -> Path:
        """项目亮点文档目录（用户用提示词生成的项目亮点README，手动放入/网页上传）"""
        return self.knowledge_base_dir / "project_highlights"

    @property
    def project_intros_dir(self) -> Path:
        """项目介绍文档目录（简历措辞成品，一个项目一份，生成简历时直接引用，不入库不向量化）"""
        return self.knowledge_base_dir / "project_intros"

    @property
    def interview_qa_dir(self) -> Path:
        return self.knowledge_base_dir / "interview_qa"

    # 处理后的数据
    @property
    def processed_dir(self) -> Path:
        return self.root_path / "data" / "processed"

    @property
    def course_chunks_dir(self) -> Path:
        return self.processed_dir / "course_chunks"

    @property
    def interview_chunks_dir(self) -> Path:
        return self.processed_dir / "interview_chunks"

    @property
    def course_summaries_dir(self) -> Path:
        return self.processed_dir / "course_summaries"

    @property
    def catalog_path(self) -> Path:
        return self.processed_dir / "catalog.json"

    # 向量数据库
    @property
    def databases_dir(self) -> Path:
        return self.root_path / "data" / "databases"

    @property
    def vector_dbs_dir(self) -> Path:
        return self.databases_dir / "vector_dbs"

    @property
    def bm25_dbs_dir(self) -> Path:
        return self.databases_dir / "bm25_dbs"

    def ensure_dirs(self):
        """确保所有数据目录存在"""
        for dir_path in [
            self.course_pdfs_dir,
            self.project_highlights_dir,
            self.project_intros_dir,
            self.interview_qa_dir,
            self.course_chunks_dir,
            self.interview_chunks_dir,
            self.course_summaries_dir,
            self.vector_dbs_dir,
            self.bm25_dbs_dir,
        ]:
            dir_path.mkdir(parents=True, exist_ok=True)


@dataclass
class LLMConfig:
    """LLM模型配置"""
    provider: Literal["dashscope", "openai", "gemini"] = "dashscope"
    model: str = "qwen-turbo-latest"
    temperature: float = 0.5
    max_retries: int = 3
    timeout: int = 60

    @classmethod
    def from_env(cls) -> "LLMConfig":
        """从环境变量加载配置"""
        return cls(
            provider=os.getenv("DEFAULT_LLM_PROVIDER", "dashscope"),
            model=os.getenv("DEFAULT_LLM_MODEL", "qwen-turbo-latest"),
        )


@dataclass
class EmbeddingConfig:
    """Embedding模型配置"""
    provider: Literal["dashscope", "openai"] = "dashscope"
    model: str = "text-embedding-v1"  # dashscope默认; openai用text-embedding-3-large

    @classmethod
    def from_env(cls) -> "EmbeddingConfig":
        """从环境变量加载配置"""
        provider = os.getenv("EMBEDDING_PROVIDER", "dashscope")
        model = "text-embedding-v1" if provider == "dashscope" else "text-embedding-3-large"
        return cls(provider=provider, model=model)


@dataclass
class RetrievalConfig:
    """检索参数配置"""
    top_n: int = 5
    use_bm25: bool = False
    use_vector: bool = True
    use_rerank: bool = False
    rerank_sample_size: int = 20


@dataclass
class DatabaseConfig:
    """数据库配置"""
    database_url: str = os.getenv("DATABASE_URL", "")


@dataclass
class AuthConfig:
    """密钥配置"""
    secret_key: str = os.getenv("SECRET_KEY", "")
    algorithm: str = "HS256"
    # 测试完后改为30分钟
    access_token_expire_minutes: int = 1
    refresh_token_expire_minutes: int = 7 * 24 * 60


@dataclass
class AppConfig:
    """应用总配置，聚合所有子配置"""
    paths: PathConfig = field(default_factory=PathConfig)
    llm: LLMConfig = field(default_factory=LLMConfig.from_env)
    embedding: EmbeddingConfig = field(
        default_factory=EmbeddingConfig.from_env)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)

    def __post_init__(self):
        self.paths.ensure_dirs()
        if not self.auth.secret_key:
            raise RuntimeError("SECRET_KEY not set")


# 全局单例
_config: AppConfig = None


def get_config() -> AppConfig:
    """获取全局配置单例"""
    global _config
    if _config is None:
        _config = AppConfig()
    return _config


def reset_config():
    """重置配置（用于测试）"""
    global _config
    _config = None
