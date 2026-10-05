"""
knowledge_build_task 任务体单测(eager 直调,mock KnowledgeService/update_state/update_task)。

不起真 worker、不连真 broker/backend、不碰真 DB。

覆盖契约:
- 首帧 status="running" + stage="queued"/percent=0 低起步
  (回归守卫:曾写 building/50,被 KnowledgeService 首个真实回调 10 压回,进度条倒退)
- progress(stage,message,percent) 回调双通道:update_state(PROGRESS) + update_task
- builder.run 五个位置参数与 KnowledgeService.run 签名前五形参对齐
- DB status 流转 running → success/failed,全部 ∈ TaskState 四态
- 失败先落 DB 再裸 raise(backend 才能标 FAILURE)
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from src.api.schemas_api import TaskState
from src.worker import tasks as tasks_module
from src.worker.tasks import knowledge_build_task


class TestKnowledgeBuildTask:
    def test_success_flow_dual_channel(self):
        svc = MagicMock()

        def _run(task, force, chunk_size, chunk_overlap, prune, progress=None):
            # 模拟 KnowledgeService 五阶段回调中的两个代表节点
            progress("parse-pdfs", "[1/5] 解析PDF...", 10)
            progress("build-indexes", "[5/5] 构建索引...", 90)

        svc.run.side_effect = _run
        with patch("src.api.services.knowledge_service.KnowledgeService", return_value=svc), \
                patch.object(knowledge_build_task, "update_state") as mock_state, \
                patch.object(tasks_module, "update_task") as mock_db:
            result = knowledge_build_task("build-all", True, 300, 50, False)

        # run 收到五个位置参数(与签名前五形参严格对齐)+ progress 回调
        assert svc.run.call_args.args == ("build-all", True, 300, 50, False)
        assert callable(svc.run.call_args.kwargs["progress"])

        # backend 通道:首帧 + 两次进度回调 = 3 次 PROGRESS
        assert mock_state.call_count == 3
        for c in mock_state.call_args_list:
            assert c.kwargs["state"] == "PROGRESS"

        # DB 通道:running 开场(queued/0 低起步) → 两个进度节点 → success 收尾
        calls = mock_db.call_args_list
        statuses = [c.kwargs.get("status") for c in calls]
        assert statuses == ["running", None, None, "success"]
        for s in statuses:
            if s is not None:
                assert s in TaskState.__args__, f"status={s!r} 破坏四态契约"
        first = calls[0].kwargs
        assert (first["stage"], first["percent"]) == ("queued", 0)
        assert calls[1].kwargs == {"stage": "parse-pdfs",
                                   "message": "[1/5] 解析PDF...", "percent": 10}
        final = calls[-1].kwargs
        assert final["percent"] == 100 and "finished_at" in final

        # 返回 None 可序列化(进 backend);构建任务无 result_path 语义
        assert result is None
        json.dumps(result)

    def test_failure_marks_failed_then_reraises(self):
        svc = MagicMock()
        svc.run.side_effect = RuntimeError("build boom")
        with patch("src.api.services.knowledge_service.KnowledgeService", return_value=svc), \
                patch.object(knowledge_build_task, "update_state"), \
                patch.object(tasks_module, "update_task") as mock_db:
            with pytest.raises(RuntimeError):
                knowledge_build_task("build-all", False, 300, 50, False)

        # 失败先落 DB(status 是契约值 "failed",不是 Celery 的 "FAILURE")再重抛
        final = mock_db.call_args_list[-1].kwargs
        assert final["status"] == "failed"
        assert final["status"] in TaskState.__args__
        assert "build boom" in final["error"]
        assert "finished_at" in final
