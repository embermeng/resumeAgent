"""src/api/sse.py 测试(TDD)

验证 SSE 帧格式契约:结构、单行 JSON、中文不转义、含换行内容仍单行安全、响应头。
"""
import json

from src.api.sse import sse_format, SSE_HEADERS, SSE_MEDIA_TYPE


def _data_of(frame: str) -> str:
    """从帧中取出 data: 后的 JSON 字符串(断言 data 单行)"""
    data_lines = [ln for ln in frame.split("\n") if ln.startswith("data: ")]
    assert len(data_lines) == 1, f"data 必须是单行,实际 {len(data_lines)} 行"
    return data_lines[0][len("data: "):]


class TestSseFormat:
    def test_frame_structure(self):
        frame = sse_format("token", {"text": "hi"})
        lines = frame.split("\n")
        assert lines[0] == "event: token"
        assert lines[1].startswith("data: ")
        # 帧以空行结尾(\n\n)
        assert frame.endswith("\n\n")

    def test_data_is_valid_json(self):
        frame = sse_format("done", {"intent": "chitchat", "step": "chitchat_done"})
        payload = json.loads(_data_of(frame))
        assert payload == {"intent": "chitchat", "step": "chitchat_done"}

    def test_chinese_not_escaped(self):
        # ensure_ascii=False:中文原样出现在帧里,便于调试与前端直读
        frame = sse_format("status", {"text": "正在识别意图..."})
        assert "正在识别意图" in frame

    def test_newline_in_content_stays_single_line(self):
        # 内容含裸换行时,json 转义为 \n,data 仍单行,还原后内容不丢
        frame = sse_format("token", {"text": "line1\nline2"})
        payload = json.loads(_data_of(frame))
        assert payload["text"] == "line1\nline2"

    def test_empty_data(self):
        frame = sse_format("ping", {})
        assert json.loads(_data_of(frame)) == {}

    def test_headers_and_media_type(self):
        assert SSE_MEDIA_TYPE == "text/event-stream"
        assert SSE_HEADERS["Cache-Control"] == "no-cache"
        assert SSE_HEADERS["Connection"] == "keep-alive"
        assert SSE_HEADERS["X-Accel-Buffering"] == "no"
