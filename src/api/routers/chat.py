"""
POST /api/chat:流式对话(SSE)

把 ResumeAgent.run_stream() 的事件字典映射为 SSE 帧:
  {"type": "<name>", ...rest}  ->  event: <name>\ndata: <rest>
契约见 docs/specs/api-contract.md 第 4.2 节。
"""
import logging
import uuid
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.config import get_config
from src.api.deps import get_agent_service
from src.api.schemas_api import ChatRequest
from src.api.services.agent_service import AgentService
from src.api.sse import SSE_HEADERS, SSE_MEDIA_TYPE, sse_format
from src.api.routers.utils.save_stream import buffer_event, read_buffer, conv_key, last_is_terminal, save_conv_mapping
from src.cache.redis_client import get_sync_client

from src.auth.security import CurrentUser
from src.database.utils.dialog import create_conversation, add_message, check_conversation
_log = logging.getLogger(__name__)

router = APIRouter()
settings = get_config()

def _subscribe(sid, after):
    """重放 Redis 里 seq>after 的事件,然后轮询等新事件,直到终态帧。"""
    last = after
    time_limit = 300  # 秒
    k = 0
    while True:
        for e in read_buffer(sid, last):
            yield sse_format(e["event"], e["data"], seq=e["seq"])
            last = e["seq"]
            if e["event"] in ("done", "error"):
                return                                    # 读到终态即收流
        time.sleep(0.15)                                  # 轮询间隔(对齐 task_stream.py 范式)
        k += 1
        if k * 0.15 > time_limit:
            # 超时，返回超时帧
            yield sse_format("done", {"step": "timeout"})
            return


@router.post("/chat")
def chat(
    req: ChatRequest,
    current_user: CurrentUser,
    svc: AgentService = Depends(get_agent_service),
):
    if req.conversation_id is not None:
        if not check_conversation(req.conversation_id, current_user.id):
            raise HTTPException(
                status_code=404, detail="conversation not found")
        else:
            conv_id = req.conversation_id
    else:
        conv_id = create_conversation(req.prompt, current_user.id)

    if conv_id is not None:
        add_message(conv_id, "user", req.prompt)

    def gen():
        stream_id = uuid.uuid4().hex
        seq = 0
        buf, intent = [], None
        ttl = settings.redis.chat_stream_buffer_ttl
        try:
            if conv_id is not None:
                save_conv_mapping(stream_id, conv_id, ttl)
                data = {"conversation_id": conv_id, "stream_id": stream_id}
                buffer_event(stream_id, seq, "conversation", data, ttl)
                cur = seq
                seq += 1
                yield sse_format("conversation", data, seq=cur)
            # run_stream 是同步生成器,StreamingResponse 会在线程池迭代,不阻塞事件循环
            for event in svc.run_stream(req.prompt, existing_resume=req.existing_resume):
                event_type = event.get("type")
                data = {k: v for k, v in event.items() if k != "type"}
                if event_type == "intent":
                    intent = event.get("value")
                if event_type == "token":
                    buf.append(event.get("text", ""))
                buffer_event(stream_id, seq, event_type, data, ttl)
                cur = seq
                seq += 1
                yield sse_format(event_type, data, seq=cur)
        except Exception as e:  # noqa: BLE001 - 流已开始,异常只能转 error 帧下发
            _log.error(f"对话流异常: {e}")
            buffer_event(stream_id, seq, "error", {"message": str(e)}, ttl)
            cur = seq
            seq += 1
            yield sse_format("error", {"message": str(e)}, seq=cur)
        finally:
            if conv_id is not None:
                text = "".join(buf)
                if text: add_message(conv_id, "assistant", text, intent)
                # 若因断线(GeneratorExit)提前退出、缓冲末尾不是终态 → 补一个截断终态,
                # 让 GET 重放有明确结束(不能 yield,生成器正在关闭,只能写 Redis)
                if not last_is_terminal(stream_id):
                    buffer_event(stream_id, seq, "done", {"intent": intent, "step": "interrupted"}, ttl)

    return StreamingResponse(gen(), media_type=SSE_MEDIA_TYPE, headers=SSE_HEADERS)


@router.get("/chat/{stream_id}/stream")
def chat_reconnect(stream_id: str, request: Request, current_user: CurrentUser):
    conv_id = get_sync_client().get(conv_key(stream_id))
    if conv_id is None or not check_conversation(int(conv_id), current_user.id):
        raise HTTPException(status_code=404, detail="stream not found")
    after = None
    raw = request.headers.get("Last-Event-ID")
    if raw is None:
        raw = request.query_params.get("after", -1)   # 头优先,没有才用 query
    try:
        lid = int(raw)
    except (ValueError, TypeError):                    # 两个都要捕
        lid = -1
    return StreamingResponse(_subscribe(stream_id, lid), media_type=SSE_MEDIA_TYPE, headers=SSE_HEADERS)