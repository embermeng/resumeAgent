"""
config.py 单元测试
"""
import os
import pytest
from pathlib import Path
from unittest.mock import patch


class TestPathConfig:
    """路径配置测试"""

    def test_default_root_path(self):
        from src.config import PathConfig
        pc = PathConfig()
        assert pc.root_path is not None
        assert isinstance(pc.root_path, Path)

    def test_knowledge_base_dir(self):
        from src.config import PathConfig
        pc = PathConfig(root_path=Path("/fake/root"))
        assert pc.knowledge_base_dir == Path("/fake/root/data/knowledge_base")

    def test_course_pdfs_dir(self):
        from src.config import PathConfig
        pc = PathConfig(root_path=Path("/fake/root"))
        assert pc.course_pdfs_dir == Path("/fake/root/data/knowledge_base/course_pdfs")

    def test_course_chunks_dir(self):
        from src.config import PathConfig
        pc = PathConfig(root_path=Path("/fake/root"))
        assert pc.course_chunks_dir == Path("/fake/root/data/processed/course_chunks")

    def test_vector_dbs_dir(self):
        from src.config import PathConfig
        pc = PathConfig(root_path=Path("/fake/root"))
        assert pc.vector_dbs_dir == Path("/fake/root/data/databases/vector_dbs")

    def test_bm25_dbs_dir(self):
        from src.config import PathConfig
        pc = PathConfig(root_path=Path("/fake/root"))
        assert pc.bm25_dbs_dir == Path("/fake/root/data/databases/bm25_dbs")

    def test_ensure_dirs(self, tmp_path):
        from src.config import PathConfig
        pc = PathConfig(root_path=tmp_path)
        pc.ensure_dirs()
        assert pc.course_pdfs_dir.exists()
        assert pc.project_highlights_dir.exists()
        assert pc.course_chunks_dir.exists()
        assert pc.vector_dbs_dir.exists()


class TestLLMConfig:
    """LLM配置测试"""

    def test_default_values(self):
        from src.config import LLMConfig
        cfg = LLMConfig()
        assert cfg.provider == "dashscope"
        assert cfg.model == "qwen-turbo-latest"
        assert cfg.temperature == 0.5
        assert cfg.max_retries == 3

    def test_from_env(self):
        from src.config import LLMConfig
        with patch.dict(os.environ, {"DEFAULT_LLM_PROVIDER": "openai", "DEFAULT_LLM_MODEL": "gpt-4o"}):
            cfg = LLMConfig.from_env()
            assert cfg.provider == "openai"
            assert cfg.model == "gpt-4o"

    def test_from_env_defaults(self):
        from src.config import LLMConfig
        with patch.dict(os.environ, {}, clear=True):
            cfg = LLMConfig.from_env()
            assert cfg.provider == "dashscope"
            assert cfg.model == "qwen-turbo-latest"


class TestEmbeddingConfig:
    """Embedding配置测试"""

    def test_default_values(self):
        from src.config import EmbeddingConfig
        cfg = EmbeddingConfig()
        assert cfg.provider == "dashscope"
        assert cfg.model == "text-embedding-v1"

    def test_from_env_dashscope(self):
        from src.config import EmbeddingConfig
        with patch.dict(os.environ, {"EMBEDDING_PROVIDER": "dashscope"}):
            cfg = EmbeddingConfig.from_env()
            assert cfg.provider == "dashscope"
            assert cfg.model == "text-embedding-v1"

    def test_from_env_openai(self):
        from src.config import EmbeddingConfig
        with patch.dict(os.environ, {"EMBEDDING_PROVIDER": "openai"}):
            cfg = EmbeddingConfig.from_env()
            assert cfg.provider == "openai"
            assert cfg.model == "text-embedding-3-large"


class TestRetrievalConfig:
    """检索配置测试"""

    def test_default_values(self):
        from src.config import RetrievalConfig
        cfg = RetrievalConfig()
        assert cfg.top_n == 5
        assert cfg.use_vector is True
        assert cfg.use_bm25 is False
        assert cfg.use_rerank is False


class TestAppConfig:
    """应用总配置测试"""

    def test_app_config_creates_dirs(self, tmp_path):
        from src.config import AppConfig, PathConfig, LLMConfig, EmbeddingConfig, RetrievalConfig
        cfg = AppConfig(
            paths=PathConfig(root_path=tmp_path),
            llm=LLMConfig(),
            embedding=EmbeddingConfig(),
            retrieval=RetrievalConfig(),
        )
        assert cfg.paths.course_pdfs_dir.exists()
        assert cfg.paths.vector_dbs_dir.exists()

    def test_get_config_singleton(self):
        from src.config import get_config, reset_config
        reset_config()
        cfg1 = get_config()
        cfg2 = get_config()
        assert cfg1 is cfg2
        reset_config()

    def test_reset_config(self):
        from src.config import get_config, reset_config
        reset_config()
        cfg1 = get_config()
        reset_config()
        cfg2 = get_config()
        assert cfg1 is not cfg2
