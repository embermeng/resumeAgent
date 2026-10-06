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


class FakeRedis:
    """内存版 Redis,模拟 save_stream/chat_reconnect 用到的 list + kv 操作。

    - 语义对齐 decode_responses=True:存取均为 str。
    - 供测试 patch get_sync_client,避免依赖真实 Redis(否则连接超时会拖慢用例)。
    - 仅实现被测代码用到的命令:rpush/lrange/lindex/expire/setex/get。
    """

    def __init__(self):
        self.kv: Dict[str, str] = {}        # setex/get
        self.lists: Dict[str, List[str]] = {}  # rpush/lrange/lindex
        self.ttls: Dict[str, int] = {}      # 记录 expire/setex 的 TTL,供断言

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    def lrange(self, key, start, end):
        lst = self.lists.get(key, [])
        return lst[start:] if end == -1 else lst[start:end + 1]

    def lindex(self, key, index):
        lst = self.lists.get(key, [])
        if not lst:
            return None
        try:
            return lst[index]
        except IndexError:
            return None

    def expire(self, key, seconds):
        self.ttls[key] = seconds
        return True

    def setex(self, key, seconds, value):
        self.kv[key] = value
        self.ttls[key] = seconds
        return True

    def get(self, key):
        return self.kv.get(key)


def only_buf_key(fr: "FakeRedis") -> str:
    """从 FakeRedis 里取唯一的 chat:stream:{sid} 缓冲键(排除 :conv 归属映射)。"""
    keys = [k for k in fr.lists if k.startswith("chat:stream:") and not k.endswith(":conv")]
    assert len(keys) == 1, f"期望恰好 1 个缓冲键,实际 {keys}"
    return keys[0]
