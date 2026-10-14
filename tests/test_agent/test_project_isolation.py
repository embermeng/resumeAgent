"""
多用户项目文档隔离回归测试

复现三处缺陷（隔离改造漏在降级/兜底分支与进程级单例快照上）：

1. `src/agent/graph.py` 有两条检索路径不带可见性约束：
   - L109 非流式 quick_response 节点：识别到 project_name 时置 category="project"，
     但不传 doc_ids 白名单（流式版 L273 已修，节点版漏了）
   - L184 `_hierarchical_search` 的降级分支：category=None + doc_ids=None，扫全库
   后果：他人私有项目分块进入自己的回答/简历上下文。且 doc_ids=None 时
   `build_key` 生成的缓存 key 对所有用户相同，泄露结果会被 Redis 缓存后分发给所有人。

2. `VectorRetriever.refresh` 按"单个用户的白名单"移除快照里其余 `project-` 条目，
   而检索器是进程级单例 → 用户 A 同步会踢掉用户 B 的内存索引；又因懒刷新按 user_id
   记账，B 下次对话版本未变不会重刷 → B 静默失去向量检索半边。

3. `AgentTools.sync_project_docs` 的版本号只取 MAX(updated_at)：删除**较旧**的文档时
   版本不变 → 不触发失效 → 已删文档仍留在 BM25 `_doc_cache` / 向量 `all_dbs` 里，
   用户删了还能被检索到，直到进程重启。

这些用例断言的都是**外部可观察行为**（检索结果里有没有别人的/已删的内容），
不绑定具体修法。三处均已修复，本文件是回归守卫。
"""
import json
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.knowledge.ingestion import BM25Ingestor
from src.retrieval.retriever import BM25Retriever, VectorRetriever

# 课程文档是纯 hex doc_id（公共资源，所有人可见）
COURSE_DOC = "abc123def456"
COURSE_TEXT = "LangChain 链式编排"
# 用户 9 的私有项目
U9_DOC = "project-u9-a"
U9_TEXT = "结算中台"
U9_OLD_DOC = "project-u9-old"
U9_OLD_TEXT = "密钥轮换"
# 用户 1 的私有项目：对用户 9 必须不可见
U1_DOC = "project-u1-a"
U1_TEXT = "薪酬明细"
# 用户 9 新上传的文档（触发同步刷新用）
U9_NEW_DOC = "project-u9-new"
U9_NEW_TEXT = "灰度发布"


