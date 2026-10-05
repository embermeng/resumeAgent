"""
task_stream SSE 适配器单测:直调生成器,patch get_task 喂假快照序列。

帧键名与老契约逐字对齐(前端 useTaskStream 无白名单派发,键名就是唯一耦合点):
- progress: {"stage", "message", "percent"}
- error:    {"message"}(取 BuildTask.error 列,不是进度 message)
- done:     {"task_id", "status", "elapsed"}
"""
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import src.api.task_stream as stream_module
from tests.test_api.helpers import event_names, parse_sse


def _row(status="running", stage="parsing", percent=50.0, message="MinerU解析中",
         error=None):
    """假 BuildTask 行(适配器只读列属性,detached 行为由 test_task_store 专测)"""
    now = datetime.now(UTC)
    return SimpleNamespace(
        status=status, stage=stage, percent=percent, message=message,
        error=error, created_at=now,
        finished_at=now if status in ("success", "failed") else None,
    )


@pytest.fixture
def feed(monkeypatch):
    """按调用次序喂 get_task 返回值;序列耗尽后重复最后一个(任务不再变化)"""
    state = {"rows": [], "i": 0}

    def _get(task_id):
        rows = state["rows"]
        if not rows:
            return None
        val = rows[min(state["i"], len(rows) - 1)]
        state["i"] += 1
        return val

    monkeypatch.setattr(stream_module, "get_task", _get)

    def _set(rows):
        state["rows"] = rows
        state["i"] = 0

    return _set


def _collect(task_id="t1"):
    return parse_sse(
        "".join(stream_module.stream_task_events(task_id, poll_interval=0)))


class TestStreamTaskEvents:
    def test_missing_task_yields_nothing(self, feed):
        """get_task 恒 None(任务不存在/受理前):静默结束,一帧不发"""
        feed([])
        assert _collect() == []

    def test_terminal_success_done_only(self, feed):
        """订阅时已 success:快路径直接 done,不进轮询"""
        feed([_row(status="success", percent=100.0)])
        frames = _collect()
        assert event_names(frames) == ["done"]
        d = frames[0][1]
        assert set(d) == {"task_id", "status", "elapsed"}
        assert d["task_id"] == "t1" and d["status"] == "success"
        assert d["elapsed"] >= 0

    def test_terminal_failed_error_then_done(self, feed):
        feed([_row(status="failed", error="MinerU超时")])
        frames = _collect()
        assert event_names(frames) == ["error", "done"]
        # error 帧取 error 列(失败原因),不是 message 列(进度文案)
        assert frames[0][1] == {"message": "MinerU超时"}
        assert set(frames[1][1]) == {"task_id", "status", "elapsed"}
        assert frames[1][1]["status"] == "failed"

    def test_snapshot_change_and_done(self, feed):
        """快照首帧 → 三元组未变不发帧(防进度条闪烁) → 变化发帧 → done 收尾"""
        feed([
            _row(),                                                  # 快照: running
            _row(),                                                  # 无变化
            _row(stage="saving", percent=90.0, message="写入结果"),   # 变化
            _row(status="success", percent=100.0),                   # 终态
        ])
        frames = _collect()
        assert event_names(frames) == ["progress", "progress", "done"]
        assert frames[0][1] == {"stage": "parsing", "percent": 50.0,
                                "message": "MinerU解析中"}
        assert frames[1][1] == {"stage": "saving", "percent": 90.0,
                                "message": "写入结果"}
        assert frames[2][1]["status"] == "success"

    def test_db_blip_none_does_not_break_stream(self, feed):
        """DB 抖动 get_task 返回 None:跳过本拍继续,不中断已建立的流"""
        feed([_row(), None, _row(status="success", percent=100.0)])
        frames = _collect()
        assert event_names(frames) == ["progress", "done"]
