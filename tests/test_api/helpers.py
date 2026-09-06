"""测试辅助:SSE 响应体解析(非 test_ 前缀,不被 pytest 收集)"""
import json
from typing import Any, Dict, List, Tuple


def parse_sse(body: str) -> List[Tuple[str, Dict[str, Any]]]:
    """把 SSE 响应体解析为 [(event, data_dict), ...]。

    - 按空行(\\n\\n)切帧
    - 每帧取 event: 与 data:(data 为单行 JSON)
    """
    frames: List[Tuple[str, Dict[str, Any]]] = []
    for block in body.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event = None
        data = None
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = json.loads(line[len("data:"):].strip())
        frames.append((event, data if data is not None else {}))
    return frames


def event_names(frames: List[Tuple[str, Dict[str, Any]]]) -> List[str]:
    return [f[0] for f in frames]
