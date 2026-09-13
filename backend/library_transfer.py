"""Library export/import (share) for the single-user paper library.

Export: serialize marked papers (+ category tree, marks, AI analysis, Chinese
abstracts) into a portable JSON payload.
Import: strictly validate a payload, preview it against the local library,
then merge selected papers in a single transaction:

- papers already present locally keep their marks untouched; only missing
  category assignments are added, and empty llm_response/abstract_zh fields
  are filled from the file;
- new papers are inserted wholesale with the timestamps from the file;
- file categories are merged into the local tree by (parent, normalized name)
  and re-mapped to local category ids.

All writes go through one connection inside _run_with_retry so a failure rolls
back everything and a retry re-reads local state (idempotent).
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

import database
from arxiv import build_arxiv_abs_url, build_arxiv_pdf_url
from database import _run_with_retry

EXPORT_FORMAT = "paper-insight-lite.export"
EXPORT_VERSION = 1
MAX_PAPERS = 2000
MAX_CATEGORIES = 1000
MAX_TITLE_LENGTH = 2000
MAX_ABSTRACT_LENGTH = 200_000
MAX_LLM_RESPONSE_LENGTH = 512_000
MAX_NOTE_LENGTH = 10_000
MAX_CATEGORY_NAME_LENGTH = 60
MAX_CATEGORY_TREE_DEPTH = 50
MAX_FILE_BYTES = 64 * 1024 * 1024


class TransferError(ValueError):
    """User-facing payload problem (structure, size, or reference integrity)."""


class PayloadTooLargeError(TransferError):
    """Payload exceeds the hard size limits."""


class ExportCategory(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1, max_length=64)
    parent_id: str | None = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=MAX_CATEGORY_NAME_LENGTH + 100)
    position: int = Field(default=0, ge=0, le=1_000_000)
    color: str | None = Field(default=None, max_length=20)


class ExportMark(BaseModel):
    model_config = ConfigDict(extra="ignore")

    viewed: bool = False
    liked: bool = False
    favorited: bool = False
    first_viewed_at: datetime | None = None
    viewed_at: datetime | None = None
    liked_at: datetime | None = None
    favorited_at: datetime | None = None
    last_opened_at: datetime | None = None

    @field_validator(
        "first_viewed_at", "viewed_at", "liked_at", "favorited_at", "last_opened_at",
        mode="before",
    )
    @classmethod
    def _tolerate_bad_timestamp(cls, value: Any) -> Any:
        # A broken timestamp loses fidelity but not structure — downgrade to
        # None instead of rejecting the whole file.
        if value in (None, ""):
            return None
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        return value


class ExportArxivMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")

    arxiv_id: str = Field(min_length=1, max_length=64)
    arxiv_url: str | None = Field(default=None, max_length=2000)
    pdf_url: str | None = Field(default=None, max_length=2000)
    published_at: datetime | None = None
    updated_at: datetime | None = None


class ExportPaper(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1, max_length=200)
    title: str | None = Field(default=None, max_length=MAX_TITLE_LENGTH)
    abstract: str | None = Field(default=None, max_length=MAX_ABSTRACT_LENGTH)
    keywords: list[str] = Field(default_factory=list, max_length=100)
    authors: list[str] = Field(default_factory=list, max_length=2000)
    pdf: str | None = Field(default=None, max_length=2000)
    venue: str | None = Field(default=None, max_length=500)
    primary_area: str | None = Field(default=None, max_length=500)
    arxiv: ExportArxivMeta | None = None
    llm_response: str | None = Field(default=None, max_length=MAX_LLM_RESPONSE_LENGTH)
    abstract_zh: str | None = Field(default=None, max_length=MAX_ABSTRACT_LENGTH)
    note: str | None = Field(default=None, max_length=MAX_NOTE_LENGTH)
    created_at: datetime | None = None
    mark: ExportMark = Field(default_factory=ExportMark)
    category_ids: list[str] = Field(default_factory=list, max_length=MAX_CATEGORIES)

    @field_validator("created_at", mode="before")
    @classmethod
    def _tolerate_bad_created_at(cls, value: Any) -> Any:
        if value in (None, ""):
            return None
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        return value

    @field_validator("keywords", "authors", mode="before")
    @classmethod
    def _stringify_items(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [str(item) for item in value if item is not None]
        return value


class ExportPayloadV1(BaseModel):
    model_config = ConfigDict(extra="ignore")

    format: Literal["paper-insight-lite.export"]
    version: Literal[1]
    exported_at: datetime | None = None
    categories: list[ExportCategory] = Field(default_factory=list, max_length=MAX_CATEGORIES)
    papers: list[ExportPaper] = Field(default_factory=list, max_length=MAX_PAPERS)

    @field_validator("exported_at", mode="before")
    @classmethod
    def _tolerate_bad_exported_at(cls, value: Any) -> Any:
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        return value


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def _normalize_category_name(name: str | None) -> str:
    return (name or "").strip()[:MAX_CATEGORY_NAME_LENGTH]


def parse_raw_file_size(raw_bytes: int) -> None:
    if raw_bytes > MAX_FILE_BYTES:
        raise PayloadTooLargeError("文件超过 64MB 上限")


def validate_payload(raw: Any) -> ExportPayloadV1:
    """Strict structural validation. Raises TransferError with a Chinese message."""
    if not isinstance(raw, dict):
        raise TransferError("文件内容不是有效的 JSON 对象")

    if raw.get("format") != EXPORT_FORMAT:
        raise TransferError("文件格式不正确：不是 Paper Insight Lite 导出文件")
    if raw.get("version") != EXPORT_VERSION:
        version = raw.get("version")
        if isinstance(version, int) and version > EXPORT_VERSION:
            raise TransferError("文件版本过新，请升级应用后再导入")
        raise TransferError("文件版本不受支持")

    try:
        payload = ExportPayloadV1.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(part) for part in first.get("loc", [])) or "未知字段"
        raise TransferError(f"文件结构不合法（{loc}: {first.get('msg', '')}）") from exc

    seen_category_ids: set[str] = set()
    category_by_id = {}
    for category in payload.categories:
        if category.id in seen_category_ids:
            raise TransferError(f"文件中存在重复的分类 ID：{category.id}")
        seen_category_ids.add(category.id)
        category_by_id[category.id] = category
    for category in payload.categories:
        if category.parent_id is not None and category.parent_id not in category_by_id:
            raise TransferError(f"分类「{category.name}」的父分类不在文件中")

    # Cycle detection via parent-chain walk with a visited set.
    for category in payload.categories:
        visited: set[str] = set()
        current = category
        depth = 0
        while current.parent_id is not None:
            if current.id in visited:
                raise TransferError(f"分类树存在循环引用（{current.name}）")
            visited.add(current.id)
            depth += 1
            if depth > MAX_CATEGORY_TREE_DEPTH:
                raise TransferError(f"分类层级过深（超过 {MAX_CATEGORY_TREE_DEPTH} 层）")
            current = category_by_id[current.parent_id]

    seen_paper_ids: set[str] = set()
    seen_arxiv_ids: set[str] = set()
    valid_category_ids = seen_category_ids
    for paper in payload.papers:
        if paper.id in seen_paper_ids:
            raise TransferError(f"文件中存在重复的论文 ID：{paper.id}")
        seen_paper_ids.add(paper.id)
        if paper.arxiv is not None:
            if paper.arxiv.arxiv_id in seen_arxiv_ids:
                raise TransferError(f"文件中存在重复的 arXiv ID：{paper.arxiv.arxiv_id}")
            seen_arxiv_ids.add(paper.arxiv.arxiv_id)
        unknown_ids = set(paper.category_ids) - valid_category_ids
        if unknown_ids:
            raise TransferError(f"论文「{paper.title or paper.id}」引用了文件中不存在的分类")

    return payload


def normalize_mark(mark: ExportMark, exported_at: datetime | None) -> dict:
    """Enforce mark invariants and fill missing viewed_at.

    liked/favorited imply viewed; an all-false mark becomes viewed=true
    (an imported paper must be visible in My Papers). Timestamp fallback
    chain for viewed_at: first_viewed_at -> exported_at -> NOW() is applied
    by the SQL layer (COALESCE), here we only carry the file values.
    """
    viewed = bool(mark.viewed or mark.liked or mark.favorited)
    first_viewed_at = mark.first_viewed_at
    viewed_at = mark.viewed_at or first_viewed_at or exported_at
    liked_at = mark.liked_at if (viewed and mark.liked) else None
    favorited_at = mark.favorited_at if (viewed and mark.favorited) else None
    return {
        "viewed": True,
        "liked": bool(viewed and mark.liked),
        "favorited": bool(viewed and mark.favorited),
        "first_viewed_at": first_viewed_at or viewed_at,
        "viewed_at": viewed_at,
        "liked_at": liked_at,
        "favorited_at": favorited_at,
        "last_opened_at": mark.last_opened_at,
    }


def plan_categories(
    file_categories: list[ExportCategory],
    referenced_export_ids: set[str],
    local_rows: list[dict],
) -> dict:
    """Plan category re-mapping for import.

    Only categories referenced by the selected papers (plus their ancestor
    chains) are created or matched. Matching key is (local parent id, exact
    normalized name) — case-sensitive, mirroring the DB UNIQUE index.

    Returns {
      creates: [{parent_id, name, position, export_id}],  # topological order
      export_to_local: {export_id: local_id},             # reused only
      reused_count, created_count,
    }
    """
    file_by_id = {c.id: c for c in file_categories}

    # Collect the referenced set expanded with ancestors.
    needed: set[str] = set()
    for export_id in referenced_export_ids:
        current_id = export_id
        while current_id is not None and current_id in file_by_id and current_id not in needed:
            needed.add(current_id)
            current_id = file_by_id[current_id].parent_id

    # Local lookup: (parent_id_or_None, normalized_name) -> local id.
    local_by_key: dict[tuple[str | None, str], str] = {}
    for row in local_rows:
        key = (row.get("parent_id"), _normalize_category_name(row.get("name")))
        local_by_key.setdefault(key, str(row["id"]))

    export_to_local: dict[str, str] = {}
    reused_count = 0
    creates: list[dict] = []

    def resolve(export_id: str) -> str:
        if export_id in export_to_local:
            return export_to_local[export_id]
        node = file_by_id[export_id]
        parent_local = resolve(node.parent_id) if node.parent_id else None
        name = _normalize_category_name(node.name)
        key = (parent_local, name)
        local_id = local_by_key.get(key)
        if local_id:
            export_to_local[export_id] = local_id
            return local_id
        # Schedule creation (recursion guarantees parents resolve first).
        creates.append({
            "export_id": export_id,
            "parent_id": parent_local,
            "name": name,
            "color": node.color,
        })
        # Placeholder so sibling recursion doesn't duplicate the create.
        placeholder = f"new:{export_id}"
        export_to_local[export_id] = placeholder
        return placeholder

    for export_id in sorted(needed):
        resolve(export_id)

    # Expand placeholders: resolve() already ordered creates parents-first.
    # used_positions tracks the position counter per parent for new nodes.
    used_positions: dict[str | None, int] = {}
    for row in local_rows:
        parent_key = row.get("parent_id")
        used_positions[parent_key] = max(
            used_positions.get(parent_key, -1), int(row.get("position") or 0),
        )
    resolved_creates: list[dict] = []
    for create in creates:
        parent_key = create["parent_id"]
        next_position = used_positions.get(parent_key, -1) + 1
        used_positions[parent_key] = next_position
        resolved_creates.append({**create, "position": next_position})

    reused_count = len(needed) - len(resolved_creates)
    return {
        "creates": resolved_creates,
        "export_to_local": export_to_local,
        "reused_count": reused_count,
        "created_count": len(resolved_creates),
    }


def build_export_payload(user_id: str, paper_ids: list[str] | None = None) -> dict:
    """Build the full export payload (v1) for the user's marked papers."""
    snapshot = database.get_export_snapshot(user_id, paper_ids)

    local_category_ids: set[str] = set()
    for paper in snapshot:
        local_category_ids.update(paper.get("category_ids") or [])
    categories = _fetch_categories_by_ids(local_category_ids) if local_category_ids else []

    papers: list[dict] = []
    for paper in snapshot:
        arxiv_meta = paper.get("arxiv")
        papers.append({
            "id": paper["id"],
            "title": paper.get("title"),
            "abstract": paper.get("abstract"),
            "keywords": paper.get("keywords") or [],
            "authors": paper.get("authors") or [],
            "pdf": paper.get("pdf"),
            "venue": paper.get("venue"),
            "primary_area": paper.get("primary_area"),
            "arxiv": (
                {
                    "arxiv_id": arxiv_meta.get("arxiv_id"),
                    "arxiv_url": arxiv_meta.get("arxiv_url"),
                    "pdf_url": arxiv_meta.get("pdf_url"),
                    "published_at": _iso(arxiv_meta.get("published_at")),
                    "updated_at": _iso(arxiv_meta.get("updated_at")),
                }
                if arxiv_meta and arxiv_meta.get("arxiv_id") else None
            ),
            "llm_response": paper.get("llm_response"),
            "abstract_zh": paper.get("abstract_zh"),
            "note": paper.get("note"),
            "created_at": _iso(paper.get("created_at")),
            "mark": {
                "viewed": bool(paper.get("viewed")),
                "liked": bool(paper.get("liked")),
                "favorited": bool(paper.get("favorited")),
                "first_viewed_at": _iso(paper.get("first_viewed_at")),
                "viewed_at": _iso(paper.get("viewed_at")),
                "liked_at": _iso(paper.get("liked_at")),
                "favorited_at": _iso(paper.get("favorited_at")),
                "last_opened_at": _iso(paper.get("last_opened_at")),
            },
            "category_ids": paper.get("category_ids") or [],
        })

    return {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "exported_at": _iso(datetime.now(timezone.utc)),
        "categories": [
            {
                "id": str(row["id"]),
                "parent_id": str(row["parent_id"]) if row.get("parent_id") else None,
                "name": row["name"],
                "position": int(row.get("position") or 0),
                "color": row.get("color"),
            }
            for row in categories
        ],
        "papers": papers,
    }


