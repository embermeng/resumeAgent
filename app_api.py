"""
FastAPI 后端入口(对称于 app_streamlit.py)

启动:
    uvicorn app_api:app --reload --port 8000
    # 或
    fastapi dev app_api.py
交互式文档:
    http://localhost:8000/docs
"""
import asyncio
import logging
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).parent))

# Windows 下 psycopg3 异步引擎不支持 ProactorEventLoop,需切到 SelectorEventLoop
# (与 alembic/env.py 的处理一致);必须在 uvicorn 创建事件循环之前设置。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logging.getLogger("httpx").setLevel(logging.WARNING)

from src.api.app import create_app  # noqa: E402

app = create_app()
