import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import database  # noqa: E402
from paper_categories import (  # noqa: E402
    assign_paper_category,
    unassign_paper_category,
)


class RecordingCursor:
    def __init__(self, owned_ids):
        self.owned_ids = owned_ids
        self.executed = []
        self.current_rows = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, query, params=None):
        self.executed.append((" ".join(query.split()), params))
        compact = self.executed[-1][0]
        if "FROM paper_categories" in compact and "INSERT" not in compact:
            self.current_rows = [{"id": i} for i in self.owned_ids]
        else:
            self.current_rows = []

    def fetchall(self):
        return list(self.current_rows)


class RecordingConnection:
    def __init__(self, cursor):
        self.cursor_instance = cursor
        self.commits = 0

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1


def install(monkeypatch, cursor):
    connection = RecordingConnection(cursor)

    @contextmanager
    def fake_get_connection():
        yield connection

    monkeypatch.setattr(database, "_get_connection", fake_get_connection)
    return connection


def test_assign_paper_category_inserts_whole_ancestor_chain(monkeypatch):
    cursor = RecordingCursor(owned_ids=["cat-child"])
    connection = install(monkeypatch, cursor)

    assign_paper_category("user-1", "paper-1", "cat-child")

    insert = next(sql for sql, _ in cursor.executed if "INSERT INTO paper_category_assignments" in sql)
    # The chain CTE walks from the target category up to the root.
    assert "WITH RECURSIVE chain" in insert
    assert "JOIN chain ON c.id = chain.parent_id" in insert
    assert connection.commits == 1


def test_assign_paper_category_unknown_category_raises(monkeypatch):
    cursor = RecordingCursor(owned_ids=[])
    install(monkeypatch, cursor)

    try:
        assign_paper_category("user-1", "paper-1", "ghost")
        raise AssertionError("expected LookupError")
    except LookupError as exc:
        assert "分类不存在" in str(exc)


def test_unassign_paper_category_deletes_only_the_leaf(monkeypatch):
    cursor = RecordingCursor(owned_ids=["cat-child"])
    install(monkeypatch, cursor)

    unassign_paper_category("user-1", "paper-1", "cat-child")

    delete_sql, params = cursor.executed[0]
    # Removing a child keeps the ancestors: a plain leaf DELETE, no chain.
    assert "WITH RECURSIVE" not in delete_sql
    assert params == ("user-1", "paper-1", "cat-child")
