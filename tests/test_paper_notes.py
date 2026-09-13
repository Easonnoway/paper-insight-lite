import asyncio
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import psycopg
import pytest

import app as app_module
import database
from paper_notes import MAX_NOTE_LENGTH, normalize_note, set_paper_note


class NoteCursor:
    """Records SQL; returns scripted rows and can raise on execute."""

    def __init__(self, rows=None, execute_error=None):
        self.rows = rows or []
        self.execute_error = execute_error
        self.calls = []
        self.current_rows = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, query, params=None):
        self.calls.append((" ".join(query.split()), params))
        if self.execute_error is not None:
            raise self.execute_error
        self.current_rows = self.rows

    def fetchone(self):
        return self.current_rows[0] if self.current_rows else None


class NoteConnection:
    def __init__(self, cursor):
        self.cursor_instance = cursor
        self.commits = 0

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1


def install_fake_connection(monkeypatch, connection):
    @contextmanager
    def fake_get_connection():
        yield connection

    monkeypatch.setattr(database, "_get_connection", fake_get_connection)


def mark_row(**overrides):
    row = {
        "paper_id": "synthetic-paper",
        "viewed": True,
        "liked": False,
        "favorited": False,
        "first_viewed_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
        "viewed_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
        "liked_at": None,
        "favorited_at": None,
        "note": "hello note",
        "updated_at": datetime(2026, 9, 2, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


def test_set_paper_note_inserts_viewed_mark_for_new_paper(monkeypatch):
    cursor = NoteCursor(rows=[mark_row()])
    connection = NoteConnection(cursor)
    install_fake_connection(monkeypatch, connection)

    result = set_paper_note("user-1", "synthetic-paper", "hello note")

    assert result["note"] == "hello note"
    assert result["viewed"] is True
    assert result["favorited"] is False
    sql, params = cursor.calls[0]
    assert "INSERT INTO paper_marks" in sql
    assert "ON CONFLICT (user_id, paper_id) DO UPDATE SET" in sql
    assert params == ("user-1", "synthetic-paper", "hello note")
    assert connection.commits == 1


def test_set_paper_note_existing_row_preserves_state(monkeypatch):
    cursor = NoteCursor(rows=[mark_row(liked=True)])
    install_fake_connection(monkeypatch, NoteConnection(cursor))

    set_paper_note("user-1", "synthetic-paper", "updated note")

    sql, params = cursor.calls[0]
    # Only the note is replaced; liked/favorited are untouched.
    assert "note = EXCLUDED.note" in sql
    assert "viewed = paper_marks.viewed OR EXCLUDED.viewed" in sql
    assert "favorited = " not in sql.split("DO UPDATE SET", 1)[1]
    assert params == ("user-1", "synthetic-paper", "updated note")


def test_set_paper_note_empty_clears_without_insert(monkeypatch):
    cursor = NoteCursor(rows=[mark_row(note=None)])
    install_fake_connection(monkeypatch, NoteConnection(cursor))

    result = set_paper_note("user-1", "synthetic-paper", "")

    assert result["note"] is None
    sql, params = cursor.calls[0]
    assert "SET note = NULL" in sql
    assert "INSERT INTO" not in sql
    assert params == ("user-1", "synthetic-paper")


def test_set_paper_note_empty_without_row_is_noop(monkeypatch):
    cursor = NoteCursor(rows=[])
    install_fake_connection(monkeypatch, NoteConnection(cursor))

    result = set_paper_note("user-1", "synthetic-paper", "")

    assert result == {
        "paper_id": "synthetic-paper",
        "viewed": False,
        "liked": False,
        "favorited": False,
        "first_viewed_at": None,
        "viewed_at": None,
        "liked_at": None,
        "favorited_at": None,
        "note": None,
        "updated_at": None,
    }


def test_normalize_note_strips_and_clears():
    assert normalize_note("  hi  ") == "hi"
    assert normalize_note("   ") is None
    assert normalize_note(None) is None


def test_set_paper_note_rejects_overlength():
    with pytest.raises(ValueError):
        set_paper_note("user-1", "synthetic-paper", "x" * (MAX_NOTE_LENGTH + 1))


def test_set_paper_note_missing_paper_maps_to_lookup_error(monkeypatch):
    cursor = NoteCursor(execute_error=psycopg.errors.ForeignKeyViolation(
        "paper missing", None, b"", None, Exception("fk")))
    install_fake_connection(monkeypatch, NoteConnection(cursor))

    with pytest.raises(LookupError):
        set_paper_note("user-1", "ghost-paper", "hello")


def test_update_my_paper_note_endpoint_uses_session_user(monkeypatch):
    calls = []

    def fake_set_paper_note(user_id, paper_id, note):
        calls.append((user_id, paper_id, note))
        return {"ok": True}

    monkeypatch.setattr(app_module, "set_paper_note", fake_set_paper_note)

    result = asyncio.run(app_module.update_my_paper_note(
        paper_id="synthetic-paper",
        req=app_module.PaperNotePayload(note="我的笔记"),
        user={"id": "session-user-id"},
    ))

    assert result == {"ok": True}
    assert calls == [("session-user-id", "synthetic-paper", "我的笔记")]

    operation = app_module.app.openapi()["paths"]["/papers/{paper_id}/note"]["put"]
    body = operation["requestBody"]["content"]["application/json"]["schema"]
    assert body["$ref"].endswith("PaperNotePayload")
    route = next(route for route in app_module.app.routes if route.path == "/papers/{paper_id}/note")
    assert [dependency.call for dependency in route.dependant.dependencies] == [
        app_module.require_current_user
    ]


def test_paper_notes_migration_is_idempotent_and_capped():
    migration = (
        Path(__file__).resolve().parents[1] / "db" / "migrations" / "027_paper_notes.sql"
    ).read_text(encoding="utf-8")

    assert "ADD COLUMN IF NOT EXISTS note TEXT" in migration
    assert "DROP CONSTRAINT IF EXISTS chk_paper_marks_note_length" in migration
    assert "char_length(note) <= 10000" in migration
