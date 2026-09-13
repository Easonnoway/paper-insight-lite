"""Paper category tree storage (Zotero-style, multi-category per paper).

Tables (db/migrations/025_paper_categories.sql):
  paper_categories            adjacency-list tree nodes
  paper_category_assignments  many-to-many paper <-> category

All functions follow the database.py conventions: _run_with_retry wrapping,
DatabaseError on infrastructure failures, ValueError for user-facing
validation problems (surface as 4xx in app.py).
"""

import logging
import uuid

import psycopg

import database
from database import DatabaseError, NoRetryError, _run_with_retry

logger = logging.getLogger(__name__)

MAX_CATEGORY_NAME_LENGTH = 60

# Color keys assigned to categories; order MUST match the backfill array in
# db/migrations/026_category_color.sql (md5-based backfill uses the same order).
CATEGORY_COLOR_PALETTE: tuple[str, ...] = (
    "amber", "blue", "violet", "emerald", "rose", "teal", "orange", "cyan", "pink", "slate",
)


class _CategoryConflict(NoRetryError):
    """Unique violations / cycle moves: surface as ValueError (HTTP 409/400)."""


class _CategoryMissing(NoRetryError):
    """Missing rows: surface as LookupError (HTTP 404)."""


def _normalize_category_row(row: dict) -> dict:
    parent_id = row.get("parent_id")
    return {
        "id": str(row["id"]),
        "parent_id": str(parent_id) if parent_id else None,
        "name": row["name"],
        "position": int(row["position"]),
        "paper_count": int(row.get("paper_count") or 0),
        "color": row.get("color"),
        "created_at": row.get("created_at"),
    }


def _normalize_name(name: str | None) -> str:
    normalized = (name or "").strip()
    return normalized[:MAX_CATEGORY_NAME_LENGTH]


def _is_unique_violation(exc: Exception) -> bool:
    return isinstance(exc, psycopg.errors.UniqueViolation)


def list_paper_categories(user_id: str) -> dict:
    """All category nodes (with direct paper counts) + the uncategorized count.

    Subtree aggregation is done client-side (dozens of nodes at most).
    "Uncategorized" = marked papers with no assignment at all, using the same
    all/viewed/liked/favorited union semantics as the My Papers list so the
    tree's "全部" row matches the list's total.
    """

    def operation() -> dict:
        with database._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT pc.id, pc.parent_id, pc.name, pc.position, pc.color, pc.created_at,
                           COUNT(DISTINCT pca.paper_id) AS paper_count
                    FROM paper_categories pc
                    LEFT JOIN paper_category_assignments pca ON pca.category_id = pc.id
                    WHERE pc.user_id = %s
                    GROUP BY pc.id
                    ORDER BY pc.parent_id NULLS FIRST, pc.position, pc.name
                    """,
                    (user_id,),
                )
                rows = cur.fetchall()

                cur.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM paper_marks pm
                    WHERE pm.user_id = %s
                      AND NOT EXISTS (
                          SELECT 1 FROM paper_category_assignments pca
                          WHERE pca.user_id = pm.user_id AND pca.paper_id = pm.paper_id
                      )
                    """,
                    (user_id,),
                )
                uncategorized = int((cur.fetchone() or {}).get("total") or 0)

        return {
            "categories": [_normalize_category_row(row) for row in rows],
            "uncategorized_count": uncategorized,
        }

    return _run_with_retry(operation, f"list_paper_categories:{user_id}")


def get_paper_category_ids(user_id: str) -> list[str]:
    """All category ids owned by the user (for ownership checks)."""

    def operation() -> list[str]:
        with database._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM paper_categories WHERE user_id = %s",
                    (user_id,),
                )
                return [str(row["id"]) for row in cur.fetchall()]

    return _run_with_retry(operation, f"get_paper_category_ids:{user_id}")


