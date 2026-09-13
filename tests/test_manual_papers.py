import asyncio
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pytest

import app as app_module
import database
from database import DatabaseError, upsert_manual_paper


class ManualPaperCursor:
    """Records SQL; the papers RETURNING mirrors SELECT *."""

    def __init__(self):
        self.calls = []
        self.current_rows = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, query, params=None):
        self.calls.append((" ".join(query.split()), params))
        sql, params_ = self.calls[-1]
        if "INSERT INTO papers" in sql:
            self.current_rows = [{
                "id": params_[0], "title": params_[1], "abstract": params_[2],
                "keywords": [], "pdf": params_[4], "venue": params_[5],
                "primary_area": params_[6], "published_year": params_[7],
            }]
        elif "UPDATE papers" in sql:
            self.current_rows = [{
                "id": params_[7], "title": params_[0], "abstract": params_[1],
                "keywords": [], "pdf": params_[3], "venue": params_[4],
                "primary_area": params_[5], "published_year": params_[6],
            }]
        else:
            self.current_rows = []

    def executemany(self, query, params_seq):
        self.calls.append((" ".join(query.split()), list(params_seq)))

    def fetchone(self):
        return self.current_rows[0] if self.current_rows else None


class ManualPaperConnection:
    def __init__(self, cursor):
        self.cursor_instance = cursor

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        return None


def install(monkeypatch, cursor):
    @contextmanager
    def fake_get_connection():
        yield ManualPaperConnection(cursor)

    monkeypatch.setattr(database, "_get_connection", fake_get_connection)


def full_fields():
    return {
        "title": "Hand Entered Paper",
        "authors": ["张三", "李四"],
        "abstract": "An abstract",
        "pdf": "https://openreview.net/pdf?id=REALID",
        "venue": "ICCV 2019",
        "keywords": ["蒸馏", "压缩"],
        "published_year": 2019,
    }


# ---------- upsert_manual_paper ----------

def test_upsert_manual_paper_inserts_paper_authors_keywords(monkeypatch):
    cursor = ManualPaperCursor()
    install(monkeypatch, cursor)

    paper = upsert_manual_paper(full_fields())

    insert_sql, insert_params = cursor.calls[0]
    assert "INSERT INTO papers" in insert_sql
    assert "published_year" in insert_sql
    assert insert_params[0].startswith("manual:")
    assert insert_params[7] == 2019
    # No arxiv_papers write anywhere.
    assert not any("arxiv_papers" in sql for sql, _ in cursor.calls)
    # Authors in enumerate order; keywords in given order.
    author_call = next(c for c in cursor.calls if "INSERT INTO authors" in c[0])
    assert author_call[1] == [(paper["id"], "张三", 0), (paper["id"], "李四", 1)]
    keyword_call = next(c for c in cursor.calls if "INSERT INTO keywords" in c[0])
    assert keyword_call[1] == [(paper["id"], "蒸馏"), (paper["id"], "压缩")]
    assert paper["authors"] == ["张三", "李四"]
    assert paper["keywords"] == ["蒸馏", "压缩"]


def test_upsert_manual_paper_keeps_pdf_raw(monkeypatch):
    cursor = ManualPaperCursor()
    install(monkeypatch, cursor)

    fields = full_fields()
    fields["pdf"] = "  https://openreview.net/pdf?id=REALID  "
    paper = upsert_manual_paper(fields)

    # Stripped but NOT rewritten to ?id=manual:xxx.
    assert paper["pdf"] == "https://openreview.net/pdf?id=REALID"
    _, insert_params = cursor.calls[0]
    assert insert_params[4] == "https://openreview.net/pdf?id=REALID"


def test_upsert_manual_paper_without_optional_fields(monkeypatch):
    cursor = ManualPaperCursor()
    install(monkeypatch, cursor)

    paper = upsert_manual_paper({"title": "Only Title"})

    _, insert_params = cursor.calls[0]
    assert insert_params[0].startswith("manual:")
    assert insert_params[1] == "Only Title"
    assert insert_params[4] is None  # pdf
    assert insert_params[5] is None  # venue
    assert insert_params[7] is None  # published_year
    assert paper["authors"] == []
    assert paper["keywords"] == []


# ---------- pdf fallback fixes ----------

def test_paper_pdf_for_output_manual_stays_raw():
    assert database._paper_pdf_for_output("manual:abc123", None) is None
    assert database._paper_pdf_for_output(
        "manual:abc123", "https://example.com/x.pdf"
    ) == "https://example.com/x.pdf"


