"""
后台任务 SSE 适配器(Celery 版)

老 TaskManager 靠进程内事件历史做 SSE 重放;迁 Celery 后任务状态只落在
权威 DB(build_tasks),本模块负责把它转成与老契约键名完全一致的 SSE 帧,
前端 useTaskStream 无感知。

帧契约(见 docs/specs/api-contract.md §1.1/§4.5):
- progress: {"stage", "message", "percent"}  首帧快照 + 三元组有变化才发
- error:    {"message"}                      失败原因(BuildTask.error 列)
- done:     {"task_id", "status", "elapsed"} 终态帧,发出即结束流

设计要点:
- 同步生成器 + time.sleep:StreamingResponse 把它丢线程池执行,不阻塞事件循环
  (与老 subscribe 风格一致)。
- DB 抖动时 get_task 吞异常返回 None,只跳过本拍(continue),不中断已建立的流。
- 进循环前先检查一次终态:秒级完成的快任务不必多等一拍。
- 轮询的是 DB 而非 AsyncResult:DB 是唯一权威信源(字段全、持久、
  backend 有 result_expires 会过期)。
"""
import time

from src.api.sse import sse_format
from src.worker.task_store import get_task


def stream_task_events(task_id: str, poll_interval: float = 1.0):
    # 1. 读 DB 快照 → yield 一帧 progress（替代"历史重放"）
    task = get_task(task_id)
    if task is None:
        return

    if task.status == "success":
        yield sse_format("done", {"task_id": task_id, "status": task.status, "elapsed": (task.finished_at - task.created_at).total_seconds()})
        return
    if task.status == "failed":
        yield sse_format("error", {"message": task.error})
        yield sse_format("done", {"task_id": task_id, "status": task.status, "elapsed": (task.finished_at - task.created_at).total_seconds()})
        return
    last_task = task
    yield sse_format("progress", {"stage": task.stage, "percent": task.percent, "message": task.message})
    # 2. 循环:睡 poll_interval → 再读 DB → stage/percent/message 有变化才 yield progress
    while True:
        time.sleep(poll_interval)
        task = get_task(task_id)
        if task is None:
            continue

        if task.status == "success":
            yield sse_format("done", {"task_id": task_id, "status": task.status, "elapsed": (task.finished_at - task.created_at).total_seconds()})
            return
        if task.status == "failed":
            yield sse_format("error", {"message": task.error})
            yield sse_format("done", {"task_id": task_id, "status": task.status, "elapsed": (task.finished_at - task.created_at).total_seconds()})
            return

        if task.stage != last_task.stage or task.percent != last_task.percent or task.message != last_task.message:
            yield sse_format("progress", {"stage": task.stage, "percent": task.percent, "message": task.message})
        last_task = task