def _write_chunk(chunks_dir, doc_id, category, texts):
    """
    分块文档 JSON。每份文档给多个分块：BM25 的 IDF 在"单分块文档"下恒为 0
    （N=1、df=1 → idf<0 → rank_bm25 归零），检索层会把零分结果过滤掉，
    测出来的"没泄露"是假绿。多分块才能让关键词命中真正得分。
    """
    doc = {
        "metainfo": {"doc_id": doc_id, "source": f"{doc_id}.md", "category": category},
        "content": {"chunks": [
            {"id": i, "text": t, "length_tokens": len(t)}
            for i, t in enumerate(texts)
        ]},
    }
    (chunks_dir / f"{doc_id}.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return doc


def _write_doc(kb, doc_id, category, texts, with_bm25=True, with_faiss=False):
    """落一份完整检索产物：分块 JSON + BM25 pkl + （可选）faiss"""
    if isinstance(texts, str):
        texts = [texts]
    doc = _write_chunk(kb.chunks, doc_id, category, texts)
    if with_bm25:
        BM25Ingestor().process_single(doc, kb.bm25)
    if with_faiss:
        import faiss
        import numpy as np
        index = faiss.IndexFlatIP(4)
        index.add(np.array([[1.0, 0.0, 0.0, 0.0]] * len(texts), dtype=np.float32))
        faiss.write_index(index, str(kb.vec / f"{doc_id}.faiss"))
    return doc


@pytest.fixture
def kb(tmp_path):
    """知识库：1 份公共课程 + 用户9 两份私有 + 用户1 一份私有（均带 BM25 索引）"""
    chunks = tmp_path / "chunks"
    bm25 = tmp_path / "bm25"
    vec = tmp_path / "vec"
    for d in (chunks, bm25, vec):
        d.mkdir()
    ns = SimpleNamespace(chunks=chunks, bm25=bm25, vec=vec, root=tmp_path)
    for doc_id in DOC_TEXTS:
        if doc_id != U9_NEW_DOC:            # 新文档由用例自己"上传"
            _materialize(ns, doc_id)
    return ns


# 每份文档的分块文本（集中定义，便于按需重建同一份产物）
DOC_TEXTS = {
    COURSE_DOC: [
        f"课程知识：{COURSE_TEXT} 的原理与用法",
        "课程知识：提示词工程的几个要点",
        "课程知识：向量数据库的选型对比",
    ],
    U9_DOC: [
        f"用户九的私密项目 {U9_TEXT} 的架构设计",
        "用户九记录的部署与运维细节",
        "用户九整理的一次线上故障复盘",
    ],
    U9_OLD_DOC: [
        f"用户九的旧项目 {U9_OLD_TEXT} 方案",
        "用户九旧项目的技术选型记录",
        "用户九旧项目的上线检查清单",
    ],
    U1_DOC: [
        f"用户一的私密项目 {U1_TEXT} 系统的架构设计",
        "用户一记录的权限模型设计",
        "用户一整理的一次性能优化过程",
    ],
    U9_NEW_DOC: [
        f"用户九新上传的项目 {U9_NEW_TEXT} 流程",
        "用户九新项目的监控指标",
        "用户九新项目的回滚预案",
    ],
}
DOC_CATEGORY = {COURSE_DOC: "course"}     # 其余均为 project


def _materialize(kb, doc_id, with_bm25=True, with_faiss=False):
    """按 DOC_TEXTS 落一份产物（可只补 faiss，文本与既有分块保持一致）"""
    texts = DOC_TEXTS[doc_id]
    return _write_doc(kb, doc_id, DOC_CATEGORY.get(doc_id, "project"), texts,
                      with_bm25=with_bm25, with_faiss=with_faiss)


def _real_tools(kb):
    """真实 AgentTools（只 mock LLM 客户端），检索器指向临时知识库"""
    from src.agent.tools import AgentTools

    config = MagicMock()
    config.paths.vector_dbs_dir = kb.vec
    config.paths.bm25_dbs_dir = kb.bm25
    config.paths.course_chunks_dir = kb.chunks
    config.paths.projects_dir = kb.root / "projects"
    config.embedding.provider = "dashscope"
    config.embedding.model = "text-embedding-v1"
    with patch("src.agent.tools.APIProcessor"):
        return AgentTools(config=config)


def _doc_rec(doc_id, updated_at):
    return MagicMock(doc_id=doc_id, updated_at=updated_at)


# ---------------------------------------------------------------------------
# 缺陷 1：graph 的两条无约束检索路径
# ---------------------------------------------------------------------------
class _RetrievingTools:
    """
    只把 search_knowledge 接到**真实 BM25 检索器**上（签名与 AgentTools 一致：
    category / top_n / doc_ids 原样透传），其余方法返回占位值。

    这样"隔离是否生效"由真实检索语义裁判，而不是断言 mock 收到了什么参数——
    修法人可以自己决定用 doc_ids 白名单、category 收窄还是排除列表。
    """

    def __init__(self, kb):
        self._bm25 = BM25Retriever(kb.bm25, kb.chunks)
        self.knowledge_calls = []

    def search_knowledge(self, query, category=None, top_n=5, doc_ids=None):
        self.knowledge_calls.append({"category": category, "doc_ids": doc_ids})
        results = self._bm25.retrieve(
            query, category=category, top_n=top_n, doc_ids=doc_ids)
        return "\n".join(r["text"] for r in results)

    def answer_question(self, user_input, knowledge):
        return knowledge

    def answer_question_stream(self, user_input, knowledge):
        yield knowledge

    def select_resume_knowledge(self, *args, **kwargs):
        return None                      # 强制走 _hierarchical_search 的降级分支

    def retrieve_by_plan(self, plan):
        return ""

    def select_project_docs(self, *args, **kwargs):
        return ""

    def search_projects(self, *args, **kwargs):
        return ""

    def generate_resume(self, **kwargs):
        return kwargs.get("knowledge", "")

    def optimize_resume(self, draft, job_requirement):
        return draft


@contextmanager
def _agent(kb, intent, entities, whitelist):
    """
    ResumeAgent：外部依赖全 mock，tools 换成接真实检索器的假 tools。

    whitelist 模拟 visible_doc_ids 的返回值 = 公共课程分块 + 本人项目分块
    （不含 U1_DOC / U9_OLD_DOC 之外他人的文档），graph 必须把它下传给检索。
    """
    from src.agent.graph import ResumeAgent
    from src.agent.intent import IntentResult

    with patch("src.agent.graph.IntentClassifier") as intent_cls, \
            patch("src.agent.graph.AgentTools"), \
            patch("src.agent.graph.APIProcessor"), \
            patch("src.agent.graph.get_config") as get_config, \
            patch("src.agent.graph.visible_doc_ids",
                  return_value=list(whitelist)) as visible:
        config = MagicMock()
        get_config.return_value = config
        classifier = MagicMock()
        classifier.classify.return_value = IntentResult(
            intent=intent, entities=entities, confidence=0.9)
        intent_cls.return_value = classifier

        agent = ResumeAgent(config=config)
        agent.tools = _RetrievingTools(kb)
        agent._whitelist_calls = visible
        yield agent


def _done_event(events):
    done = [e for e in events if e.get("type") == "done"]
    assert done, f"未收到 done 帧: {events}"
    return done[-1]


class TestQuickResponseIsolation:
    """非流式 quick_response 节点（graph.py L103-109）"""

    def test_project_category_does_not_leak_other_user(self, kb):
        """识别到项目名 → category="project"，此时必须受白名单约束"""
        from src.agent.intent import IntentType

        with _agent(kb, IntentType.QUICK_RESPONSE,
                    {"project_name": U1_TEXT}, [COURSE_DOC, U9_DOC]) as agent:
            result = agent.run(f"{U1_TEXT} 系统是怎么设计的？", user_id=9)

        assert U1_TEXT not in result["retrieved_knowledge"], (
            "quick_response 节点把他人私有项目分块检回来了："
            f"{result['retrieved_knowledge']!r}")

    def test_no_category_does_not_leak_other_user(self, kb):
        """无项目名 → category=None 扫全库，同样不能带出他人项目分块"""
        from src.agent.intent import IntentType

        with _agent(kb, IntentType.QUICK_RESPONSE, {}, [COURSE_DOC, U9_DOC]) as agent:
            result = agent.run(f"{U1_TEXT} 系统是怎么设计的？", user_id=9)

        assert U1_TEXT not in result["retrieved_knowledge"], (
            "category=None 的知识检索越过了可见性边界："
            f"{result['retrieved_knowledge']!r}")

    def test_course_knowledge_still_retrievable(self, kb):
        """
        隔离不能把公共课程知识一起切掉——修法人若用"只传项目白名单"来堵泄露，
        这条会红：课程文档不在 visible_project_doc_ids 里，会被一并排除。
        """
        from src.agent.intent import IntentType

        with _agent(kb, IntentType.QUICK_RESPONSE, {}, [COURSE_DOC, U9_DOC]) as agent:
            result = agent.run(f"{COURSE_TEXT} 是怎么用的？", user_id=9)

        assert COURSE_TEXT in result["retrieved_knowledge"], (
            "公共课程知识被隔离逻辑一起切掉了，检索结果："
            f"{result['retrieved_knowledge']!r}")

    def test_stream_quick_response_does_not_leak_other_user(self, kb):
        """流式快速回答（graph.py L268-276 已带白名单）——回归守卫"""
        from src.agent.intent import IntentType

        with _agent(kb, IntentType.QUICK_RESPONSE,
                    {"project_name": U1_TEXT}, [COURSE_DOC, U9_DOC]) as agent:
            events = list(agent.run_stream(
                f"{U1_TEXT} 系统是怎么设计的？", user_id=9))

        assert U1_TEXT not in _done_event(events).get("retrieved_knowledge", "")

    def test_stream_no_category_does_not_leak_other_user(self, kb):
        """流式快速回答的 category=None 分支同样不能扫到他人项目分块"""
        from src.agent.intent import IntentType

        with _agent(kb, IntentType.QUICK_RESPONSE, {}, [COURSE_DOC, U9_DOC]) as agent:
            events = list(agent.run_stream(
                f"{U1_TEXT} 系统是怎么设计的？", user_id=9))

        assert U1_TEXT not in _done_event(events).get("retrieved_knowledge", "")


class TestHierarchicalFallbackIsolation:
    """`_hierarchical_search` 的降级分支（graph.py L184）：深思路径主链路"""

    def test_fallback_does_not_leak_other_user(self, kb):
        from src.agent.intent import IntentType

        with _agent(kb, IntentType.DEEP_THINKING, {}, [COURSE_DOC, U9_DOC]) as agent:
            result = agent.run(f"{U1_TEXT} 系统是怎么设计的？", user_id=9)

        assert U1_TEXT not in result["retrieved_knowledge"], (
            "分层检索降级分支未带可见性约束，他人私有项目进入了简历上下文："
            f"{result['retrieved_knowledge']!r}")

    def test_stream_fallback_does_not_leak_other_user(self, kb):
        """Web 主路径：流式深思"""
        from src.agent.intent import IntentType

        with _agent(kb, IntentType.DEEP_THINKING, {}, [COURSE_DOC, U9_DOC]) as agent:
            events = list(agent.run_stream(
                f"{U1_TEXT} 系统是怎么设计的？", user_id=9))

        done = _done_event(events)
        assert U1_TEXT not in done.get("retrieved_knowledge", ""), (
            "流式深思路径的降级检索泄露了他人项目分块")
        assert U1_TEXT not in done.get("resume_draft", ""), (
            "泄露内容已写进生成的简历草稿")

    def test_fallback_still_returns_course_knowledge(self, kb):
        """降级分支仍需能召回公共课程知识（防止"一刀切空白名单"式修法）"""
        from src.agent.intent import IntentType

        with _agent(kb, IntentType.DEEP_THINKING, {}, [COURSE_DOC, U9_DOC]) as agent:
            result = agent.run(f"{COURSE_TEXT} 是怎么用的？", user_id=9)

        assert COURSE_TEXT in result["retrieved_knowledge"]


# ---------------------------------------------------------------------------
# 缺陷 2：refresh 跨用户互踢
# ---------------------------------------------------------------------------
class TestRefreshDoesNotEvictOtherUsers:
    def test_refresh_keeps_other_users_entries(self, kb):
        """
        refresh 传的是**单个用户**的白名单，但 all_dbs 是进程级共享快照。
        移除白名单外的 project- 条目 = 把别人的内存索引删了。
        检索层本来就按 doc_ids 过滤，内存里多留他人的条目并不构成泄露。
        """
        _materialize(kb, U9_DOC, with_bm25=False, with_faiss=True)
        _materialize(kb, U1_DOC, with_bm25=False, with_faiss=True)
        r = VectorRetriever(kb.vec, kb.chunks)
        assert {e["doc_id"] for e in r.all_dbs} >= {U9_DOC, U1_DOC}

        r.refresh([U9_DOC])                     # 用户 9 同步自己的白名单

        present = {e["doc_id"] for e in r.all_dbs}
        assert U1_DOC in present, (
            "用户 9 的同步把用户 1 的向量索引从共享快照里踢掉了")

    def test_alternating_sync_keeps_both_users_visible(self, kb):
        """
        真实事故序列：A 上传后同步（refresh 踢掉 B）→ B 再对话时自己的版本未变、
        不触发重刷 → B 的文档从此不在快照里，且没有任何日志提示。
        """
        _materialize(kb, U9_DOC, with_bm25=False, with_faiss=True)
        _materialize(kb, U1_DOC, with_bm25=False, with_faiss=True)
        tools = _real_tools(kb)
        tools.vector_retriever                      # 触发构造，加载全量快照

        u9_docs = [_doc_rec(U9_DOC, datetime(2026, 1, 1))]
        u1_docs = [_doc_rec(U1_DOC, datetime(2026, 1, 1))]

        def fake_list(user_id):
            return list(u9_docs) if user_id == 9 else list(u1_docs)

        with patch("src.database.utils.project_docs.list_visible_project_docs",
                   side_effect=fake_list):
            tools.sync_project_docs(9)              # 首次记账
            tools.sync_project_docs(1)              # 首次记账
            # 用户 9 上传新文档
            _materialize(kb, U9_NEW_DOC, with_bm25=False, with_faiss=True)
            u9_docs.append(_doc_rec(U9_NEW_DOC, datetime(2026, 1, 2)))
            assert tools.sync_project_docs(9) is True
            # 用户 1 什么都没做，再对话一次
            assert tools.sync_project_docs(1) is False

        present = {e["doc_id"] for e in tools.vector_retriever.all_dbs}
        assert U1_DOC in present, (
            "用户 9 的上传让共享快照丢掉了用户 1 的文档，"
            "而用户 1 的版本号未变不会重刷 → 静默失去向量检索")
        assert U9_NEW_DOC in present


# ---------------------------------------------------------------------------
# 缺陷 3：删除较旧文档后的幽灵
# ---------------------------------------------------------------------------
class TestDeletedDocGhost:
    def _sync(self, tools, docs_by_user):
        """
        docs_by_user 是**同一个列表对象**，测试内用切片赋值原地改它——
        side_effect 闭包持有的是这个对象，重新绑定局部变量它看不到。
        """
        return patch("src.database.utils.project_docs.list_visible_project_docs",
                     side_effect=lambda uid: list(docs_by_user))

    def test_deleting_older_doc_stops_serving_it(self, kb):
        """
        删除**较旧**那份文档：MAX(updated_at) 不变 → 版本判定"无变化" → 不失效。
        但磁盘产物已被 purge，BM25 的 _doc_cache 仍持有旧内容 → 继续供给已删文档。
        """
        tools = _real_tools(kb)
        query = f"{U9_OLD_TEXT} 方案"
        # 预热：两份文档都进过缓存
        primed = tools.bm25_retriever.retrieve(query, doc_ids=[U9_OLD_DOC])
        assert primed, "预热失败：BM25 应能检到旧文档"

        docs = [_doc_rec(U9_OLD_DOC, datetime(2026, 1, 1)),
                _doc_rec(U9_DOC, datetime(2026, 1, 3))]
        with self._sync(tools, docs):
            tools.sync_project_docs(9)              # 记账：版本 = 2026-01-03
            # 删除较旧的文档（模拟 purge_disk_artifacts 已清掉产物）
            (kb.chunks / f"{U9_OLD_DOC}.json").unlink()
            (kb.bm25 / f"{U9_OLD_DOC}.pkl").unlink()
            docs[:] = [_doc_rec(U9_DOC, datetime(2026, 1, 3))]
            tools.sync_project_docs(9)              # 版本未变 → 当前实现直接返回

        ghost = tools.bm25_retriever.retrieve(query, doc_ids=[U9_OLD_DOC])
        assert ghost == [], (
            "已删除的项目文档仍从进程内缓存被检回（删除较旧文档时版本号不变，"
            f"未触发失效）：{ghost!r}")

    def test_deleting_older_doc_drops_vector_entry(self, kb):
        """同一根因的向量侧：all_dbs 里的条目在删除后依然存在"""
        _materialize(kb, U9_OLD_DOC, with_bm25=False, with_faiss=True)
        _materialize(kb, U9_DOC, with_bm25=False, with_faiss=True)
        tools = _real_tools(kb)
        tools.vector_retriever

        docs = [_doc_rec(U9_OLD_DOC, datetime(2026, 1, 1)),
                _doc_rec(U9_DOC, datetime(2026, 1, 3))]
        with self._sync(tools, docs):
            tools.sync_project_docs(9)
            (kb.chunks / f"{U9_OLD_DOC}.json").unlink()
            (kb.vec / f"{U9_OLD_DOC}.faiss").unlink()
            docs[:] = [_doc_rec(U9_DOC, datetime(2026, 1, 3))]
            tools.sync_project_docs(9)

        present = {e["doc_id"] for e in tools.vector_retriever.all_dbs}
        assert U9_OLD_DOC not in present, (
            "已删除文档的向量索引仍留在进程内快照中，会被继续检索到")

    def test_deleting_newest_doc_also_clears_bm25_cache(self, kb):
        """
        删除**最新**那份时版本号会变、刷新会触发，但 invalidate 收到的是
        "当前可见 doc_ids"，不含已删的那个 → BM25 缓存里的幽灵同样清不掉。
        """
        tools = _real_tools(kb)
        query = f"{U9_TEXT} 架构设计"
        assert tools.bm25_retriever.retrieve(query, doc_ids=[U9_DOC])

        docs = [_doc_rec(U9_DOC, datetime(2026, 1, 5)),
                _doc_rec(U9_OLD_DOC, datetime(2026, 1, 1))]
        with self._sync(tools, docs):
            tools.sync_project_docs(9)
            (kb.chunks / f"{U9_DOC}.json").unlink()
            (kb.bm25 / f"{U9_DOC}.pkl").unlink()
            docs[:] = [_doc_rec(U9_OLD_DOC, datetime(2026, 1, 1))]
            tools.sync_project_docs(9)              # 版本变化 → 会刷新

        assert tools.bm25_retriever.retrieve(query, doc_ids=[U9_DOC]) == [], (
            "删除最新文档后，其分块仍留在 BM25 缓存中被供给")
