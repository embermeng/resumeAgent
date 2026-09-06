"""
POST /api/chat:流式对话(SSE)

把 ResumeAgent.run_stream() 的事件字典映射为 SSE 帧:
  {"type": "<name>", ...rest}  ->  event: <name>\ndata: <rest>
契约见 docs/specs/api-contract.md 第 4.2 节。
"""
import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from src.api.deps import get_agent_service
from src.api.schemas_api import ChatRequest
from src.api.services.agent_service import AgentService
from src.api.sse import SSE_HEADERS, SSE_MEDIA_TYPE, sse_format

_log = logging.getLogger(__name__)

router = APIRouter()


def event_to_sse(event: Dict[str, Any]) -> str:
    """run_stream 事件字典 -> SSE 帧(type 作为事件名,其余字段作为 data)"""
    etype = event.get("type", "message")
    data = {k: v for k, v in event.items() if k != "type"}
    return sse_format(etype, data)


@router.post("/chat")
def chat(req: ChatRequest, svc: AgentService = Depends(get_agent_service)):
    def gen():
        try:
            # run_stream 是同步生成器,StreamingResponse 会在线程池迭代,不阻塞事件循环
            for event in svc.run_stream(req.prompt, existing_resume=req.existing_resume):
                yield event_to_sse(event)
        except Exception as e:  # noqa: BLE001 - 流已开始,异常只能转 error 帧下发
            _log.error(f"对话流异常: {e}")
            yield sse_format("error", {"message": str(e)})

    return StreamingResponse(gen(), media_type=SSE_MEDIA_TYPE, headers=SSE_HEADERS)
