"""src/api/task_manager.py 测试(TDD)

覆盖:任务提交、后台线程执行、进度回调更新、成功/失败状态机、
事件流(subscribe 重放 + 实时)、多任务隔离、不存在任务的处理。
"""
import time

from src.api.task_manager import TaskManager


def _ok_run(progress):
    progress("s1", "step1", 50)
    progress("s2", "step2", 90)


def _fail_run(progress):
    progress("s1", "step1", 30)
    raise RuntimeError("boom")


def _wait(tm, task_id, timeout=5.0):
    """轮询等待任务结束(线程测试同步辅助)"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        rec = tm.get(task_id)
        if rec and rec.status in ("success", "failed"):
            return rec
        time.sleep(0.02)
    raise TimeoutError(f"任务未在 {timeout}s 内结束: {task_id}")


class TestSubmit:
    def test_submit_returns_id_and_pending_or_running(self):
        tm = TaskManager()
        tid = tm.submit("build-all", _ok_run)
        assert isinstance(tid, str) and tid
        rec = tm.get(tid)
        assert rec is not None
        assert rec.task == "build-all"

    def test_get_missing_returns_none(self):
        tm = TaskManager()
        assert tm.get("nonexist") is None

    def test_task_ids_unique(self):
        tm = TaskManager()
        t1 = tm.submit("build-all", _ok_run)
        t2 = tm.submit("build-all", _ok_run)
        assert t1 != t2


class TestExecution:
    def test_success_final_state(self):
        tm = TaskManager()
        tid = tm.submit("build-all", _ok_run)
        rec = _wait(tm, tid)
        assert rec.status == "success"
        assert rec.percent == 100
        assert rec.finished_at is not None
        assert rec.error is None
        # 进度回调更新了阶段字段
        assert rec.stage == "s2"
        assert rec.message == "step2"

    def test_failed_final_state(self):
        tm = TaskManager()
        tid = tm.submit("parse-pdfs", _fail_run)
        rec = _wait(tm, tid)
        assert rec.status == "failed"
        assert "boom" in rec.error
        assert rec.finished_at is not None


class TestSubscribe:
    def test_events_end_with_done_on_success(self):
        tm = TaskManager()
        tid = tm.submit("build-all", _ok_run)
        events = list(tm.subscribe(tid))
        kinds = [e["event"] for e in events]
        assert kinds[0] == "progress"          # 任务开始
        assert kinds[-1] == "done"
        assert "error" not in kinds
        done = events[-1]["data"]
        assert done["status"] == "success"
        assert done["task_id"] == tid
        assert "elapsed" in done

    def test_events_include_error_then_done_on_failure(self):
        tm = TaskManager()
        tid = tm.submit("parse-pdfs", _fail_run)
        events = list(tm.subscribe(tid))
        kinds = [e["event"] for e in events]
        assert "error" in kinds
        assert kinds[-1] == "done"
        assert events[-1]["data"]["status"] == "failed"
        # error 帧携带消息
        err = [e for e in events if e["event"] == "error"][0]
        assert "boom" in err["data"]["message"]

    def test_progress_events_carry_percent(self):
        tm = TaskManager()
        tid = tm.submit("build-all", _ok_run)
        events = list(tm.subscribe(tid))
        percents = [e["data"]["percent"] for e in events if e["event"] == "progress"]
        assert 0 in percents        # 任务开始
        assert 50 in percents
        assert 90 in percents

    def test_subscribe_missing_task_yields_nothing(self):
        tm = TaskManager()
        assert list(tm.subscribe("nonexist")) == []

    def test_subscribe_after_completion_replays_history(self):
        # 任务结束后再订阅,应重放完整历史直到 done
        tm = TaskManager()
        tid = tm.submit("build-all", _ok_run)
        _wait(tm, tid)
        events = list(tm.subscribe(tid))
        assert events[-1]["event"] == "done"
        assert len(events) >= 2


class TestIsolation:
    def test_two_tasks_independent(self):
        tm = TaskManager()
        t1 = tm.submit("build-all", _ok_run)
        t2 = tm.submit("parse-pdfs", _fail_run)
        _wait(tm, t1)
        _wait(tm, t2)
        assert tm.get(t1).status == "success"
        assert tm.get(t2).status == "failed"
        # 事件互不串台
        e1 = list(tm.subscribe(t1))
        assert all(e["data"].get("task_id") != t2 for e in e1 if e["event"] == "done")