def test_paper_pdf_for_output_openreview_fallback_kept():
    # Regression guard: non-manual ids keep the OpenReview fallback.
    assert database._paper_pdf_for_output("yjFkeQ2ynQ", None) == (
        "https://openreview.net/pdf?id=yjFkeQ2ynQ"
    )


def test_list_marked_papers_includes_published_year(monkeypatch):
    class RowCursor:
        def __init__(self):
            self.calls = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, query, params=None):
            self.calls.append(" ".join(query.split()))

        def fetchall(self):
            return [{
                "id": "manual:abc", "title": "T", "abstract": None,
                "keywords": [], "pdf": None, "venue": "ICCV 2019",
                "primary_area": None, "published_year": 2019,
                "llm_response": None, "created_at": None, "published_at": None,
                "viewed": False, "liked": False, "favorited": False,
                "categories": [], "authors": [],
                "first_viewed_at": None, "viewed_at": None,
                "liked_at": None, "favorited_at": None,
                "last_opened_at": None, "note": None, "mark_updated_at": None,
            }]

        def fetchone(self):
            return {"total": 1}

    cursor = RowCursor()
    install(monkeypatch, cursor)
    monkeypatch.setattr(database, "_load_keywords_for_papers", lambda papers: (papers, True))

    items, total = database.list_marked_papers("u1", "all", "viewed_at", 0, 12)

    assert total == 1
    assert items[0]["paper"]["published_year"] == 2019
    # manual: paper with no pdf gets None — no fabricated OpenReview URL.
    assert items[0]["paper"]["pdf"] is None
    assert "p.published_year" in cursor.calls[0]


# ---------- migration ----------

def test_published_year_migration_is_idempotent():
    migration = (
        Path(__file__).resolve().parents[1] / "db" / "migrations" / "030_paper_published_year.sql"
    ).read_text(encoding="utf-8")

    assert "ADD COLUMN IF NOT EXISTS published_year INTEGER" in migration


# ---------- endpoint ----------

def test_manual_papers_endpoint_happy_path(monkeypatch):
    calls = {"mark": [], "analysis": []}
    monkeypatch.setattr(
        app_module, "upsert_manual_paper",
        lambda fields: {"id": "manual:abc", "title": fields["title"]},
    )
    monkeypatch.setattr(
        app_module, "set_paper_mark",
        lambda user_id, paper_id, viewed=None: calls["mark"].append((user_id, paper_id, viewed)),
    )
    monkeypatch.setattr(
        app_module, "_schedule_background_analysis",
        lambda paper_id: calls["analysis"].append(paper_id),
    )

    class FakeRequest:
        pass

    result = asyncio.run(app_module.create_manual_paper(
        req=app_module.ManualPaperRequest(title="T"),
        request=FakeRequest(),
    ))

    assert result["paper"]["id"] == "manual:abc"
    # Single-user cache may or may not resolve; the mark call matches whichever.
    if calls["mark"]:
        assert calls["mark"][0][2] is False
    assert calls["analysis"] == ["manual:abc"]


def test_manual_papers_endpoint_db_error_502(monkeypatch):
    def boom(fields):
        raise DatabaseError("db down")

    monkeypatch.setattr(app_module, "upsert_manual_paper", boom)

    class FakeRequest:
        pass

    import fastapi
    with pytest.raises(fastapi.HTTPException) as excinfo:
        asyncio.run(app_module.create_manual_paper(
            req=app_module.ManualPaperRequest(title="T"),
            request=FakeRequest(),
        ))
    assert excinfo.value.status_code == 502


def test_manual_paper_request_validation():
    # Blank title rejected.
    with pytest.raises(Exception):
        app_module.ManualPaperRequest(title="   ")
    # Year bounds.
    with pytest.raises(Exception):
        app_module.ManualPaperRequest(title="T", published_year=1899)
    with pytest.raises(Exception):
        app_module.ManualPaperRequest(title="T", published_year=2101)
    # List caps.
    with pytest.raises(Exception):
        app_module.ManualPaperRequest(title="T", authors=[f"a{i}" for i in range(201)])
    with pytest.raises(Exception):
        app_module.ManualPaperRequest(title="T", keywords=[f"k{i}" for i in range(101)])
    # Stripping + empty-item removal.
    req = app_module.ManualPaperRequest(
        title="  T  ", authors=[" A ", "", "B"], keywords=[],
    )
    assert req.title == "T"
    assert req.authors == ["A", "B"]


