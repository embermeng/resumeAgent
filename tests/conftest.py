"""
测试配置 - Mock外部依赖
"""
import sys
from unittest.mock import MagicMock

# Mock docling 模块（避免安装大型依赖）
docling_mock = MagicMock()
docling_base_mock = MagicMock()
docling_base_mock.ConversionStatus = MagicMock()
docling_base_mock.ConversionStatus.SUCCESS = "SUCCESS"
docling_base_mock.InputFormat = MagicMock()

sys.modules.setdefault("docling", docling_mock)
sys.modules.setdefault("docling.datamodel", MagicMock())
sys.modules.setdefault("docling.datamodel.base_models", docling_base_mock)
sys.modules.setdefault("docling.datamodel.document", MagicMock())
sys.modules.setdefault("docling.datamodel.pipeline_options", MagicMock())
sys.modules.setdefault("docling.document_converter", MagicMock())
sys.modules.setdefault("docling.pipeline", MagicMock())
sys.modules.setdefault("docling.pipeline.standard_pdf_pipeline", MagicMock())
sys.modules.setdefault("docling.backend", MagicMock())
sys.modules.setdefault("docling.backend.docling_parse_v2_backend", MagicMock())
