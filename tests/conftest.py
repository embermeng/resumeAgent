"""
测试配置 - Mock外部依赖
"""
import sys
from unittest.mock import MagicMock

import pytest

# Mock mineru 模块（避免测试时加载GPU模型）
# 注：pdf_parser.py 对 mineru 为延迟导入，且测试中 _run_mineru 均被patch，
# 此处mock仅作为兜底防护
sys.modules.setdefault("mineru", MagicMock())
sys.modules.setdefault("mineru.cli", MagicMock())
sys.modules.setdefault("mineru.cli.common", MagicMock())


@pytest.fixture(autouse=True)
def _isolate_retrieval_cache(monkeypatch):
    """测试默认关闭检索缓存,避免任何真走 retrieve 的用例依赖/污染真实 Redis。
    patch 目标是 retriever 模块实际持有的 settings 对象(字符串目标):
    reset_config() 后 get_config() 新单例与模块级 settings 可能不是同一实例。
    缓存行为专测(TestHybridCacheAside)自行显式重新开启"""
    monkeypatch.setattr(
        "src.retrieval.retriever.settings.redis.cache_enabled", False)