def test_manual_papers_route_registered():
    route = next(r for r in app_module.app.routes if r.path == "/manual-papers")
    assert "POST" in route.methods


# ---------- edit mode (paper_id given) ----------

def test_upsert_manual_paper_edit_updates_without_insert(monkeypatch):
    cursor = ManualPaperCursor()
    install(monkeypatch, cursor)

    paper = upsert_manual_paper(full_fields(), "manual:abc123")

    update_sql, update_params = cursor.calls[0]
    assert "UPDATE papers" in update_sql
    assert "INSERT INTO papers" not in update_sql
    for column in ("title", "abstract", "keywords", "pdf", "venue", "primary_area", "published_year"):
        assert column in update_sql
    assert update_params[-1] == "manual:abc123"
    assert paper["id"] == "manual:abc123"
    assert paper["title"] == "Hand Entered Paper"
    # Authors/keywords still rewritten via DELETE + executemany.
    assert any("DELETE FROM authors" in sql for sql, _ in cursor.calls)
    assert any("DELETE FROM keywords" in sql for sql, _ in cursor.calls)


def test_upsert_manual_paper_edit_rejects_non_manual_id(monkeypatch):
    install(monkeypatch, ManualPaperCursor())

    with pytest.raises(ValueError):
        upsert_manual_paper(full_fields(), "arxiv:2401.01234")


def test_upsert_manual_paper_edit_missing_paper(monkeypatch):
    cursor = ManualPaperCursor()
    # Simulate an UPDATE that matches no row.
    cursor.execute = lambda query, params=None: cursor.calls.append(
        (" ".join(query.split()), params)) or setattr(cursor, "current_rows", [])
    install(monkeypatch, cursor)

    with pytest.raises(LookupError):
        upsert_manual_paper(full_fields(), "manual:ghost")


# ---------- PUT endpoint ----------

def test_update_manual_paper_endpoint_happy_path(monkeypatch):
    calls = {"upsert": [], "clear": [], "analysis": []}

    def fake_upsert(fields, paper_id):
        calls["upsert"].append((fields["title"], paper_id))
        return {"id": paper_id, "title": fields["title"]}

    monkeypatch.setattr(app_module, "upsert_manual_paper", fake_upsert)
    monkeypatch.setattr(
        app_module, "clear_paper_analysis_cache",
        lambda paper_id: calls["clear"].append(paper_id),
    )
    monkeypatch.setattr(
        app_module, "_schedule_background_analysis",
        lambda paper_id: calls["analysis"].append(paper_id),
    )

    result = asyncio.run(app_module.update_manual_paper(
        paper_id="manual:abc",
        req=app_module.ManualPaperRequest(title="Edited"),
    ))

    assert result["paper"]["id"] == "manual:abc"
    assert calls["upsert"] == [("Edited", "manual:abc")]
    assert calls["clear"] == ["manual:abc"]
    assert calls["analysis"] == ["manual:abc"]


def test_update_manual_paper_endpoint_rejects_non_manual(monkeypatch):
    import fastapi
    with pytest.raises(fastapi.HTTPException) as excinfo:
        asyncio.run(app_module.update_manual_paper(
            paper_id="arxiv:2401.01234",
            req=app_module.ManualPaperRequest(title="T"),
        ))
    assert excinfo.value.status_code == 400


def test_update_manual_paper_endpoint_missing_paper_404(monkeypatch):
    def missing(fields, paper_id):
        raise LookupError("论文不存在")

    monkeypatch.setattr(app_module, "upsert_manual_paper", missing)

    import fastapi
    with pytest.raises(fastapi.HTTPException) as excinfo:
        asyncio.run(app_module.update_manual_paper(
            paper_id="manual:ghost",
            req=app_module.ManualPaperRequest(title="T"),
        ))
    assert excinfo.value.status_code == 404


def test_update_manual_paper_endpoint_db_error_502(monkeypatch):
    def boom(fields, paper_id):
        raise DatabaseError("db down")

    monkeypatch.setattr(app_module, "upsert_manual_paper", boom)

    import fastapi
    with pytest.raises(fastapi.HTTPException) as excinfo:
        asyncio.run(app_module.update_manual_paper(
            paper_id="manual:abc",
            req=app_module.ManualPaperRequest(title="T"),
        ))
    assert excinfo.value.status_code == 502


def test_update_manual_paper_route_registered():
    route = next(r for r in app_module.app.routes if r.path == "/manual-papers/{paper_id}")
    assert "PUT" in route.methods
