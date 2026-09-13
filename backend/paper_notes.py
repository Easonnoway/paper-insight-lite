"""Per-paper user notes (one note per paper per user).

Storage: paper_marks.note (db/migrations/027_paper_notes.sql). Reading paths
need no new code — list_marked_papers / get_paper_marks / get_export_snapshot
already return the column. This module only owns the write path.

All functions follow the database.py conventions (see paper_categories.py):
_run_with_retry wrapping, DatabaseError on infrastructure failures, ValueError
for user-facing validation problems (app.py -> 4xx), LookupError for missing
papers (app.py -> 404).

Semantics note: writing a non-empty note to a paper without a mark row creates
one with viewed=TRUE, so the noted paper shows up in My Papers. This matches
library_transfer.normalize_mark's invariant that any user-curated paper must
be visible; clearing a note never creates a row.
"""

import psycopg

import database
from database import NoRetryError, _run_with_retry

MAX_NOTE_LENGTH = 10_000

_MARK_RETURNING = """
RETURNING paper_id, viewed, liked, favorited,
          first_viewed_at, viewed_at, liked_at, favorited_at, note, updated_at
"""


class _PaperMissing(NoRetryError):
    """papers FK violation: surface as LookupError (HTTP 404)."""


def normalize_note(note: str | None) -> str | None:
    """strip -> empty means None (clearing). Over-length raises ValueError."""
    normalized = (note or "").strip()
    if not normalized:
        return None
    if len(normalized) > MAX_NOTE_LENGTH:
        raise ValueError(f"笔记最长 {MAX_NOTE_LENGTH} 字")
    return normalized


def _empty_mark(paper_id: str) -> dict:
    return {
        "paper_id": paper_id,
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


def _normalize_mark_row(row: dict) -> dict:
    return {
        "paper_id": row["paper_id"],
        "viewed": bool(row["viewed"]),
        "liked": bool(row["liked"]),
        "favorited": bool(row["favorited"]),
        "first_viewed_at": row.get("first_viewed_at"),
        "viewed_at": row.get("viewed_at"),
        "liked_at": row.get("liked_at"),
        "favorited_at": row.get("favorited_at"),
        "note": row.get("note"),
        "updated_at": row.get("updated_at"),
    }


def set_paper_note(user_id: str, paper_id: str, note: str | None) -> dict:
    """Upsert the user's note for a paper; returns the full mark dict.

    - note non-empty: upsert the mark row, keeping viewed/liked/favorited
      intact (an all-false existing row gains viewed=TRUE so the paper stays
      visible in My Papers).
    - note empty/None: clear it; without an existing row this is a no-op
      returning an empty-state dict (no mark row is created).
    - a paper_id violating the papers FK raises LookupError.
    """
    note = normalize_note(note)

    def operation() -> dict:
        try:
            with database._get_connection() as conn:
                with conn.cursor() as cur:
                    if note is None:
                        cur.execute(
                            f"""
                            UPDATE paper_marks
                            SET note = NULL, updated_at = NOW()
                            WHERE user_id = %s AND paper_id = %s
                            {_MARK_RETURNING}
                            """,
                            (user_id, paper_id),
                        )
                        row = cur.fetchone()
                        conn.commit()
                        return _normalize_mark_row(row) if row else _empty_mark(paper_id)

                    cur.execute(
                        f"""
                        INSERT INTO paper_marks (
                            user_id, paper_id, viewed, viewed_at, first_viewed_at,
                            note, created_at, updated_at
                        )
                        VALUES (%s, %s, TRUE, NOW(), NOW(), %s, NOW(), NOW())
                        ON CONFLICT (user_id, paper_id) DO UPDATE SET
                            note = EXCLUDED.note,
                            viewed = paper_marks.viewed OR EXCLUDED.viewed,
                            viewed_at = COALESCE(paper_marks.viewed_at, EXCLUDED.viewed_at),
                            first_viewed_at = COALESCE(paper_marks.first_viewed_at, EXCLUDED.first_viewed_at),
                            updated_at = NOW()
                        {_MARK_RETURNING}
                        """,
                        (user_id, paper_id, note),
                    )
                    row = cur.fetchone()
                    conn.commit()
                    return _normalize_mark_row(row)
        except psycopg.errors.ForeignKeyViolation as exc:
            raise _PaperMissing from exc

    try:
        return _run_with_retry(operation, f"set_paper_note:{user_id}:{paper_id}")
    except _PaperMissing as exc:
        raise LookupError("论文不存在") from exc


__all__ = ["MAX_NOTE_LENGTH", "normalize_note", "set_paper_note"]
