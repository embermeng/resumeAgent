"""
检索器增量刷新测试
覆盖：VectorRetriever.refresh 的 新增/覆盖/移除三态、课程文档不受影响、
      BM25Retriever.invalidate 清缓存、AgentTools 刷新覆盖 hybrid 内部副本
"""
import json
import pickle

import pytest

from src.retrieval.retriever import BM25Retriever, VectorRetriever


def _write_chunk(chunks_dir, doc_id, source, texts):
    """写一个分块文档 JSON"""
    (chunks_dir / f"{doc_id}.json").write_text(
        json.dumps({
            "metainfo": {"doc_id": doc_id, "source": source, "category": "project"},
            "content": {"chunks": [
                {"id": i, "text": t, "length_tokens": len(t)}
                for i, t in enumerate(texts)
            ]},
        }, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_faiss(vector_dir, doc_id, tag):
    """写一个假 faiss 索引（用 pickle 占位，refresh 只关心能否加载）"""
    import faiss
    import numpy as np
    index = faiss.IndexFlatIP(4)
    index.add(np.array([[1.0, 0.0, 0.0, 0.0]], dtype=np.float32))
    faiss.write_index(index, str(vector_dir / f"{doc_id}.faiss"))
    return tag


@pytest.fixture
def dirs(tmp_path):
    chunks = tmp_path / "chunks"
    vec = tmp_path / "vec"
    bm = tmp_path / "bm25"
    for d in (chunks, vec, bm):
        d.mkdir()
    return chunks, vec, bm


class TestVectorRetrieverRefresh:
    def test_adds_new_doc(self, dirs):
        chunks, vec, _ = dirs
        r = VectorRetriever(vec, chunks)          # 构造时为空
        assert r.all_dbs == []

        _write_chunk(chunks, "project-u9-a", "项目A", ["内容A"])
        _write_faiss(vec, "project-u9-a", "a")
        stats = r.refresh(["project-u9-a"])

        assert stats == {"added": 1, "replaced": 0, "removed": 0}
        assert [e["doc_id"] for e in r.all_dbs] == ["project-u9-a"]

    def test_replaces_existing_doc(self, dirs):
        """覆盖上传：条目被替换而不是重复 append"""
        chunks, vec, _ = dirs
        _write_chunk(chunks, "project-u9-a", "项目A", ["旧内容"])
        _write_faiss(vec, "project-u9-a", "a")
        r = VectorRetriever(vec, chunks)
        assert len(r.all_dbs) == 1

        # 模拟覆盖：分块与索引都被重写
        _write_chunk(chunks, "project-u9-a", "项目A", ["新内容"])
        _write_faiss(vec, "project-u9-a", "a")
        stats = r.refresh(["project-u9-a"])

        assert stats["replaced"] == 1
        assert len(r.all_dbs) == 1
        assert r.all_dbs[0]["document"]["content"]["chunks"][0]["text"] == "新内容"

    def test_removes_deleted_doc(self, dirs):
        """移除只认显式 removed_ids"""
        chunks, vec, _ = dirs
        _write_chunk(chunks, "project-u9-a", "项目A", ["内容A"])
        _write_chunk(chunks, "project-u9-b", "项目B", ["内容B"])
        _write_faiss(vec, "project-u9-a", "a")
        _write_faiss(vec, "project-u9-b", "b")
        r = VectorRetriever(vec, chunks)
        assert len(r.all_dbs) == 2

        stats = r.refresh(["project-u9-a"], removed_ids=["project-u9-b"])

        assert stats["removed"] == 1
        assert [e["doc_id"] for e in r.all_dbs] == ["project-u9-a"]

    def test_does_not_evict_docs_outside_whitelist(self, dirs):
        """
        all_dbs 是**进程级共享**快照，doc_ids 是单个用户的白名单。
        按"不在白名单里"驱逐会把其他用户的内存索引一起删掉，而对方的版本比对
        看不出变化、不会重刷 → 静默失去向量检索。
        """
        chunks, vec, _ = dirs
        for doc_id in ("project-u9-a", "project-u1-a"):
            _write_chunk(chunks, doc_id, doc_id, [doc_id])
            _write_faiss(vec, doc_id, doc_id)
        r = VectorRetriever(vec, chunks)

        r.refresh(["project-u9-a"])                 # 用户 9 同步自己的白名单

        assert {e["doc_id"] for e in r.all_dbs} == {"project-u9-a", "project-u1-a"}

    def test_course_docs_untouched(self, dirs):
        """空白名单不应驱逐任何条目（课程文档更不可能被误删）"""
        chunks, vec, _ = dirs
        _write_chunk(chunks, "ab12cd34ef56", "课程X", ["课程内容"])
        _write_faiss(vec, "ab12cd34ef56", "c")
        r = VectorRetriever(vec, chunks)

        stats = r.refresh([])                     # 该用户无任何项目文档

        assert stats["removed"] == 0
        assert [e["doc_id"] for e in r.all_dbs] == ["ab12cd34ef56"]

    def test_missing_faiss_skipped(self, dirs):
        """分块存在但索引缺失时不加载（refresh 会检查两者都存在）"""
        chunks, vec, _ = dirs
        _write_chunk(chunks, "project-u9-a", "项目A", ["内容A"])
        r = VectorRetriever(vec, chunks)

        stats = r.refresh(["project-u9-a"])
        assert stats == {"added": 0, "replaced": 0, "removed": 0}

    def test_refresh_empty_is_noop(self, dirs):
        chunks, vec, _ = dirs
        _write_chunk(chunks, "ab12cd34ef56", "课程X", ["课程内容"])
        _write_faiss(vec, "ab12cd34ef56", "c")
        r = VectorRetriever(vec, chunks)
        before = [e["doc_id"] for e in r.all_dbs]

        r.refresh(None)                            # doc_ids 为 None 不应报错
        assert [e["doc_id"] for e in r.all_dbs] == before


class TestBM25Invalidate:
    def test_invalidate_clears_cache(self, dirs):
        chunks, _, bm = dirs
        _write_chunk(chunks, "project-u9-a", "项目A", ["内容A"])
        (bm / "project-u9-a.pkl").write_bytes(pickle.dumps({"index": 1}))

        r = BM25Retriever(bm, chunks)
        r._load_document("project-u9-a")
        r._load_bm25_index("project-u9-a")
        assert "project-u9-a" in r._doc_cache
        assert "project-u9-a" in r._index_cache

        r.invalidate(["project-u9-a"])
        assert "project-u9-a" not in r._doc_cache
        assert "project-u9-a" not in r._index_cache

    def test_invalidate_unknown_id_is_safe(self, dirs):
        chunks, _, bm = dirs
        r = BM25Retriever(bm, chunks)
        assert r.invalidate(["not-exist"]) == 0
        assert r.invalidate(None) == 0


class TestSyncProjectDocs:
    """
    AgentTools 的懒刷新。

    关键：HybridRetriever 构造时自己 new 了 VectorRetriever/BM25Retriever，
    与 AgentTools 三个 property 建的实例是不同对象。只刷后者的话，
    检索主路径（走 hybrid）根本看不到新文档——这正是要防的回归。
    """

    def _tools_with_hybrid(self):
        from unittest.mock import MagicMock, patch
        from src.agent.tools import AgentTools

        with patch("src.agent.tools.APIProcessor"):   # 构造时会建 LLM 客户端
            tools = AgentTools(config=MagicMock())
        hybrid = MagicMock()
        tools._hybrid_retriever = hybrid
        return tools, hybrid

    def test_first_call_still_refreshes(self):
        """
        首次同步也必须刷：检索器是**进程级共享**快照，它可能在别的用户先对话时
        就已构造完，此后本用户的上传不会自动进去。只记账 = 永远检索不到。
        """
        from datetime import datetime
        from unittest.mock import MagicMock, patch

        tools, hybrid = self._tools_with_hybrid()
        doc = MagicMock(doc_id="project-u9-a", updated_at=datetime(2026, 1, 1))

        with patch("src.database.utils.project_docs.list_visible_project_docs") as m:
            m.return_value = [doc]
            assert tools.sync_project_docs(9) is True

        hybrid.vector_retriever.refresh.assert_called_once_with(
            ["project-u9-a"], removed_ids=[])

    def test_unchanged_docs_are_not_reloaded(self):
        """只刷变化过的那份：未变文档的索引/分块留在缓存里，省掉整批 faiss 重读"""
        from datetime import datetime
        from unittest.mock import MagicMock, patch

        tools, hybrid = self._tools_with_hybrid()
        a = MagicMock(doc_id="project-u9-a", updated_at=datetime(2026, 1, 1))
        b = MagicMock(doc_id="project-u9-b", updated_at=datetime(2026, 1, 1))

        with patch("src.database.utils.project_docs.list_visible_project_docs") as m:
            m.return_value = [a, b]
            tools.sync_project_docs(9)
            b.updated_at = datetime(2026, 1, 2)     # 只有 b 变了
            tools.sync_project_docs(9)

        hybrid.vector_retriever.refresh.assert_called_with(
            ["project-u9-b"], removed_ids=[])
        hybrid.bm25_retriever.invalidate.assert_called_with(["project-u9-b"])

    def test_refreshes_hybrid_internal_retrievers(self):
        """快照变化时必须刷到 hybrid 内部的副本"""
        from datetime import datetime
        from unittest.mock import MagicMock, patch

        tools, hybrid = self._tools_with_hybrid()
        doc = MagicMock(doc_id="project-u9-a", updated_at=datetime(2026, 1, 1))

        with patch("src.database.utils.project_docs.list_visible_project_docs") as m:
            m.return_value = [doc]
            tools.sync_project_docs(9)          # 首次同步
            doc.updated_at = datetime(2026, 1, 2)
            assert tools.sync_project_docs(9) is True   # 二次刷新

        hybrid.vector_retriever.refresh.assert_called_with(
            ["project-u9-a"], removed_ids=[])
        hybrid.bm25_retriever.invalidate.assert_called_with(["project-u9-a"])
        assert hybrid.vector_retriever.refresh.call_count == 2

    def test_also_refreshes_standalone_retrievers(self):
        """降级路径用的独立实例（若已创建）同样要刷"""
        from datetime import datetime
        from unittest.mock import MagicMock, patch

        tools, hybrid = self._tools_with_hybrid()
        standalone_vec = MagicMock()
        standalone_bm = MagicMock()
        tools._vector_retriever = standalone_vec
        tools._bm25_retriever = standalone_bm
        doc = MagicMock(doc_id="project-u9-a", updated_at=datetime(2026, 1, 1))

        with patch("src.database.utils.project_docs.list_visible_project_docs") as m:
            m.return_value = [doc]
            tools.sync_project_docs(9)
            doc.updated_at = datetime(2026, 1, 2)
            tools.sync_project_docs(9)

        standalone_vec.refresh.assert_called_with(["project-u9-a"], removed_ids=[])
        standalone_bm.invalidate.assert_called_with(["project-u9-a"])

    def test_deleting_older_doc_still_triggers_refresh(self):
        """
        快照必须是逐文档字典：只比 MAX(updated_at) 时，删除**较旧**的那份
        不会改变版本号，判定"无变化"直接返回 → 已删文档继续从进程内缓存被供给。
        """
        from datetime import datetime
        from unittest.mock import MagicMock, patch

        tools, hybrid = self._tools_with_hybrid()
        old = MagicMock(doc_id="project-u9-old", updated_at=datetime(2026, 1, 1))
        new = MagicMock(doc_id="project-u9-new", updated_at=datetime(2026, 1, 3))

        with patch("src.database.utils.project_docs.list_visible_project_docs") as m:
            m.return_value = [old, new]
            tools.sync_project_docs(9)
            m.return_value = [new]                  # 删掉较旧的那份
            assert tools.sync_project_docs(9) is True

        # 剩下的那份没变，不必重读；已删的那份要显式移除 + 清缓存
        hybrid.vector_retriever.refresh.assert_called_with(
            [], removed_ids=["project-u9-old"])
        hybrid.bm25_retriever.invalidate.assert_called_with(["project-u9-old"])

    def test_no_change_skips_refresh(self):
        """快照未变则零开销（只有首次同步那一次调用）"""
        from datetime import datetime
        from unittest.mock import MagicMock, patch

        tools, hybrid = self._tools_with_hybrid()
        doc = MagicMock(doc_id="project-u9-a", updated_at=datetime(2026, 1, 1))

        with patch("src.database.utils.project_docs.list_visible_project_docs") as m:
            m.return_value = [doc]
            tools.sync_project_docs(9)
            assert tools.sync_project_docs(9) is False
            assert tools.sync_project_docs(9) is False

        assert hybrid.vector_retriever.refresh.call_count == 1