def create_paper_category(user_id: str, name: str, parent_id: str | None) -> dict:
    """Create a category under parent_id (NULL = top level)."""
    normalized = _normalize_name(name)
    if not normalized:
        raise ValueError("分类名不能为空")
    if parent_id is not None and parent_id not in get_paper_category_ids(user_id):
        raise LookupError("父分类不存在")

    def operation() -> dict:
        with database._get_connection() as conn:
            with conn.cursor() as cur:
                try:
                    # Deterministic round-robin color: palette slot by how many
                    # categories the user already has.
                    cur.execute(
                        "SELECT COUNT(*) AS total FROM paper_categories WHERE user_id = %s",
                        (user_id,),
                    )
                    total = int((cur.fetchone() or {}).get("total") or 0)
                    color = CATEGORY_COLOR_PALETTE[total % len(CATEGORY_COLOR_PALETTE)]
                    cur.execute(
                        """
                        INSERT INTO paper_categories (user_id, parent_id, name, position, color)
                        SELECT %s, %s, %s,
                               COALESCE(
                                   (SELECT MAX(position) + 1 FROM paper_categories
                                    WHERE user_id = %s
                                      AND parent_id IS NOT DISTINCT FROM %s),
                                   0
                               ),
                               %s
                        RETURNING id, parent_id, name, position, color, created_at
                        """,
                        (user_id, parent_id, normalized, user_id, parent_id, color),
                    )
                    row = cur.fetchone()
                except psycopg.errors.UniqueViolation as exc:
                    # Re-raise outside the retry loop as a user-facing error.
                    raise _CategoryConflict("同级已有同名分类") from exc
            conn.commit()
        return _normalize_category_row({**row, "paper_count": 0})

    try:
        return _run_with_retry(operation, f"create_paper_category:{user_id}")
    except _CategoryConflict as exc:
        raise ValueError(str(exc)) from exc.__cause__


def _category_exists(cur: psycopg.Cursor, user_id: str, category_id: str) -> bool:
    cur.execute(
        "SELECT 1 FROM paper_categories WHERE user_id = %s AND id = %s",
        (user_id, category_id),
    )
    return cur.fetchone() is not None


def rename_paper_category(user_id: str, category_id: str, name: str) -> dict:
    normalized = _normalize_name(name)
    if not normalized:
        raise ValueError("分类名不能为空")

    def operation() -> dict:
        with database._get_connection() as conn:
            with conn.cursor() as cur:
                if not _category_exists(cur, user_id, category_id):
                    raise _CategoryMissing("分类不存在")
                try:
                    cur.execute(
                        """
                        UPDATE paper_categories SET name = %s, updated_at = NOW()
                        WHERE user_id = %s AND id = %s
                        RETURNING id, parent_id, name, position, color, created_at
                        """,
                        (normalized, user_id, category_id),
                    )
                    row = cur.fetchone()
                except psycopg.errors.UniqueViolation as exc:
                    raise _CategoryConflict("同级已有同名分类") from exc
            conn.commit()
        return _normalize_category_row({**row, "paper_count": 0})

    try:
        return _run_with_retry(operation, f"rename_paper_category:{user_id}")
    except _CategoryMissing as exc:
        raise LookupError(str(exc)) from None
    except _CategoryConflict as exc:
        raise ValueError(str(exc)) from exc.__cause__


def move_paper_category(user_id: str, category_id: str, new_parent_id: str | None) -> dict:
    """Re-parent a category. Cycles are rejected (Python-side walk up the tree)."""
    if new_parent_id == category_id:
        raise ValueError("不能移动到自己下面")

    def operation() -> dict:
        with database._get_connection() as conn:
            with conn.cursor() as cur:
                if not _category_exists(cur, user_id, category_id):
                    raise _CategoryMissing("分类不存在")
                if new_parent_id is not None:
                    if not _category_exists(cur, user_id, new_parent_id):
                        raise _CategoryMissing("目标父分类不存在")
                    # Walk up from the new parent; hitting ourselves means a
                    # cycle. UUID columns come back as uuid.UUID objects, so
                    # normalize to str before comparing.
                    ancestor: str | None = new_parent_id
                    while ancestor is not None:
                        if ancestor == category_id:
                            raise _CategoryConflict("不能移动到自己的子分类下")
                        cur.execute(
                            "SELECT parent_id FROM paper_categories WHERE id = %s",
                            (ancestor,),
                        )
                        row = cur.fetchone()
                        if row is None:
                            break
                        ancestor = str(row["parent_id"]) if row["parent_id"] else None

                try:
                    cur.execute(
                        """
                        UPDATE paper_categories
                        SET parent_id = %s,
                            position = COALESCE(
                                (SELECT MAX(position) + 1 FROM paper_categories
                                 WHERE user_id = %s
                                   AND parent_id IS NOT DISTINCT FROM %s),
                                0
                            ),
                            updated_at = NOW()
                        WHERE user_id = %s AND id = %s
                        RETURNING id, parent_id, name, position, color, created_at
                        """,
                        (new_parent_id, user_id, new_parent_id, user_id, category_id),
                    )
                    row = cur.fetchone()
                except psycopg.errors.UniqueViolation as exc:
                    raise _CategoryConflict("同级已有同名分类") from exc
            conn.commit()
        return _normalize_category_row({**row, "paper_count": 0})

    try:
        return _run_with_retry(operation, f"move_paper_category:{user_id}")
    except _CategoryMissing as exc:
        raise LookupError(str(exc)) from None
    except _CategoryConflict as exc:
        raise ValueError(str(exc)) from exc.__cause__ or None


