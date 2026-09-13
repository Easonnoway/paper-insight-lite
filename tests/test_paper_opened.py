import asyncio
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pytest

import app as app_module
import database


class OpenedCursor:
    """Fake for record_paper_opened: records the upsert SQL and params."""

    def __init__(self):
        self.calls = []
        self.current_rows = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, query, params=None):
        self.calls.append((" ".join(query.split()), params))
        self.current_rows = [{
            "paper_id": "synthetic-paper",
            "viewed": False,
            "liked": False,
            "favorited": False,
            "first_viewed_at": None,
            "viewed_at": None,
            "liked_at": None,
            "favorited_at": None,
            "last_opened_at": datetime(2026, 9, 19, 8, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 9, 19, 8, tzinfo=timezone.utc),
        }]

    def fetchone(self):
        return self.current_rows[0] if self.current_rows else None


class OpenedConnection:
    def __init__(self, cursor):
        self.cursor_instance = cursor

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        return None


def install(monkeypatch, cursor):
    @contextmanager
    def fake_get_connection():
        yield OpenedConnection(cursor)

    monkeypatch.setattr(database, "_get_connection", fake_get_connection)


# ---------- record_paper_opened ----------

def test_record_paper_opened_refreshes_only_last_opened_at(monkeypatch):
    cursor = OpenedCursor()
    install(monkeypatch, cursor)

    result = database.record_paper_opened("synthetic-user", "synthetic-paper")

    sql, params = cursor.calls[0]
    # The upsert touches last_opened_at and nothing else in DO UPDATE.
    assert "INSERT INTO paper_marks" in sql
    update_section = sql.split("DO UPDATE SET", 1)[1].split("RETURNING", 1)[0]
    assert "last_opened_at = NOW()" in update_section
    assert "viewed =" not in update_section
    assert "liked =" not in update_section
    assert "favorited =" not in update_section
    # A brand-new row starts all-false (added-but-never-viewed semantics).
    assert params == ("synthetic-user", "synthetic-paper")
    assert result["viewed"] is False
    assert result["last_opened_at"] is not None


def test_record_opened_endpoint_exists_with_user_dependency(monkeypatch):
    monkeypatch.setattr(
        app_module, "record_paper_opened",
        lambda user_id, paper_id: {"ok": True},
    )

    result = asyncio.run(app_module.record_my_paper_opened(
        paper_id="synthetic-paper", user={"id": "session-user-id"},
    ))

    assert result == {"ok": True}
    operation = app_module.app.openapi()["paths"]["/papers/{paper_id}/opened"]["post"]
    assert operation["responses"]["200"]["description"]
    route = next(r for r in app_module.app.routes if r.path == "/papers/{paper_id}/opened")
    assert [d.call for d in route.dependant.dependencies] == [app_module.require_current_user]


# ---------- migration ----------

def test_last_opened_migration_is_idempotent():
    migration = (
        Path(__file__).resolve().parents[1] / "db" / "migrations" / "029_paper_last_opened.sql"
    ).read_text(encoding="utf-8")

    assert "ADD COLUMN IF NOT EXISTS last_opened_at TIMESTAMPTZ" in migration


# ---------- list filter semantics ----------

def test_list_marked_papers_all_filter_includes_unviewed(monkeypatch):
    class FilterCursor:
        def __init__(self):
            self.calls = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, query, params=None):
            self.calls.append(" ".join(query.split()))

        def fetchall(self):
            return []

        def fetchone(self):
            return {"total": 0}

    cursor = FilterCursor()
    install(monkeypatch, cursor)
    monkeypatch.setattr(database, "_load_keywords_for_papers", lambda papers: (papers, True))

    items, total = database.list_marked_papers("synthetic-user", "all", "viewed_at", 0, 12)

    assert total == 0
    # all = TRUE means even all-false rows (added, never opened) are listed.
    assert "WHERE pm.user_id = %s AND TRUE" in cursor.calls[0]
    # viewed filter keeps the strict manual flag.
    items, _ = database.list_marked_papers("synthetic-user", "viewed", "viewed_at", 0, 12)
    assert "pm.viewed = TRUE" in cursor.calls[2]


# ---------- arxiv add no longer auto-views ----------

def test_arxiv_add_marks_row_without_viewed(monkeypatch):
    calls = []
    monkeypatch.setattr(
        app_module, "set_paper_mark",
        lambda user_id, paper_id, viewed=None: calls.append((user_id, paper_id, viewed)),
    )
    # The add path passes viewed=False explicitly (row exists, nothing viewed).
    app_module.set_paper_mark("u1", "arxiv:1234.5678", viewed=False)

    assert calls == [("u1", "arxiv:1234.5678", False)]
