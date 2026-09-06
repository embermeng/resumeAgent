"""
简历文件接口:
  POST /api/resume/parse                上传文件解析为 Markdown
  GET  /api/resume/supported-extensions 支持的扩展名
契约见 docs/specs/api-contract.md 第 4.3/4.4 节。
"""
import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from src.api.deps import get_resume_parser
from src.api.schemas_api import ParseResponse, SupportedExtensions
from src.knowledge.resume_file_parser import SUPPORTED_EXTENSIONS, ResumeFileParser

_log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/resume/supported-extensions", response_model=SupportedExtensions)
def supported_extensions():
    return SupportedExtensions(extensions=list(SUPPORTED_EXTENSIONS))


@router.post("/resume/parse", response_model=ParseResponse)
async def parse_resume(
    file: UploadFile = File(...),
    parser: ResumeFileParser = Depends(get_resume_parser),
):
    file_bytes = await file.read()
    try:
        # 解析可能较重(PDF 走 MinerU),放线程池避免阻塞事件循环
        text = await run_in_threadpool(parser.parse, file.filename, file_bytes)
    except ValueError as e:
        # 不支持的格式 / 解析结果为空 -> 400
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        _log.error(f"简历解析失败: {file.filename}, error={e}")
        raise HTTPException(status_code=400, detail=f"简历解析失败: {e}")
    return ParseResponse(filename=file.filename, content=text)
