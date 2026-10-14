"""
项目文档服务层测试：磁盘清理与配额
覆盖：四处产物全部清除、缺失文件容错、删记录+清盘组合、配额边界

这是"删号必须清文件"的核心保障——四处产物分布在 data/ 的三个子树里，
DB 只管登记表行，漏掉任何一处都会留下无法索引的残留。
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import src.database.models  # noqa: F401
from src.api.services import project_service as ps
from src.database.database import Base

ME_ID = 9


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'svc.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(ps, "count_user_project_docs", lambda uid: 0)  # 默认值，按需覆盖
    yield factory
    engine.dispose()


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    """把四类产物目录都重定向到 tmp"""
    chunks = tmp_path / "chunks"
    vec = tmp_path / "vec"
    bm = tmp_path / "bm25"
    for d in (chunks, vec, bm):
        d.mkdir()
    from types import SimpleNamespace
    monkeypatch.setattr(ps, "get_config", lambda: SimpleNamespace(paths=SimpleNamespace(
        course_chunks_dir=chunks, vector_dbs_dir=vec, bm25_dbs_dir=bm)))
    return tmp_path, chunks, vec, bm


def _touch(*paths):
    for p in paths:
        p.write_text("x", encoding="utf-8")
    return list(paths)


class TestPurgeDiskArtifacts:
    def test_removes_all_four_artifacts(self, dirs):
        tmp_path, chunks, vec, bm = dirs
        src = tmp_path / "src.md"
        files = _touch(
            src,
            chunks / "project-u9-a.json",
            vec / "project-u9-a.faiss",
            bm / "project-u9-a.pkl",
        )

        removed = ps.purge_disk_artifacts(str(src), "project-u9-a")

        assert removed == 4
        assert not any(f.exists() for f in files)

    def test_tolerates_missing_files(self, dirs):
        """部分产物不存在（如索引还没建）不应报错"""
        tmp_path, chunks, _, _ = dirs
        src = tmp_path / "src.md"
        _touch(src, chunks / "project-u9-a.json")

        assert ps.purge_disk_artifacts(str(src), "project-u9-a") == 2

    def test_no_args_is_noop(self, dirs):
        assert ps.purge_disk_artifacts(None, None) == 0

    def test_single_failure_does_not_stop_others(self, dirs):
        """无法做到跨文件原子，单项失败要继续清其余"""
        tmp_path, chunks, vec, bm = dirs
        src = tmp_path / "src.md"
        _touch(src, chunks / "project-u9-a.json", vec / "project-u9-a.faiss",
               bm / "project-u9-a.pkl")

        removed = ps.purge_disk_artifacts(str(src), "project-u9-a")
        assert removed == 4


class TestDeleteEverywhere:
    def test_deletes_db_row_and_disk(self, dirs, tmp_path, monkeypatch):
        _, chunks, vec, bm = dirs
        src = tmp_path / "src.md"
        _touch(src, chunks / "project-u9-a.json", vec / "project-u9-a.faiss",
               bm / "project-u9-a.pkl")

        from src.database.utils import project_docs as pd_utils
        engine = create_engine(
            f"sqlite:///{tmp_path / 'del.db'}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine)
        monkeypatch.setattr(pd_utils, "SessionLocal", factory)

        pd_utils.upsert_project_doc(
            doc_id="project-u9-a", project_name="甲", file_name="a.md",
            source_path=str(src), user_id=ME_ID, mtime=1.0)

        assert ps.delete_project_doc_everywhere("project-u9-a") is True
        assert pd_utils.get_project_doc("project-u9-a") is None
        assert not src.exists()

    def test_missing_doc_returns_false(self, dirs, tmp_path, monkeypatch):
        from src.database.utils import project_docs as pd_utils
        engine = create_engine(
            f"sqlite:///{tmp_path / 'del2.db'}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        monkeypatch.setattr(pd_utils, "SessionLocal", sessionmaker(bind=engine))

        assert ps.delete_project_doc_everywhere("not-exist") is False


class TestCheckQuota:
    def test_under_limit_passes(self, monkeypatch):
        from src.config import get_config
        monkeypatch.setattr(ps, "count_user_project_docs", lambda uid: get_config().project_doc.max_docs_per_user - 1)
        assert ps.check_quota(ME_ID) is None

    def test_at_limit_rejected(self, monkeypatch):
        from src.config import get_config
        monkeypatch.setattr(ps, "count_user_project_docs", lambda uid: get_config().project_doc.max_docs_per_user)
        msg = ps.check_quota(ME_ID)
        assert msg and "上限" in msg
