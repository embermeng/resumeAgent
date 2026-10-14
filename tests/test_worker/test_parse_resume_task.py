"""
正式 parse_resume_task 任务体 + celery_app 队列路由测试。

任务体以 eager 方式直调(mock update_state/update_task/ResumeFileParser),
不起真 worker、不连真 broker/backend、不碰真 DB。

覆盖契约:
- 进度双通道:update_state(PROGRESS)×2 给 backend,update_task 给权威 DB
- DB status 流转 running → (进度只更 stage/percent/message) → success/failed
- 所有写进 DB 的 status 必须在 TaskState 四态内(回归守卫:曾写 "failure" 破契约)
- .upload 成功/失败两条路都在 finally 清理
- 失败先落 DB 再裸 raise(backend 才能标 FAILURE)
- 每人一份简历:成功回写后 purge 同用户旧行并删其产物;失败/无 user_id 一律不动旧的
- task_routes 把两个具名任务路由到 resume/knowledge 队列 + acks_late
"""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.api.schemas_api import TaskState
from src.config import get_config
from src.worker import tasks as tasks_module
from src.worker.celery_app import celery_app
from src.worker.tasks import parse_resume_task


class TestTaskRoutes:
    def test_routes_to_dedicated_queues(self):
        """路由键是任务名字符串;写错不报错、只是消息进默认队列没人消费(broker 键名同款静默坑)"""
        routes = celery_app.conf.task_routes
        assert routes["resumeagent.parse_resume"]["queue"] == "resume"
        assert routes["resumeagent.knowledge_build"]["queue"] == "knowledge"

    def test_acks_late_enabled(self):
        """worker 崩溃时消息回队列重投而非丢失(解析管线幂等,重跑安全)"""
        assert celery_app.conf.task_acks_late is True


@pytest.fixture
def task_env(tmp_path, monkeypatch):
    """产物目录重定向到 tmp(paths 是 dataclass 实例属性可整体替换;
    resume_uploads_dir 是无 setter 的 property,不能在实例上单点 patch)"""
    monkeypatch.setattr(
        get_config(), "paths", SimpleNamespace(resume_uploads_dir=tmp_path))
    upload = tmp_path / "x.upload.pdf"
    upload.write_bytes(b"%PDF-1.4 fake")
    return tmp_path, upload


class TestParseResumeTask:
    def test_success_flow_dual_channel(self, task_env):
        tmp_path, upload = task_env
        parser = MagicMock()
        parser.parse.return_value = "# 简历 markdown"
        with patch("src.knowledge.resume_file_parser.ResumeFileParser", return_value=parser), \
                patch.object(parse_resume_task, "update_state") as mock_state, \
                patch.object(tasks_module, "update_task") as mock_db:
            result = parse_resume_task(str(upload), "x.pdf")

        # backend 通道:两次 PROGRESS
        assert mock_state.call_count == 2
        for c in mock_state.call_args_list:
            assert c.kwargs["state"] == "PROGRESS"

        # DB 通道:running 开场 → 中途只更进度字段 → success 收尾
        statuses = [c.kwargs.get("status") for c in mock_db.call_args_list]
        assert statuses == ["running", None, "success"]
        for s in statuses:
            if s is not None:
                assert s in TaskState.__args__, f"status={s!r} 破坏四态契约"
        final = mock_db.call_args_list[-1].kwargs
        assert final["percent"] == 100
        assert "result_path" in final and "finished_at" in final

        # 产物与返回值(可序列化,进 backend)
        assert result["filename"] == "x.pdf"
        assert Path(result["result_path"]).read_text(encoding="utf-8") == "# 简历 markdown"
        json.dumps(result)

        # PoC 债 #1 已还:.upload 被清理
        assert not upload.exists()

    def test_failure_marks_failed_then_reraises(self, task_env):
        tmp_path, upload = task_env
        parser = MagicMock()
        parser.parse.side_effect = ValueError("parse boom")
        with patch("src.knowledge.resume_file_parser.ResumeFileParser", return_value=parser), \
                patch.object(parse_resume_task, "update_state"), \
                patch.object(tasks_module, "update_task") as mock_db, \
                patch.object(tasks_module, "purge_other_resumes") as mock_purge:
            with pytest.raises(ValueError):
                parse_resume_task(str(upload), "x.pdf", 7)

        # 失败先落 DB(status 必须是契约值 "failed",不是 Celery 的 "FAILURE"/"failure")
        final = mock_db.call_args_list[-1].kwargs
        assert final["status"] == "failed"
        assert final["status"] in TaskState.__args__
        assert "parse boom" in final["error"]
        assert "finished_at" in final
        # 失败路径 finally 同样清理 .upload
        assert not upload.exists()
        # 新简历没解析成功 → 旧的必须原样留着(否则传错文件就把简历弄丢了)
        mock_purge.assert_not_called()


class TestParseResumeSupersede:
    """每人只保留一份简历:成功回写后由 worker 顶替掉同用户的旧简历。"""

    def test_success_purges_previous_rows_and_files(self, task_env):
        tmp_path, upload = task_env
        stale = tmp_path / "stale.md"
        stale.write_text("# 旧简历", encoding="utf-8")
        gone = tmp_path / "already-gone.md"   # 产物已被外部清掉,unlink 要容错
        parser = MagicMock()
        parser.parse.return_value = "# 新简历"
        with patch("src.knowledge.resume_file_parser.ResumeFileParser", return_value=parser), \
                patch.object(parse_resume_task, "update_state"), \
                patch.object(tasks_module, "update_task"), \
                patch.object(tasks_module, "purge_other_resumes",
                             return_value=[str(stale), str(gone)]) as mock_purge:
            r = parse_resume_task.apply(args=(str(upload), "x.pdf", 7), task_id="t-new")

        assert r.successful()
        # 只认本次消息里带的 user_id,保留的正是当前 task
        assert mock_purge.call_args.args == (7, "t-new")
        assert not stale.exists()
        assert not upload.exists()

    def test_legacy_message_without_user_id_skips_purge(self, task_env):
        """旧格式消息(只带路径+文件名)在队列里残留时,不能误删任何人的简历。"""
        _, upload = task_env
        parser = MagicMock()
        parser.parse.return_value = "# 简历"
        with patch("src.knowledge.resume_file_parser.ResumeFileParser", return_value=parser), \
                patch.object(parse_resume_task, "update_state"), \
                patch.object(tasks_module, "update_task"), \
                patch.object(tasks_module, "purge_other_resumes") as mock_purge:
            parse_resume_task(str(upload), "x.pdf")

        mock_purge.assert_not_called()
