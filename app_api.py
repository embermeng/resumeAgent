"""
FastAPI 后端入口(对称于 app_streamlit.py)

启动:
    uvicorn app_api:app --reload --port 8000
    # 或
    fastapi dev app_api.py
交互式文档:
    http://localhost:8000/docs
"""
import logging
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logging.getLogger("httpx").setLevel(logging.WARNING)

from src.api.app import create_app  # noqa: E402

app = create_app()
