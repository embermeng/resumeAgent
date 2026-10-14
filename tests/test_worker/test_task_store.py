"""
task_store 测试:SQLite 文件库直测(monkeypatch 换掉模块内 SessionLocal)。

覆盖契约:
- insert 恒为 pending 行,支持无归属任务(Step B knowledge build 形态)
- update 只写传入字段,finished_at float epoch → UTC datetime
- 三个函数全部吞 DB 异常(沿用 TaskManager 时代行为:DB 抖动不炸任务/端点)
- get_task 返回脱离 session 的实例,列属性仍可读(A-4 SSE 适配器依赖此行为)
- purge_other_resumes 每人只留一份简历:只删本人 parse-resume 行,返回产物路径交调用方删文件
"""
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import src.database.models as models  # noqa: F401  注册全部表到 Base.metadata
import src.worker.task_store as store
from src.database.database import Base


@pytest.fixture
def factory(tmp_path, monkeypatch):
    """SQLite 文件库 + 替换 task_store.SessionLocal"""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ts.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(store, "SessionLocal", session_factory)
    return session_factory


def _row(factory, task_id):
    with factory() as db:
        return db.query(models.BuildTask).filter_by(task_id=task_id).first()


def _as_utc(dt):
    """SQLite 不存时区,读回是 naive datetime;统一补 UTC 再比较"""
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)


class TestInsertTask:
    def test_creates_pending_row(self, factory):
        store.insert_task("t1", "parse-resume", 7, "a.pdf")
        row = _row(factory, "t1")
        assert row is not None
        assert (row.status, row.task, row.user_id, row.filename) == \
            ("pending", "parse-resume", 7, "a.pdf")
        # 方案 A:受理即写进度初值,SSE 快照/轮询首读不会拿到全 NULL(老 TaskManager 首帧恒为 queued/0)
        assert (row.stage, row.percent, row.message) == ("queued", 0, "排队中")

    def test_allows_missing_owner(self, factory):
        """Step B 形态:knowledge build 无 user_id/filename(列 nullable)"""
        store.insert_task("t2", "build-all", None, None)
        row = _row(factory, "t2")
        assert row.user_id is None and row.filename is None

    def test_db_error_swallowed(self, monkeypatch):
        def boom():
            raise RuntimeError("db down")
        monkeypatch.setattr(store, "SessionLocal", boom)
        store.insert_task("t3", "parse-resume", None, None)  # 不抛即通过


class TestUpdateTask:
    def test_partial_update_keeps_other_fields(self, factory):
        store.insert_task("t1", "parse-resume", 1, "a.pdf")
        store.update_task("t1", status="running", stage="parsing",
                          percent=50, message="MinerU解析中")
        row = _row(factory, "t1")
        assert (row.status, row.stage, row.percent, row.message) == \
            ("running", "parsing", 50.0, "MinerU解析中")
        assert row.filename == "a.pdf"  # 未传字段不得被冲掉

    def test_finished_at_epoch_to_utc_datetime(self, factory):
        store.insert_task("t1", "parse-resume", 1, "a.pdf")
        store.update_task("t1", status="success", finished_at=1767225600.0)
        row = _row(factory, "t1")
        assert _as_utc(row.finished_at) == datetime.fromtimestamp(1767225600.0, tz=UTC)

    def test_empty_fields_noop(self, factory):
        store.insert_task("t1", "parse-resume", 1, "a.pdf")
        store.update_task("t1")  # 不抛不写
        assert _row(factory, "t1").status == "pending"

    def test_missing_row_silent(self, factory):
        """0 行命中不报错(worker 只 update 不 insert,受理竞态由 A-5 先insert后delay保证)"""
        store.update_task("nope", status="running")

    def test_db_error_swallowed(self, factory, monkeypatch):
        store.insert_task("t1", "parse-resume", 1, "a.pdf")

        def boom():
            raise RuntimeError("db down")
        monkeypatch.setattr(store, "SessionLocal", boom)
        store.update_task("t1", status="running")  # 不抛即通过


class TestGetTask:
    def test_returns_row(self, factory):
        store.insert_task("t1", "parse-resume", 1, "a.pdf")
        rec = store.get_task("t1")
        assert rec is not None and rec.task_id == "t1"

    def test_missing_returns_none(self, factory):
        assert store.get_task("nope") is None

    def test_detached_column_attrs_readable(self, factory):
        """A-4 依赖:get_task 返回的实例在 session 关闭后仍可读列属性(不触 DetachedInstanceError)"""
        store.insert_task("t1", "parse-resume", 1, "a.pdf")
        store.update_task("t1", status="running", stage="parsing",
                          percent=50, message="m")
        rec = store.get_task("t1")
        assert (rec.status, rec.stage, rec.percent, rec.message) == \
            ("running", "parsing", 50.0, "m")

    def test_db_error_returns_none(self, monkeypatch):
        def boom():
            raise RuntimeError("db down")
        monkeypatch.setattr(store, "SessionLocal", boom)
        assert store.get_task("t1") is None


class TestPurgeOtherResumes:
    """每用户只保留一份简历:新简历成功后清掉同一用户的其余 parse-resume 行。"""

    def test_deletes_others_and_returns_paths(self, factory):
        store.insert_task("t-keep", "parse-resume", 7, "new.pdf")
        store.insert_task("t-old", "parse-resume", 7, "old.pdf")
        store.insert_task("t-failed", "parse-resume", 7, "bad.pdf")
        store.update_task("t-old", status="success", result_path="/tmp/old.md")
        # 失败/pending 的旧行也要清掉,否则永远留在表里没人再看见
        paths = store.purge_other_resumes(7, "t-keep")
        assert paths == ["/tmp/old.md"]
        assert _row(factory, "t-keep") is not None
        assert _row(factory, "t-old") is None
        assert _row(factory, "t-failed") is None

    def test_keeps_other_users_and_knowledge_tasks(self, factory):
        store.insert_task("t-mine-old", "parse-resume", 7, "a.pdf")
        store.insert_task("t-other", "parse-resume", 8, "b.pdf")
        store.insert_task("t-build", "build-all", 7, None)
        assert store.purge_other_resumes(7, "t-mine-new") == []
        assert _row(factory, "t-other") is not None
        assert _row(factory, "t-build") is not None

    def test_noop_when_no_other_rows(self, factory):
        store.insert_task("t-keep", "parse-resume", 7, "a.pdf")
        assert store.purge_other_resumes(7, "t-keep") == []
        assert _row(factory, "t-keep") is not None

    def test_db_error_swallowed_returns_empty(self, factory, monkeypatch):
        store.insert_task("t-old", "parse-resume", 7, "a.pdf")

        def boom():
            raise RuntimeError("db down")
        monkeypatch.setattr(store, "SessionLocal", boom)
        # 清理失败不能炸任务:旧的留着(顶多下次再清),但绝不能返回假路径让人删文件
        assert store.purge_other_resumes(7, "t-new") == []