def set_paper_category_color(user_id: str, category_id: str, color: str | None) -> dict:
    """Set (or clear with None) a category's color."""
    if color is not None and color not in CATEGORY_COLOR_PALETTE:
        raise ValueError("不支持的颜色")

    def operation() -> dict:
        with database._get_connection() as conn:
            with conn.cursor() as cur:
                if not _category_exists(cur, user_id, category_id):
                    raise _CategoryMissing("分类不存在")
                cur.execute(
                    """
                    UPDATE paper_categories SET color = %s, updated_at = NOW()
                    WHERE user_id = %s AND id = %s
                    RETURNING id, parent_id, name, position, color, created_at
                    """,
                    (color, user_id, category_id),
                )
                row = cur.fetchone()
            conn.commit()
        return _normalize_category_row({**row, "paper_count": 0})

    try:
        return _run_with_retry(operation, f"set_paper_category_color:{user_id}")
    except _CategoryMissing as exc:
        raise LookupError(str(exc)) from None


def delete_paper_category(user_id: str, category_id: str) -> int:
    """Delete a category and its subtree (CASCADE). Papers are never deleted;
    they simply lose those assignments. Returns the number of subcategory
    nodes removed (excluding the root itself) for the UI confirmation text.
    """

    def operation() -> int:
        with database._get_connection() as conn:
            with conn.cursor() as cur:
                if not _category_exists(cur, user_id, category_id):
                    raise _CategoryMissing("分类不存在")
                cur.execute(
                    """
                    WITH RECURSIVE subtree AS (
                        SELECT id FROM paper_categories WHERE user_id = %s AND id = %s
                        UNION ALL
                        SELECT c.id FROM paper_categories c
                        JOIN subtree s ON c.parent_id = s.id
                    )
                    SELECT COUNT(*) AS total FROM subtree
                    """,
                    (user_id, category_id),
                )
                subtree_total = int((cur.fetchone() or {}).get("total") or 0)
                cur.execute(
                    "DELETE FROM paper_categories WHERE user_id = %s AND id = %s",
                    (user_id, category_id),
                )
            conn.commit()
        return max(subtree_total - 1, 0)

    try:
        return _run_with_retry(operation, f"delete_paper_category:{user_id}")
    except _CategoryMissing as exc:
        raise LookupError(str(exc)) from None


def assign_paper_category(user_id: str, paper_id: str, category_id: str) -> None:
    """Attach a paper to a category (idempotent), including every ancestor.

    Assigning to a nested category implicitly assigns the whole ancestor
    chain, so the paper also shows under the parent categories (Zotero-style
    "descendants imply ancestors" semantics). Unassign stays leaf-only.
    """
    if category_id not in get_paper_category_ids(user_id):
        raise LookupError("分类不存在")

    def operation() -> None:
        with database._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO paper_category_assignments (user_id, paper_id, category_id)
                    WITH RECURSIVE chain AS (
                        SELECT id, parent_id FROM paper_categories
                        WHERE user_id = %s AND id = %s
                        UNION ALL
                        SELECT c.id, c.parent_id
                        FROM paper_categories c JOIN chain ON c.id = chain.parent_id
                    )
                    SELECT %s, %s, id FROM chain
                    ON CONFLICT DO NOTHING
                    """,
                    (user_id, category_id, user_id, paper_id),
                )
            conn.commit()

    _run_with_retry(operation, f"assign_paper_category:{user_id}:{paper_id}")


def unassign_paper_category(user_id: str, paper_id: str, category_id: str) -> None:
    """Detach a paper from a category (idempotent)."""

    def operation() -> None:
        with database._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM paper_category_assignments
                    WHERE user_id = %s AND paper_id = %s AND category_id = %s
                    """,
                    (user_id, paper_id, category_id),
                )
            conn.commit()

    _run_with_retry(operation, f"unassign_paper_category:{user_id}:{paper_id}")


def clear_paper_categories(user_id: str, paper_id: str) -> None:
    """Remove every category assignment of a paper (drop on 未分类)."""

    def operation() -> None:
        with database._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM paper_category_assignments WHERE user_id = %s AND paper_id = %s",
                    (user_id, paper_id),
                )
            conn.commit()

    _run_with_retry(operation, f"clear_paper_categories:{user_id}:{paper_id}")


def clear_user_paper_categories(user_id: str) -> None:
    """Remove ALL of a user's assignments (full AI re-categorize preamble)."""

    def operation() -> None:
        with database._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM paper_category_assignments WHERE user_id = %s",
                    (user_id,),
                )
            conn.commit()

    _run_with_retry(operation, f"clear_user_paper_categories:{user_id}")


def validate_uuid(value: str) -> str | None:
    """Canonicalize a UUID string, or None if invalid."""
    try:
        return str(uuid.UUID(value))
    except (TypeError, ValueError):
        return None