def build_export_info(user_id: str) -> dict:
    """Lightweight candidate list for the export checkbox UI."""
    snapshot = database.get_export_snapshot(user_id)
    papers = [
        {
            "id": paper["id"],
            "title": paper.get("title"),
            "venue": paper.get("venue"),
            "has_ai": bool(paper.get("llm_response")),
            "category_ids": paper.get("category_ids") or [],
        }
        for paper in snapshot
    ]
    local_category_ids: set[str] = set()
    for paper in papers:
        local_category_ids.update(paper["category_ids"])
    categories = _fetch_categories_by_ids(local_category_ids) if local_category_ids else []
    return {
        "papers": papers,
        "categories": [
            {
                "id": str(row["id"]),
                "parent_id": str(row["parent_id"]) if row.get("parent_id") else None,
                "name": row["name"],
                "position": int(row.get("position") or 0),
                "color": row.get("color"),
            }
            for row in categories
        ],
    }


def preview_import(payload: ExportPayloadV1, user_id: str) -> dict:
    """Read-only diff of the payload against the local library."""
    file_paper_ids = [paper.id for paper in payload.papers]
    file_arxiv_ids = [
        paper.arxiv.arxiv_id for paper in payload.papers if paper.arxiv is not None
    ]

    def operation() -> tuple[dict[str, dict], list[dict]]:
        with database._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT p.id, ap.arxiv_id, p.llm_response, p.abstract_zh,
                           pm.note AS local_note
                    FROM papers p
                    LEFT JOIN arxiv_papers ap ON ap.paper_id = p.id
                    LEFT JOIN paper_marks pm ON pm.user_id = %s AND pm.paper_id = p.id
                    WHERE p.id = ANY(%s) OR ap.arxiv_id = ANY(%s)
                    """,
                    (user_id, file_paper_ids, file_arxiv_ids),
                )
                local_by_paper_id = {row["id"]: dict(row) for row in cur.fetchall()}

                cur.execute(
                    """
                    SELECT id, parent_id, name, position
                    FROM paper_categories
                    WHERE user_id = %s
                    ORDER BY parent_id NULLS FIRST, position, name
                    """,
                    (user_id,),
                )
                local_rows = [dict(row) for row in cur.fetchall()]
                for row in local_rows:
                    row["id"] = str(row["id"])
                    row["parent_id"] = (
                        str(row["parent_id"]) if row.get("parent_id") else None
                    )
        return local_by_paper_id, local_rows

    try:
        local_by_paper_id, local_rows = _run_with_retry(
            operation, f"library_import_preview:{user_id}",
        )
    except database.DatabaseError as exc:
        raise TransferError("读取本地论文库失败，请重试") from exc

    local_id_by_arxiv: dict[str, str] = {}
    for local_id, row in local_by_paper_id.items():
        if row.get("arxiv_id"):
            local_id_by_arxiv[row["arxiv_id"]] = local_id

    papers_preview = []
    new_count = 0
    merge_count = 0
    for paper in payload.papers:
        # Match by local paper id first, then fall back to arxiv id.
        local = local_by_paper_id.get(paper.id)
        if local is None and paper.arxiv is not None:
            local_id = local_id_by_arxiv.get(paper.arxiv.arxiv_id)
            local = local_by_paper_id.get(local_id) if local_id else None
        is_new = local is None
        if is_new:
            new_count += 1
        else:
            merge_count += 1
        papers_preview.append({
            "id": paper.id,
            "title": paper.title,
            "is_new": is_new,
            "will_fill_ai": bool(
                not is_new and paper.llm_response and not (local.get("llm_response"))
            ),
            "will_fill_abstract_zh": bool(
                not is_new and paper.abstract_zh and not (local.get("abstract_zh"))
            ),
            "will_fill_note": bool(
                not is_new and paper.note and not (local.get("local_note"))
            ),
        })

    referenced_ids: set[str] = set()
    for paper in payload.papers:
        referenced_ids.update(paper.category_ids)
    plan = plan_categories(payload.categories, referenced_ids, local_rows)

    # Human-readable paths for the categories the import will touch.
    file_by_id = {c.id: c for c in payload.categories}
    export_to_local = plan["export_to_local"]
    local_name_by_id = {row["id"]: row["name"] for row in local_rows}

    def category_path(export_id: str) -> str:
        names: list[str] = []
        current = file_by_id.get(export_id)
        while current is not None:
            names.append(current.name)
            current = file_by_id.get(current.parent_id) if current.parent_id else None
        return " / ".join(reversed(names))

    categories_preview = []
    for export_id in sorted(referenced_ids):
        node = file_by_id.get(export_id)
        if node is None:
            continue
        target = export_to_local.get(export_id)
        will_merge = bool(target and not target.startswith("new:"))
        categories_preview.append({
            "path": category_path(export_id),
            "will_merge": will_merge,
            "existing_name": local_name_by_id.get(target) if will_merge else None,
        })

    return {
        "exported_at": _iso(payload.exported_at),
        "papers": papers_preview,
        "new_count": new_count,
        "merge_count": merge_count,
        "categories": categories_preview,
        "new_category_count": plan["created_count"],
        "reused_category_count": plan["reused_count"],
    }


def apply_import(payload: ExportPayloadV1, selected_paper_ids: list[str], user_id: str) -> dict:
    """Merge the selected papers from the payload into the local library.

    One transaction via _run_with_retry: the closure re-reads local state on
    every attempt, so retries are idempotent and failures roll back cleanly.
    """
    if not selected_paper_ids:
        raise TransferError("至少选择一篇论文")
    payload_papers = {paper.id: paper for paper in payload.papers}
    unknown = set(selected_paper_ids) - set(payload_papers)
    if unknown:
        raise TransferError("选择了文件中不存在的论文")

    selected = [payload_papers[pid] for pid in selected_paper_ids]
    referenced_export_ids: set[str] = set()
    for paper in selected:
        referenced_export_ids.update(paper.category_ids)
    file_arxiv_ids = [
        paper.arxiv.arxiv_id for paper in selected if paper.arxiv is not None
    ]
    selected_ids = [paper.id for paper in selected]

    def operation() -> dict:
        with database._get_connection() as conn:
            with conn.cursor() as cur:
                # 1. Re-read local state (id/arxiv lookup + llm/zh/note emptiness).
                cur.execute(
                    """
                    SELECT p.id, ap.arxiv_id, p.llm_response, p.abstract_zh,
                           pm.note AS local_note, (pm.paper_id IS NOT NULL) AS has_mark
                    FROM papers p
                    LEFT JOIN arxiv_papers ap ON ap.paper_id = p.id
                    LEFT JOIN paper_marks pm ON pm.user_id = %s AND pm.paper_id = p.id
                    WHERE p.id = ANY(%s) OR ap.arxiv_id = ANY(%s)
                    """,
                    (user_id, selected_ids, file_arxiv_ids),
                )
                local_rows = cur.fetchall()
                local_by_paper_id = {row["id"]: dict(row) for row in local_rows}
                local_id_by_arxiv: dict[str, str] = {}
                for row in local_rows:
                    if row.get("arxiv_id"):
                        local_id_by_arxiv[row["arxiv_id"]] = row["id"]

                # 2. Local category tree for merge planning.
                cur.execute(
                    """
                    SELECT id, parent_id, name, position
                    FROM paper_categories
                    WHERE user_id = %s
                    ORDER BY parent_id NULLS FIRST, position, name
                    """,
                    (user_id,),
                )
                local_tree = [dict(row) for row in cur.fetchall()]
                for row in local_tree:
                    row["parent_id"] = (
                        str(row["parent_id"]) if row.get("parent_id") else None
                    )
                    row["id"] = str(row["id"])

                plan = plan_categories(payload.categories, referenced_export_ids, local_tree)

                # 3. Create missing categories (parents already come first in
                #    plan order), mapping export ids to real local ids.
                export_to_local: dict[str, str] = dict(plan["export_to_local"])
                created_categories = 0
                for create in plan["creates"]:
                    parent_id = create["parent_id"]
                    # Resolve a parent that was itself created in this loop.
                    if parent_id is not None and parent_id.startswith("new:"):
                        parent_id = export_to_local.get(parent_id.removeprefix("new:"))
                    cur.execute(
                        """
                        INSERT INTO paper_categories (user_id, parent_id, name, position, color)
                        SELECT %s, %s, %s, %s, %s
                        WHERE NOT EXISTS (
                            SELECT 1 FROM paper_categories
                            WHERE user_id = %s
                              AND parent_id IS NOT DISTINCT FROM %s
                              AND name = %s
                        )
                        RETURNING id
                        """,
                        (
                            user_id, parent_id, create["name"], create["position"],
                            create.get("color"),
                            user_id, parent_id, create["name"],
                        ),
                    )
                    row = cur.fetchone()
                    if row is None:
                        # The NOT EXISTS guard found a concurrent duplicate:
                        # fall back to reading the existing row.
                        cur.execute(
                            """
                            SELECT id FROM paper_categories
                            WHERE user_id = %s
                              AND parent_id IS NOT DISTINCT FROM %s
                              AND name = %s
                            """,
                            (user_id, parent_id, create["name"]),
                        )
                        row = cur.fetchone()
                        if row is None:
                            raise TransferError(f"分类「{create['name']}」创建失败")
                    else:
                        created_categories += 1
                    export_to_local[create["export_id"]] = str(row["id"])

                # 4. Papers: insert new / merge existing.
                created_papers = 0
                merged_papers = 0
                assignments_added = 0
                for paper in selected:
                    local_id = paper.id
                    local = local_by_paper_id.get(paper.id)
                    if local is None and paper.arxiv is not None:
                        mapped = local_id_by_arxiv.get(paper.arxiv.arxiv_id)
                        if mapped:
                            local_id = mapped
                            local = local_by_paper_id.get(mapped)

                    if local is None:
                        _insert_new_paper(cur, user_id, paper)
                        created_papers += 1
                    else:
                        merged_papers += 1
                        if paper.llm_response and not local.get("llm_response"):
                            cur.execute(
                                "UPDATE papers SET llm_response = %s WHERE id = %s",
                                (paper.llm_response, local_id),
                            )
                        if paper.abstract_zh and not local.get("abstract_zh"):
                            cur.execute(
                                """
                                UPDATE papers
                                SET abstract_zh = %s, abstract_zh_at = NOW()
                                WHERE id = %s
                                """,
                                (paper.abstract_zh, local_id),
                            )
                        # Note fills an empty local note but never overwrites
                        # one (same semantics as llm_response above).
                        if paper.note and not local.get("local_note"):
                            if local.get("has_mark"):
                                cur.execute(
                                    """
                                    UPDATE paper_marks
                                    SET note = %s, updated_at = NOW()
                                    WHERE user_id = %s AND paper_id = %s
                                    """,
                                    (paper.note, user_id, local_id),
                                )
                            else:
                                # No mark row yet: create one (viewed=TRUE, same
                                # invariant as normalize_mark — an imported note
                                # must be visible in My Papers).
                                cur.execute(
                                    """
                                    INSERT INTO paper_marks (
                                        user_id, paper_id, viewed, viewed_at,
                                        first_viewed_at, note, created_at, updated_at
                                    )
                                    VALUES (%s, %s, TRUE, NOW(), NOW(), %s, NOW(), NOW())
                                    ON CONFLICT (user_id, paper_id) DO NOTHING
                                    """,
                                    (user_id, local_id, paper.note),
                                )

                    # 5. Category assignments (missing ones only).
                    for export_id in paper.category_ids:
                        local_category_id = export_to_local.get(export_id)
                        if not local_category_id:
                            continue
                        cur.execute(
                            """
                            INSERT INTO paper_category_assignments
                                (user_id, paper_id, category_id)
                            SELECT %s, %s, %s
                            WHERE NOT EXISTS (
                                SELECT 1 FROM paper_category_assignments
                                WHERE user_id = %s AND paper_id = %s
                                  AND category_id = %s
                            )
                            """,
                            (user_id, local_id, local_category_id, user_id, local_id, local_category_id),
                        )
                        if cur.rowcount:
                            assignments_added += 1

                    # 6. Mark: only for newly inserted papers — existing
                    #    papers keep their local marks untouched.
                    if local is None:
                        mark = normalize_mark(paper.mark, payload.exported_at)
                        cur.execute(
                            """
                            INSERT INTO paper_marks (
                                user_id, paper_id, viewed, liked, favorited,
                                first_viewed_at, viewed_at, liked_at, favorited_at,
                                last_opened_at, note, created_at, updated_at
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                            ON CONFLICT (user_id, paper_id) DO NOTHING
                            """,
                            (
                                user_id, paper.id, mark["viewed"], mark["liked"],
                                mark["favorited"], mark["first_viewed_at"],
                                mark["viewed_at"], mark["liked_at"], mark["favorited_at"],
                                mark["last_opened_at"], paper.note,
                            ),
                        )

            conn.commit()

        database._conference_cache.clear()
        database._cache_timestamp.clear()
        return {
            "created_papers": created_papers,
            "merged_papers": merged_papers,
            "created_categories": created_categories,
            "reused_categories": plan["reused_count"],
            "assignments_added": assignments_added,
        }

    try:
        return _run_with_retry(operation, f"library_import:{user_id}")
    except database.NoRetryError:
        raise
    except database.DatabaseError as exc:
        raise TransferError("导入失败：数据库暂不可用，请重试") from exc


def _insert_new_paper(cur, user_id: str, paper: ExportPaper) -> None:
    normalized_keywords = [k.strip() for k in paper.keywords if k and k.strip()]
    cur.execute(
        """
        INSERT INTO papers (
            id, title, abstract, keywords, pdf, venue, primary_area,
            llm_response, abstract_zh, abstract_zh_at, created_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, NOW()))
        ON CONFLICT (id) DO NOTHING
        """,
        (
            paper.id, paper.title, paper.abstract,
            database.Jsonb(normalized_keywords),
            paper.pdf, paper.venue, paper.primary_area,
            paper.llm_response, paper.abstract_zh,
            datetime.now(timezone.utc) if paper.abstract_zh else None,
            paper.created_at,
        ),
    )
    cur.execute(
        "DELETE FROM authors WHERE paper_id = %s",
        (paper.id,),
    )
    if paper.authors:
        cur.executemany(
            """
            INSERT INTO authors (paper_id, author_name, author_order)
            VALUES (%s, %s, %s)
            """,
            [(paper.id, author, index) for index, author in enumerate(paper.authors)],
        )
    cur.execute("DELETE FROM keywords WHERE paper_id = %s", (paper.id,))
    if normalized_keywords:
        cur.executemany(
            "INSERT INTO keywords (paper_id, keyword) VALUES (%s, %s)",
            [(paper.id, keyword) for keyword in normalized_keywords],
        )
    if paper.arxiv is not None:
        arxiv_url = paper.arxiv.arxiv_url or build_arxiv_abs_url(paper.arxiv.arxiv_id)
        pdf_url = paper.arxiv.pdf_url or build_arxiv_pdf_url(paper.arxiv.arxiv_id)
        cur.execute(
            """
            INSERT INTO arxiv_papers (
                paper_id, arxiv_id, arxiv_url, pdf_url,
                published_at, arxiv_updated_at, added_by_user_id, added_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, COALESCE(%s, NOW()))
            ON CONFLICT (paper_id) DO NOTHING
            """,
            (
                paper.id, paper.arxiv.arxiv_id, arxiv_url,
                pdf_url, paper.arxiv.published_at,
                paper.arxiv.updated_at, uuid.UUID(user_id), paper.created_at,
            ),
        )


def _fetch_categories_by_ids(category_ids: set[str]) -> list[dict]:
    def operation() -> list[dict]:
        with database._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, parent_id, name, position, color
                    FROM paper_categories
                    WHERE id = ANY(%s)
                    ORDER BY parent_id NULLS FIRST, position, name
                    """,
                    ([str(cid) for cid in category_ids],),
                )
                rows = [dict(row) for row in cur.fetchall()]
        for row in rows:
            row["id"] = str(row["id"])
            row["parent_id"] = str(row["parent_id"]) if row.get("parent_id") else None
        return rows

    return _run_with_retry(operation, "library_export_categories")


def dumps_payload(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)
