"""
后台任务管理器

单用户/演示场景:内存任务表 + 后台守护线程执行 + 事件历史(供 SSE 重放与实时订阅)。
契约见 docs/specs/api-contract.md 第 4.5/4.6 节。

设计要点:
- 每个任务一个 TaskRecord,含 threading.Condition 保护的事件历史列表 events。
- run_fn(progress) 在后台线程执行,progress(stage, message, percent) 上报进度。
- subscribe() 生成器:先重放历史事件,再阻塞等待新事件,直到 done 帧(延迟订阅也不丢事件)。
- 不依赖 pydantic,保持纯逻辑易测;由 router 层负责转 TaskStatus。
"""
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

# run_fn 签名:接受进度回调 (stage, message, percent)
ProgressCallback = Callable[[str, str, float], None]
RunFn = Callable[[ProgressCallback], Any]

# 终止事件名(subscribe 遇到即结束)
_TERMINAL_EVENT = "done"


@dataclass
class TaskRecord:
    """单个任务的状态与事件历史"""

    task_id: str
    task: str
    status: str = "pending"
    stage: Optional[str] = None
    percent: Optional[float] = None
    message: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    error: Optional[str] = None
    events: List[Dict[str, Any]] = field(default_factory=list)
    cond: threading.Condition = field(default_factory=threading.Condition)

    def emit(self, event: str, data: Dict[str, Any]) -> None:
        """追加一个 SSE 事件到历史并唤醒订阅者"""
        with self.cond:
            self.events.append({"event": event, "data": data})
            self.cond.notify_all()


class TaskManager:
    """内存任务表 + 线程执行 + 进度事件流"""

    def __init__(self) -> None:
        self._tasks: Dict[str, TaskRecord] = {}
        self._lock = threading.Lock()

    def submit(self, task: str, run_fn: RunFn) -> str:
        """创建任务并启动后台线程执行,立即返回 task_id"""
        task_id = uuid.uuid4().hex[:16]
        record = TaskRecord(task_id=task_id, task=task)
        with self._lock:
            self._tasks[task_id] = record
        thread = threading.Thread(
            target=self._execute, args=(record, run_fn), daemon=True
        )
        thread.start()
        return task_id

    def get(self, task_id: str) -> Optional[TaskRecord]:
        """取任务记录,不存在返回 None"""
        with self._lock:
            return self._tasks.get(task_id)

    def _execute(self, record: TaskRecord, run_fn: RunFn) -> None:
        """后台线程体:执行 run_fn,捕获进度与异常,维护状态机与事件流"""
        record.status = "running"
        record.emit("progress", {"stage": record.task, "message": "任务开始", "percent": 0})

        def progress(stage: str, message: str, percent: float) -> None:
            record.stage = stage
            record.message = message
            record.percent = percent
            record.emit(
                "progress", {"stage": stage, "message": message, "percent": percent}
            )

        t0 = time.time()
        try:
            run_fn(progress)
            record.status = "success"
            record.percent = 100
            record.finished_at = time.time()
            record.emit("done", {
                "task_id": record.task_id,
                "status": "success",
                "elapsed": round(time.time() - t0, 2),
            })
        except Exception as e:  # noqa: BLE001 - 任务异常需转成 error 事件下发
            record.status = "failed"
            record.error = str(e)
            record.finished_at = time.time()
            record.emit("error", {"message": str(e)})
            record.emit("done", {
                "task_id": record.task_id,
                "status": "failed",
                "elapsed": round(time.time() - t0, 2),
            })

    def subscribe(self, task_id: str, wait_timeout: float = 1.0):
        """生成器:重放历史事件 + 实时等待新事件,直到 done 帧。

        任务不存在时立即结束(不产出任何事件)。
        wait_timeout 用于避免异常情况下永久阻塞(正常任务一定会发 done)。
        """
        record = self.get(task_id)
        if record is None:
            return

        idx = 0
        while True:
            with record.cond:
                # 等待新事件(带超时,防呆)
                while idx >= len(record.events):
                    record.cond.wait(timeout=wait_timeout)
                pending = record.events[idx:]
                idx = len(record.events)
            for ev in pending:
                yield ev
                if ev["event"] == _TERMINAL_EVENT:
                    return
