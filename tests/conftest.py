"""
测试配置 - Mock外部依赖
"""
import sys
from unittest.mock import MagicMock

# Mock mineru 模块（避免测试时加载GPU模型）
# 注：pdf_parser.py 对 mineru 为延迟导入，且测试中 _run_mineru 均被patch，
# 此处mock仅作为兜底防护
sys.modules.setdefault("mineru", MagicMock())
sys.modules.setdefault("mineru.cli", MagicMock())
sys.modules.setdefault("mineru.cli.common", MagicMock())
