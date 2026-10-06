"""
SSE 工具:事件帧格式化与响应头常量

契约见 docs/specs/api-contract.md 第 1.1 节。
所有流式接口(/api/chat、/api/knowledge/tasks/{id}/stream)复用本模块,
保证帧格式统一:event: <名>\ndata: <单行JSON>\n\n
"""
import json
from typing import Any, Dict

# SSE 媒体类型
SSE_MEDIA_TYPE = "text/event-stream"

# SSE 响应头:防缓存 + 防代理(Nginx)缓冲导致的"不流式"
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def sse_format(event: str, data: Dict[str, Any], seq: int | None = None) -> str:
    """把事件名与数据字典封装成标准 SSE 帧。

    - data 序列化为单行 JSON;ensure_ascii=False 保留中文原文
    - 内容中的换行由 json 转义为 \\n,保证 data 恒为单行,符合 SSE 规范
    - 帧以空行(\n\n)结尾,供客户端按 \n\n 切帧
    - seq 为帧序号
    """
    payload = json.dumps(data, ensure_ascii=False)
    id_line = f"id: {seq}\n" if seq is not None else ""   # id 行在 event 前(SSE 规范)
    return f"{id_line}event: {event}\ndata: {payload}\n\n"
