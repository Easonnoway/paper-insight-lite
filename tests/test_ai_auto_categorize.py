import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import fastapi

import app as app_module


def make_categories():
    """Two top-level + one nested category; indices follow depth-first order."""
    return {
        "categories": [
            {"id": "cat-ml", "parent_id": None, "name": "ML", "position": 0, "paper_count": 0, "color": None, "created_at": None},
            {"id": "cat-nlp", "parent_id": None, "name": "NLP", "position": 1, "paper_count": 0, "color": None, "created_at": None},
            {"id": "cat-safety", "parent_id": "cat-ml", "name": "Safety", "position": 0, "paper_count": 0, "color": None, "created_at": None},
        ],
        "uncategorized_count": 0,
    }


def make_paper():
    return {
        "id": "arxiv:1706.03762",
        "title": "Attention Is All You Need",
        "abstract": "We propose the Transformer.",
        "authors": ["Vaswani"],
        "keywords": [],
        "pdf": None,
        "venue": None,
        "primary_area": None,
        "llm_response": None,
        "created_at": None,
    }


def llm_reply(indices):
    """Fake LLM returning the prompt-format JSON."""
    return json.dumps({"categories": indices}), ""


def install_mocks(monkeypatch, *, paper="default", categories=None, reply=None, llm_error=False):
    """paper="default" uses a fixture paper; paper=None means unknown paper."""
    assigned_calls: list[tuple[str, str]] = []

    resolved_paper = make_paper() if paper == "default" else paper
    monkeypatch.setattr(app_module, "get_paper", lambda pid: resolved_paper)
    monkeypatch.setattr(app_module, "list_paper_categories", lambda user_id: categories or make_categories())

    async def fake_llm_chat_collect(messages, **kwargs):
        if llm_error:
            return "抱歉，我无法完成该任务", ""
        return reply if reply is not None else llm_reply([1])

    monkeypatch.setattr(app_module, "_llm_chat_collect", fake_llm_chat_collect)

    def fake_assign(user_id, paper_id, category_id):
        assigned_calls.append((paper_id, category_id))

    monkeypatch.setattr(app_module, "assign_paper_category", fake_assign)
    return assigned_calls


def test_auto_categorize_assigns_matching_categories(monkeypatch):
    calls = install_mocks(monkeypatch, reply=llm_reply([2, 1]))
    result = asyncio.run(app_module.auto_categorize_paper("arxiv:1706.03762", {"id": "u1"}))
    assert result["ok"] is True
    assert [c["name"] for c in result["assigned"]] == ["Safety", "ML"]
    assert [c["id"] for c in result["assigned"]] == ["cat-safety", "cat-ml"]
    assert result["skipped"] == 0
    assert calls == [("arxiv:1706.03762", "cat-safety"), ("arxiv:1706.03762", "cat-ml")]


def test_auto_categorize_skips_out_of_range_and_garbage(monkeypatch):
    calls = install_mocks(monkeypatch, reply=llm_reply([99, "abc", None, 3]))
    result = asyncio.run(app_module.auto_categorize_paper("arxiv:1706.03762", {"id": "u1"}))
    assert [c["id"] for c in result["assigned"]] == ["cat-nlp"]
    assert result["skipped"] == 3
    assert calls == [("arxiv:1706.03762", "cat-nlp")]


def test_auto_categorize_dedupes_and_caps_at_three(monkeypatch):
    calls = install_mocks(monkeypatch, reply=llm_reply([1, 1, 3, 2, 1]))
    result = asyncio.run(app_module.auto_categorize_paper("arxiv:1706.03762", {"id": "u1"}))
    assert [c["id"] for c in result["assigned"]] == ["cat-ml", "cat-nlp", "cat-safety"]
    # The second 1 is a duplicate; the trailing 1 is never reached (cap break).
    assert result["skipped"] == 1
    assert len(calls) == 3


def test_auto_categorize_ignores_new_category_names(monkeypatch):
    # Contract violation: LLM invents names instead of returning indices.
    reply = json.dumps({"categories": ["深度学习", "RL"]}), ""
    calls = install_mocks(monkeypatch, reply=reply)
    result = asyncio.run(app_module.auto_categorize_paper("arxiv:1706.03762", {"id": "u1"}))
    assert result["assigned"] == []
    assert result["message"] == "没有找到合适的分类"
    assert calls == []


def test_auto_categorize_empty_selection_returns_message(monkeypatch):
    install_mocks(monkeypatch, reply=llm_reply([]))
    result = asyncio.run(app_module.auto_categorize_paper("arxiv:1706.03762", {"id": "u1"}))
    assert result["assigned"] == []
    assert result["message"] == "没有找到合适的分类"


def test_auto_categorize_rejects_unparseable_reply(monkeypatch):
    install_mocks(monkeypatch, llm_error=True)
    try:
        asyncio.run(app_module.auto_categorize_paper("arxiv:1706.03762", {"id": "u1"}))
        raise AssertionError("expected HTTPException")
    except fastapi.HTTPException as exc:
        assert exc.status_code == 502
        assert "解析失败" in exc.detail


def test_auto_categorize_requires_categories(monkeypatch):
    install_mocks(monkeypatch, categories={"categories": [], "uncategorized_count": 0})
    try:
        asyncio.run(app_module.auto_categorize_paper("arxiv:1706.03762", {"id": "u1"}))
        raise AssertionError("expected HTTPException")
    except fastapi.HTTPException as exc:
        assert exc.status_code == 400


def test_auto_categorize_unknown_paper(monkeypatch):
    install_mocks(monkeypatch, paper=None)
    try:
        asyncio.run(app_module.auto_categorize_paper("ghost", {"id": "u1"}))
        raise AssertionError("expected HTTPException")
    except fastapi.HTTPException as exc:
        assert exc.status_code == 404


def test_format_category_tree_for_prompt_numbering():
    outline, indexes = app_module._format_category_tree_for_prompt(make_categories()["categories"])
    lines = outline.split("\n")
    assert lines[0] == "1. ML"
    # Depth-first: nested Safety comes right after its parent.
    assert "2.   Safety" in lines[1]
    assert lines[2] == "3. NLP"
    assert indexes == {"cat-ml": 1, "cat-safety": 2, "cat-nlp": 3}
