"""
项目文档可见性测试（多用户隔离的核心）
覆盖：公共+本人可见、跨用户不可见、未登录仅公共、标签过滤、DB 异常安全降级

用 SQLite 覆盖同步 SessionLocal，完全隔离开发库（与 tests/test_auth/conftest.py 同思路）。
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import src.database.models  # noqa: F401  注册全部表
from src.database.database import Base
from src.retrieval import visibility

PRIORITY_TAG = "简历优先"


@pytest.fixture
def session_factory(tmp_path):
    """SQLite 文件库 + sessionmaker，用于替换模块内的 SessionLocal"""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'visibility.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    yield factory
    engine.dispose()


@pytest.fixture
def seeded(session_factory, monkeypatch):
    """替换 SessionLocal 并灌入数据：u9 两份（一份带标签）、u10 一份、公共一份"""
    monkeypatch.setattr(visibility, "SessionLocal", session_factory)
    with session_factory() as db:
        db.add_all([
            src.database.models.ProjectDoc(
                doc_id="project-u9-aaa", user_id=9, project_name="甲项目",
                file_name="a.md", source_path="/p/u9/a.md", tags=[PRIORITY_TAG],
                has_resume_section=True, mtime=1.0),
            src.database.models.ProjectDoc(
                doc_id="project-u9-bbb", user_id=9, project_name="乙项目",
                file_name="b.md", source_path="/p/u9/b.md", tags=[],
                has_resume_section=False, mtime=1.0),
            src.database.models.ProjectDoc(
                doc_id="project-u10-ccc", user_id=10, project_name="丙项目",
                file_name="c.md", source_path="/p/u10/c.md", tags=[PRIORITY_TAG],
                has_resume_section=True, mtime=1.0),
            src.database.models.ProjectDoc(
                doc_id="project-ddd", user_id=None, project_name="公共示例",
                file_name="d.md", source_path="/p/d.md", tags=[],
                has_resume_section=False, mtime=1.0),
        ])
        db.commit()
    return session_factory


class TestVisibleProjectDocIds:
    def test_own_and_public_visible(self, seeded):
        ids = set(visibility.visible_project_doc_ids(9))
        assert ids == {"project-u9-aaa", "project-u9-bbb", "project-ddd"}

    def test_other_users_docs_not_visible(self, seeded):
        ids = visibility.visible_project_doc_ids(10)
        assert "project-u9-aaa" not in ids
        assert "project-u9-bbb" not in ids
        assert set(ids) == {"project-u10-ccc", "project-ddd"}

    def test_anonymous_sees_only_public(self, seeded):
        assert visibility.visible_project_doc_ids(None) == ["project-ddd"]

    def test_no_cross_user_leak(self, seeded):
        """两个用户的可见集合交集只可能是公共素材"""
        a = set(visibility.visible_project_doc_ids(9))
        b = set(visibility.visible_project_doc_ids(10))
        assert a & b == {"project-ddd"}

    def test_unknown_user_sees_only_public(self, seeded):
        assert visibility.visible_project_doc_ids(999) == ["project-ddd"]

    def test_db_error_returns_empty_not_unfiltered(self, seeded, monkeypatch):
        """DB 异常时必须返回空而不是"不过滤"，否则会跨用户泄露"""
        def _boom():
            raise RuntimeError("db down")

        monkeypatch.setattr(visibility, "SessionLocal", _boom)
        assert visibility.visible_project_doc_ids(9) == []


class TestVisibleTaggedProjectIds:
    def test_filters_by_tag_within_visibility(self, seeded):
        tagged = visibility.visible_tagged_project_ids(9, PRIORITY_TAG)
        assert tagged == [("project-u9-aaa", "甲项目")]

    def test_other_users_tagged_not_visible(self, seeded):
        tagged = visibility.visible_tagged_project_ids(10, PRIORITY_TAG)
        assert [t[0] for t in tagged] == ["project-u10-ccc"]

    def test_tag_absent_returns_empty(self, seeded):
        assert visibility.visible_tagged_project_ids(9, "不存在的标签") == []

    def test_public_tagged_visible_to_all(self, seeded):
        with seeded() as db:
            rec = db.query(src.database.models.ProjectDoc).filter_by(
                doc_id="project-ddd").one()
            rec.tags = [PRIORITY_TAG]
            db.commit()
        assert ("project-ddd", "公共示例") in visibility.visible_tagged_project_ids(9, PRIORITY_TAG)
        assert ("project-ddd", "公共示例") in visibility.visible_tagged_project_ids(10, PRIORITY_TAG)

    def test_db_error_returns_empty(self, seeded, monkeypatch):
        def _boom():
            raise RuntimeError("db down")

        monkeypatch.setattr(visibility, "SessionLocal", _boom)
        assert visibility.visible_tagged_project_ids(9, PRIORITY_TAG) == []
