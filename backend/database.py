import logging
import re
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Iterator, TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from config import settings
from utils import get_openreview_pdf_url, normalize_paper_pdf_url

DATABASE_URL = settings.database.url

logger = logging.getLogger(__name__)
T = TypeVar("T")

# Cache for conference/search results
_conference_cache = {}
_cache_timestamp = {}
_CACHE_TTL_SECONDS = 86400
_READ_FILTER_SEARCH_LIMIT = 1_000_000
CODE_AVAILABILITY_STATUSES = {"open_source", "unavailable", "not_found", "unknown"}
CODE_FILTERS = CODE_AVAILABILITY_STATUSES | {"all", "not_open_source"}


class DatabaseError(Exception):
    """Raised when database access fails after retries."""


class NoRetryError(Exception):
    """Marker base for caller-defined errors that must NOT be retried.

    Raise a NoRetryError subclass inside an operation to abort the retry
    loop immediately; _run_with_retry re-raises it untouched.
    """


def _normalize_user_row(row: dict | None) -> dict | None:
    if not row:
        return None
    normalized = dict(row)
    normalized["id"] = str(normalized["id"])
    return normalized


def _normalize_session_row(row: dict | None) -> dict | None:
    if not row:
        return None
    normalized = dict(row)
    if normalized.get("account_user_id") is not None:
        normalized["account_user_id"] = str(normalized["account_user_id"])
    return normalized


def _normalize_llm_provider_row(row: dict | None) -> dict | None:
    if not row:
        return None
    normalized = dict(row)
    normalized["id"] = str(normalized["id"])
    return normalized


def _normalize_llm_model_row(row: dict | None) -> dict | None:
    if not row:
        return None
    normalized = dict(row)
    normalized["id"] = str(normalized["id"])
    normalized["provider_id"] = str(normalized["provider_id"])
    return normalized


def _as_nonnegative_int(value: object) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(parsed, 0)


def _normalize_uuid(value: object) -> str | None:
    if not value:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError):
        return None


def _reading_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(settings.hf_daily.timezone)
    except ZoneInfoNotFoundError:
        logger.warning("阅读概览 timezone 无效，回退到 UTC: %s", settings.hf_daily.timezone)
        return ZoneInfo("UTC")


def _run_with_retry(
    operation: Callable[[], T],
    context: str,
    retries: int = 3,
    delay: float = 1.0,
) -> T:
    last_error: Exception | None = None

    for attempt in range(retries):
        try:
            return operation()
        except NoRetryError:
            raise
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Database operation failed for %s (attempt %s/%s): %s",
                context,
                attempt + 1,
                retries,
                exc,
            )
            if attempt < retries - 1:
                time.sleep(delay)

    raise DatabaseError(f"Database operation failed for {context}") from last_error


@contextmanager
def _get_connection() -> Iterator[psycopg.Connection]:
    if not DATABASE_URL:
        raise DatabaseError("DATABASE_URL is not configured")

    conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    try:
        yield conn
    finally:
        conn.close()


def _fetch_keywords_for_papers(conn: psycopg.Connection, paper_ids: list[str]) -> dict[str, list[str]]:
    if not paper_ids:
        return {}

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT paper_id, keyword
            FROM keywords
            WHERE paper_id = ANY(%s)
            ORDER BY id
            """,
            (paper_ids,),
        )
        rows = cur.fetchall()

    keywords_by_paper: dict[str, list[str]] = {}
    for row in rows:
        keywords_by_paper.setdefault(row["paper_id"], []).append(row["keyword"])
    return keywords_by_paper


def _arxiv_meta_from_row(row: dict | None) -> dict | None:
    if not row:
        return None
    return {
        "arxiv_id": row.get("arxiv_id"),
        "arxiv_url": row.get("arxiv_url"),
        "pdf_url": row.get("pdf_url"),
        "published_at": row.get("published_at"),
        "updated_at": row.get("updated_at"),
        "added_at": row.get("added_at"),
        "added_by_user_id": str(row["added_by_user_id"]) if row.get("added_by_user_id") else None,
        "metadata": row.get("metadata") or {},
    }


def _paper_pdf_for_output(paper_id: str, stored_pdf: str | None) -> str | None:
    """Assemble the outbound pdf field.

    manual: papers store the user-entered link verbatim — no normalization
    (which would rewrite an openreview URL to ?id=manual:xxx and destroy it)
    and never an OpenReview fallback URL (paper_id is not a real openreview
    id; the fabricated link 404s and sends the analysis path on a 10-30s
    Jina Reader wild-goose chase).
    """
    if paper_id.startswith("manual:"):
        return stored_pdf
    return normalize_paper_pdf_url(paper_id, stored_pdf) or get_openreview_pdf_url(paper_id)


def _paper_from_arxiv_row(row: dict) -> dict:
    paper = {
        "id": row["id"],
        "title": row.get("title"),
        "abstract": row.get("abstract"),
        "keywords": row.get("keywords") or [],
        "pdf": normalize_paper_pdf_url(row["id"], row.get("pdf")) or row.get("arxiv_pdf_url"),
        "venue": row.get("venue"),
        "primary_area": row.get("primary_area"),
        "llm_response": row.get("llm_response"),
        "created_at": row.get("created_at"),
        "code_status": row.get("code_status") or "unknown",
        "code_url": row.get("code_url"),
        "code_evidence": row.get("code_evidence"),
        "code_checked_at": row.get("code_checked_at"),
        "arxiv": _arxiv_meta_from_row(
            {
                "arxiv_id": row.get("arxiv_id"),
                "arxiv_url": row.get("arxiv_url"),
                "pdf_url": row.get("arxiv_pdf_url"),
                "published_at": row.get("arxiv_published_at"),
                "updated_at": row.get("arxiv_updated_at"),
                "added_at": row.get("arxiv_added_at"),
                "added_by_user_id": row.get("arxiv_added_by_user_id"),
                "metadata": row.get("arxiv_metadata"),
            }
        ),
    }
    return paper


def get_paper(paper_id: str) -> dict | None:
    if not DATABASE_URL:
        return None

    def operation() -> dict | None:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM papers WHERE id = %s", (paper_id,))
                paper = cur.fetchone()
                if not paper:
                    return None

                cur.execute(
                    """
                    SELECT author_name
                    FROM authors
                    WHERE paper_id = %s
                    ORDER BY author_order
                    """,
                    (paper_id,),
                )
                paper["authors"] = [row["author_name"] for row in cur.fetchall()]

                cur.execute(
                    """
                    SELECT keyword
                    FROM keywords
                    WHERE paper_id = %s
                    ORDER BY id
                    """,
                    (paper_id,),
                )
                paper["keywords"] = [row["keyword"] for row in cur.fetchall()]
                paper["pdf"] = _paper_pdf_for_output(paper_id, paper.get("pdf"))
                cur.execute(
                    """
                    SELECT arxiv_id,
                           arxiv_url,
                           pdf_url,
                           published_at,
                           arxiv_updated_at AS updated_at,
                           added_at,
                           added_by_user_id,
                           metadata
                    FROM arxiv_papers
                    WHERE paper_id = %s
                    """,
                    (paper_id,),
                )
                arxiv_meta = _arxiv_meta_from_row(cur.fetchone())
                if arxiv_meta:
                    paper["arxiv"] = arxiv_meta
                return paper

    return _run_with_retry(operation, f"get_paper:{paper_id}")


def get_papers_by_ids(paper_ids: list[str]) -> dict[str, dict]:
    """Fetch multiple papers by id (with authors + keywords). Returns {id: paper}."""
    if not paper_ids:
        return {}
    unique_ids = list({pid for pid in paper_ids if pid})

    def operation() -> dict[str, dict]:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, title, abstract, keywords, pdf, venue, primary_area, llm_response
                    FROM papers WHERE id = ANY(%s)
                    """,
                    (unique_ids,),
                )
                papers = {row["id"]: dict(row) for row in cur.fetchall()}
        return papers

    return _run_with_retry(operation, "get_papers_by_ids")



def save_paper(paper_info: dict, llm_response: str = None):
    if not DATABASE_URL:
        return

    def operation() -> None:
        normalized_pdf = normalize_paper_pdf_url(paper_info["id"], paper_info.get("pdf"))
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO papers (
                        id,
                        title,
                        abstract,
                        keywords,
                        pdf,
                        venue,
                        primary_area,
                        sort_order,
                        llm_response
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        title = EXCLUDED.title,
                        abstract = EXCLUDED.abstract,
                        keywords = EXCLUDED.keywords,
                        pdf = EXCLUDED.pdf,
                        venue = EXCLUDED.venue,
                        primary_area = EXCLUDED.primary_area,
                        sort_order = EXCLUDED.sort_order,
                        llm_response = EXCLUDED.llm_response
                    """,
                    (
                        paper_info["id"],
                        paper_info.get("title"),
                        paper_info.get("abstract"),
                        Jsonb(paper_info.get("keywords", [])),
                        normalized_pdf,
                        paper_info.get("venue"),
                        paper_info.get("primary_area"),
                        paper_info.get("sort_order"),
                        llm_response,
                    ),
                )

                cur.execute("DELETE FROM authors WHERE paper_id = %s", (paper_info["id"],))
                authors = paper_info.get("authors", [])
                if authors:
                    cur.executemany(
                        """
                        INSERT INTO authors (paper_id, author_name, author_order)
                        VALUES (%s, %s, %s)
                        """,
                        [
                            (paper_info["id"], author, index)
                            for index, author in enumerate(authors)
                        ],
                    )

                cur.execute("DELETE FROM keywords WHERE paper_id = %s", (paper_info["id"],))
                keywords = paper_info.get("keywords", [])
                if keywords:
                    cur.executemany(
                        """
                        INSERT INTO keywords (paper_id, keyword)
                        VALUES (%s, %s)
                        """,
                        [(paper_info["id"], keyword) for keyword in keywords],
                    )

            conn.commit()

    _run_with_retry(operation, f"save_paper:{paper_info['id']}")


def upsert_arxiv_paper(
    paper_info: dict,
    arxiv_info: dict,
    added_by_user_id: str | None = None,
) -> dict:
    if not DATABASE_URL:
        return {
            **paper_info,
            "arxiv": _arxiv_meta_from_row(
                {
                    "arxiv_id": arxiv_info.get("arxiv_id"),
                    "arxiv_url": arxiv_info.get("arxiv_url"),
                    "pdf_url": arxiv_info.get("pdf_url"),
                    "published_at": arxiv_info.get("published_at"),
                    "updated_at": arxiv_info.get("updated_at"),
                    "added_at": None,
                    "added_by_user_id": added_by_user_id,
                    "metadata": arxiv_info.get("raw") or {},
                }
            ),
        }

    def operation() -> dict:
        paper_id = paper_info["id"]
        normalized_pdf = normalize_paper_pdf_url(paper_id, paper_info.get("pdf"))
        arxiv_metadata = {
            "primary_category": arxiv_info.get("primary_category"),
            "categories": arxiv_info.get("categories") or [],
            "comment": arxiv_info.get("comment"),
            "journal_ref": arxiv_info.get("journal_ref"),
            "doi": arxiv_info.get("doi"),
            "raw": arxiv_info.get("raw") or {},
        }

        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO papers (
                        id,
                        title,
                        abstract,
                        keywords,
                        pdf,
                        venue,
                        primary_area
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        title = EXCLUDED.title,
                        abstract = EXCLUDED.abstract,
                        keywords = EXCLUDED.keywords,
                        pdf = EXCLUDED.pdf,
                        venue = EXCLUDED.venue,
                        primary_area = EXCLUDED.primary_area
                    RETURNING *
                    """,
                    (
                        paper_id,
                        paper_info.get("title"),
                        paper_info.get("abstract"),
                        Jsonb(paper_info.get("keywords", [])),
                        normalized_pdf,
                        paper_info.get("venue"),
                        paper_info.get("primary_area"),
                    ),
                )
                paper = cur.fetchone()

                cur.execute("DELETE FROM authors WHERE paper_id = %s", (paper_id,))
                authors = paper_info.get("authors", [])
                if authors:
                    cur.executemany(
                        """
                        INSERT INTO authors (paper_id, author_name, author_order)
                        VALUES (%s, %s, %s)
                        """,
                        [(paper_id, author, index) for index, author in enumerate(authors)],
                    )

                cur.execute("DELETE FROM keywords WHERE paper_id = %s", (paper_id,))
                keywords = paper_info.get("keywords", [])
                if keywords:
                    cur.executemany(
                        """
                        INSERT INTO keywords (paper_id, keyword)
                        VALUES (%s, %s)
                        """,
                        [(paper_id, keyword) for keyword in keywords],
                    )

                cur.execute(
                    """
                    INSERT INTO arxiv_papers (
                        paper_id,
                        arxiv_id,
                        arxiv_url,
                        pdf_url,
                        published_at,
                        arxiv_updated_at,
                        added_by_user_id,
                        added_at,
                        metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), %s)
                    ON CONFLICT (arxiv_id) DO UPDATE SET
                        paper_id = EXCLUDED.paper_id,
                        arxiv_url = EXCLUDED.arxiv_url,
                        pdf_url = EXCLUDED.pdf_url,
                        published_at = EXCLUDED.published_at,
                        arxiv_updated_at = EXCLUDED.arxiv_updated_at,
                        added_by_user_id = COALESCE(EXCLUDED.added_by_user_id, arxiv_papers.added_by_user_id),
                        added_at = NOW(),
                        metadata = EXCLUDED.metadata,
                        updated_at = NOW()
                    RETURNING arxiv_id,
                              arxiv_url,
                              pdf_url,
                              published_at,
                              arxiv_updated_at AS updated_at,
                              added_at,
                              added_by_user_id,
                              metadata
                    """,
                    (
                        paper_id,
                        arxiv_info["arxiv_id"],
                        arxiv_info.get("arxiv_url"),
                        arxiv_info.get("pdf_url"),
                        arxiv_info.get("published_at"),
                        arxiv_info.get("updated_at"),
                        added_by_user_id,
                        Jsonb(arxiv_metadata),
                    ),
                )
                arxiv_row = cur.fetchone()

            conn.commit()

        _conference_cache.clear()
        _cache_timestamp.clear()
        paper["authors"] = authors
        paper["keywords"] = keywords
        paper["pdf"] = normalize_paper_pdf_url(paper_id, paper.get("pdf")) or paper.get("pdf")
        paper["arxiv"] = _arxiv_meta_from_row(arxiv_row)
        return paper

    return _run_with_retry(operation, f"upsert_arxiv_paper:{paper_info['id']}")


def upsert_manual_paper(fields: dict) -> dict:
    """Insert a hand-entered paper (no arxiv_papers row).

    The `manual:` id is generated here and callers cannot choose one — keeps
    the namespace disjoint from arxiv:/OpenReview ids (arxiv_id_from_paper_id
    only strips "arxiv:", so manual ids never re-enter the arXiv fetch path).
    The pdf link is stored verbatim (stripped): see _paper_pdf_for_output for
    why normalization would destroy openreview URLs.

    Papers/authors/keywords writes mirror upsert_arxiv_paper, including the
    defensive DELETEs — keeping the shapes identical lets this function be
    reused as "edit manual paper" later.
    """
    if not DATABASE_URL:
        return {
            **fields,
            "id": "manual:offline",
            "authors": fields.get("authors", []),
            "keywords": fields.get("keywords", []),
        }

    def operation() -> dict:
        paper_id = f"manual:{uuid.uuid4().hex[:12]}"
        pdf = (fields.get("pdf") or "").strip() or None
        authors = [a for a in (fields.get("authors") or []) if a]
        keywords = [k for k in (fields.get("keywords") or []) if k]

        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO papers (
                        id, title, abstract, keywords, pdf, venue,
                        primary_area, published_year
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        paper_id,
                        fields.get("title"),
                        fields.get("abstract"),
                        Jsonb(keywords),
                        pdf,
                        fields.get("venue"),
                        fields.get("primary_area"),
                        fields.get("published_year"),
                    ),
                )
                paper = cur.fetchone()

                cur.execute("DELETE FROM authors WHERE paper_id = %s", (paper_id,))
                if authors:
                    cur.executemany(
                        "INSERT INTO authors (paper_id, author_name, author_order) VALUES (%s, %s, %s)",
                        [(paper_id, author, index) for index, author in enumerate(authors)],
                    )

                cur.execute("DELETE FROM keywords WHERE paper_id = %s", (paper_id,))
                if keywords:
                    cur.executemany(
                        "INSERT INTO keywords (paper_id, keyword) VALUES (%s, %s)",
                        [(paper_id, keyword) for keyword in keywords],
                    )

            conn.commit()

        paper["authors"] = authors
        paper["keywords"] = keywords
        paper["pdf"] = pdf
        return paper

    return _run_with_retry(operation, f"upsert_manual_paper:{(fields.get('title') or '')[:50]}")


def update_llm_response(paper_id: str, response: str):
    if not DATABASE_URL:
        return

    def operation() -> None:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE papers
                    SET llm_response = %s
                    WHERE id = %s
                    """,
                    (response, paper_id),
                )
            conn.commit()

    _run_with_retry(operation, f"update_llm_response:{paper_id}")


def update_paper_code_availability(
    paper_id: str,
    status: str,
    code_url: str | None = None,
    evidence: str | None = None,
    meta: dict | None = None,
) -> None:
    if not DATABASE_URL:
        return
    normalized_status = status if status in CODE_AVAILABILITY_STATUSES else "unknown"

    def operation() -> None:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE papers
                    SET code_status = %s,
                        code_url = %s,
                        code_evidence = %s,
                        code_meta = %s,
                        code_checked_at = NOW()
                    WHERE id = %s
                    """,
                    (
                        normalized_status,
                        code_url if normalized_status == "open_source" else None,
                        evidence,
                        Jsonb(meta or {}),
                        paper_id,
                    ),
                )
            conn.commit()

        _conference_cache.clear()
        _cache_timestamp.clear()

    _run_with_retry(operation, f"update_paper_code_availability:{paper_id}")


def create_user(
    email: str,
    email_normalized: str,
    password_hash: str | None,
    role: str = "user",
    email_verified: bool = False,
) -> dict:
    def operation() -> dict:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (email, email_normalized, password_hash, role, email_verified)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (email, email_normalized, password_hash, role, email_verified),
                )
                user = cur.fetchone()
            conn.commit()
        return _normalize_user_row(user)

    return _run_with_retry(operation, f"create_user:{email_normalized}")


def get_user_by_email(email_normalized: str) -> dict | None:
    if not DATABASE_URL:
        return None

    def operation() -> dict | None:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE email_normalized = %s", (email_normalized,))
                return _normalize_user_row(cur.fetchone())

    return _run_with_retry(operation, f"get_user_by_email:{email_normalized}")


def get_user_by_id(user_id: str) -> dict | None:
    if not DATABASE_URL:
        return None

    def operation() -> dict | None:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
                return _normalize_user_row(cur.fetchone())

    return _run_with_retry(operation, f"get_user_by_id:{user_id}")


def ensure_admin_user(email: str, email_normalized: str, password_hash: str) -> dict:
    def operation() -> dict:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE email_normalized = %s", (email_normalized,))
                existing = cur.fetchone()
                if existing:
                    cur.execute(
                        """
                        UPDATE users
                        SET role = 'admin', is_active = TRUE, updated_at = NOW()
                        WHERE id = %s
                        RETURNING *
                        """,
                        (existing["id"],),
                    )
                    user = cur.fetchone()
                else:
                    cur.execute(
                        """
                        INSERT INTO users (email, email_normalized, password_hash, role, email_verified)
                        VALUES (%s, %s, %s, 'admin', TRUE)
                        RETURNING *
                        """,
                        (email, email_normalized, password_hash),
                    )
                    user = cur.fetchone()
            conn.commit()
        return _normalize_user_row(user)

    return _run_with_retry(operation, f"ensure_admin_user:{email_normalized}")


def _normalize_model_names(model_names: list[str] | None) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for raw_name in model_names or []:
        model_name = str(raw_name or "").strip()
        if not model_name or model_name in seen:
            continue
        seen.add(model_name)
        normalized.append(model_name)
    return normalized


def _provider_key_from_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "custom-provider"


def _unique_llm_provider_key(cur: psycopg.Cursor, name: str) -> str:
    base_key = _provider_key_from_name(name)
    provider_key = base_key
    while True:
        cur.execute("SELECT 1 FROM llm_providers WHERE provider_key = %s", (provider_key,))
        if not cur.fetchone():
            return provider_key
        provider_key = f"{base_key}-{uuid.uuid4().hex[:8]}"


def _fetch_llm_models_for_provider(
    conn: psycopg.Connection,
    provider_ids: list[uuid.UUID],
) -> dict[str, list[dict]]:
    if not provider_ids:
        return {}

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, provider_id, model_name, display_name, is_enabled, source, created_at, updated_at
            FROM llm_models
            WHERE provider_id = ANY(%s)
            ORDER BY model_name
            """,
            (provider_ids,),
        )
        rows = cur.fetchall()

    models_by_provider: dict[str, list[dict]] = {}
    for row in rows:
        model = _normalize_llm_model_row(row)
        models_by_provider.setdefault(model["provider_id"], []).append(model)
    return models_by_provider


def ensure_default_llm_providers(provider_specs: list[dict]) -> None:
    def operation() -> None:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                for spec in provider_specs:
                    provider_key = spec["provider_key"]
                    name = spec["name"].strip()
                    base_url = spec["base_url"].strip().rstrip("/")
                    api_key = (spec.get("api_key") or "").strip() or None
                    active_model = (spec.get("active_model") or "").strip() or None
                    default_parameters = spec.get("default_parameters") or {}

                    cur.execute(
                        """
                        INSERT INTO llm_providers (
                          provider_key, name, base_url, api_key, is_builtin,
                          active_model, default_parameters
                        )
                        VALUES (%s, %s, %s, %s, TRUE, %s, %s)
                        ON CONFLICT (provider_key) DO UPDATE SET
                          name = EXCLUDED.name,
                          base_url = EXCLUDED.base_url,
                          api_key = CASE
                            WHEN COALESCE(llm_providers.api_key, '') = ''
                              THEN EXCLUDED.api_key
                            ELSE llm_providers.api_key
                          END,
                          is_builtin = TRUE,
                          active_model = COALESCE(NULLIF(llm_providers.active_model, ''), EXCLUDED.active_model),
                          default_parameters = EXCLUDED.default_parameters,
                          updated_at = NOW()
                        RETURNING id
                        """,
                        (
                            provider_key,
                            name,
                            base_url,
                            api_key,
                            active_model,
                            Jsonb(default_parameters),
                        ),
                    )
                    provider_id = cur.fetchone()["id"]

                    for model_name in _normalize_model_names(spec.get("models")):
                        cur.execute(
                            """
                            INSERT INTO llm_models (provider_id, model_name, display_name, source)
                            VALUES (%s, %s, %s, 'seed')
                            ON CONFLICT (provider_id, model_name) DO UPDATE SET
                              display_name = COALESCE(llm_models.display_name, EXCLUDED.display_name),
                              is_enabled = TRUE,
                              updated_at = NOW()
                            """,
                            (provider_id, model_name, model_name),
                        )

                cur.execute("SELECT id FROM llm_providers WHERE is_active AND is_enabled LIMIT 1")
                active = cur.fetchone()
                if not active:
                    cur.execute(
                        """
                        SELECT id
                        FROM llm_providers
                        WHERE is_enabled
                          AND COALESCE(api_key, '') <> ''
                        ORDER BY CASE WHEN provider_key = 'step' THEN 0 ELSE 1 END,
                                 is_builtin DESC,
                                 name
                        LIMIT 1
                        """
                    )
                    selected = cur.fetchone()
                    if selected:
                        cur.execute("UPDATE llm_providers SET is_active = FALSE WHERE is_active")
                        cur.execute(
                            """
                            UPDATE llm_providers
                            SET is_active = TRUE, updated_at = NOW()
                            WHERE id = %s
                            """,
                            (selected["id"],),
                        )
            conn.commit()

    _run_with_retry(operation, "ensure_default_llm_providers")


def list_llm_providers(include_models: bool = True) -> list[dict]:
    def operation() -> list[dict]:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, provider_key, name, base_url, api_key, is_active, is_enabled,
                           is_builtin, active_model, default_parameters, models_fetched_at,
                           created_at, updated_at
                    FROM llm_providers
                    ORDER BY is_active DESC, is_builtin DESC, name
                    """
                )
                provider_rows = cur.fetchall()

            provider_ids = [row["id"] for row in provider_rows]
            models_by_provider = _fetch_llm_models_for_provider(conn, provider_ids) if include_models else {}

        providers = []
        for row in provider_rows:
            provider = _normalize_llm_provider_row(row)
            if include_models:
                provider["models"] = models_by_provider.get(provider["id"], [])
            providers.append(provider)
        return providers

    return _run_with_retry(operation, "list_llm_providers")


def update_llm_provider_api_key(provider_id: str, api_key: str | None) -> dict | None:
    """Set (or clear, with empty string) a provider's API key from the settings
    dialog. Returns the refreshed provider or None if it doesn't exist."""
    def operation() -> dict | None:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE llm_providers
                    SET api_key = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    ((api_key or "").strip() or None, provider_id),
                )
            conn.commit()
        return get_llm_provider(provider_id)

    return _run_with_retry(operation, f"update_llm_provider_api_key:{provider_id}")


def create_llm_provider(
    provider_key: str,
    name: str,
    base_url: str,
    api_key: str | None = None,
    active_model: str | None = None,
) -> dict:
    """Create a custom provider from the settings dialog (non-builtin).

    The provider starts disabled (is_enabled=False); the user enables it via
    the activate endpoint once a key is set. Duplicate provider_key raises
    psycopg UniqueViolation, surfaced by the caller as a user-facing error.
    """
    normalized_key = provider_key.strip().lower()
    if not normalized_key:
        raise ValueError("供应商标识不能为空")

    def operation() -> dict:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        INSERT INTO llm_providers (
                            provider_key, name, base_url, api_key,
                            is_builtin, is_enabled, active_model
                        )
                        VALUES (%s, %s, %s, %s, FALSE, FALSE, %s)
                        RETURNING id
                        """,
                        (
                            normalized_key,
                            name.strip(),
                            base_url.strip().rstrip("/"),
                            (api_key or "").strip() or None,
                            (active_model or "").strip() or None,
                        ),
                    )
                except psycopg.errors.UniqueViolation as exc:
                    raise LlmProviderConflict(f"供应商标识「{normalized_key}」已存在") from exc
                provider_id = str(cur.fetchone()["id"])
                model_name = (active_model or "").strip()
                if model_name:
                    cur.execute(
                        """
                        INSERT INTO llm_models (provider_id, model_name, display_name, source)
                        VALUES (%s, %s, %s, 'manual')
                        ON CONFLICT (provider_id, model_name) DO NOTHING
                        """,
                        (provider_id, model_name, model_name),
                    )
            conn.commit()
        return get_llm_provider(provider_id)

    return _run_with_retry(operation, f"create_llm_provider:{normalized_key}")


class LlmProviderConflict(NoRetryError):
    """provider_key already exists (unique violation on create)."""


def update_llm_provider(provider_id: str, name: str, base_url: str) -> dict | None:
    """Edit a provider's display name and base URL from the settings dialog."""
    def operation() -> dict | None:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE llm_providers
                    SET name = %s, base_url = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (name.strip(), base_url.strip().rstrip("/"), provider_id),
                )
            conn.commit()
        return get_llm_provider(provider_id)

    return _run_with_retry(operation, f"update_llm_provider:{provider_id}")


def get_llm_provider(provider_id: str, include_models: bool = True) -> dict | None:
    def operation() -> dict | None:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, provider_key, name, base_url, api_key, is_active, is_enabled,
                           is_builtin, active_model, default_parameters, models_fetched_at,
                           created_at, updated_at
                    FROM llm_providers
                    WHERE id = %s
                    """,
                    (provider_id,),
                )
                row = cur.fetchone()
            if not row:
                return None
            provider = _normalize_llm_provider_row(row)
            if include_models:
                provider["models"] = _fetch_llm_models_for_provider(conn, [row["id"]]).get(provider["id"], [])
            return provider

    return _run_with_retry(operation, f"get_llm_provider:{provider_id}")


def set_active_llm_provider(provider_id: str, model_name: str | None = None) -> dict | None:
    """Mark one provider active (optionally setting its active_model) for the
    settings dialog. Returns the refreshed provider or None if disabled/missing."""
    def operation() -> dict | None:
        selected_model = (model_name or "").strip()
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, is_enabled FROM llm_providers WHERE id = %s",
                    (provider_id,),
                )
                provider = cur.fetchone()
                if not provider or not provider["is_enabled"]:
                    return None

                if not selected_model:
                    cur.execute(
                        """
                        SELECT model_name
                        FROM llm_models
                        WHERE provider_id = %s AND is_enabled
                        ORDER BY created_at
                        LIMIT 1
                        """,
                        (provider_id,),
                    )
                    model = cur.fetchone()
                    selected_model = model["model_name"] if model else ""

                if selected_model:
                    cur.execute(
                        """
                        INSERT INTO llm_models (provider_id, model_name, display_name, source)
                        VALUES (%s, %s, %s, 'manual')
                        ON CONFLICT (provider_id, model_name) DO UPDATE SET
                          is_enabled = TRUE,
                          updated_at = NOW()
                        """,
                        (provider_id, selected_model, selected_model),
                    )

                cur.execute("UPDATE llm_providers SET is_active = FALSE WHERE is_active")
                cur.execute(
                    """
                    UPDATE llm_providers
                    SET is_active = TRUE,
                        active_model = NULLIF(%s, ''),
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (selected_model, provider_id),
                )
            conn.commit()

        return get_llm_provider(provider_id)

    return _run_with_retry(operation, f"set_active_llm_provider:{provider_id}")


def get_active_llm_config() -> dict | None:
    def operation() -> dict | None:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT p.id, p.provider_key, p.name, p.base_url, p.api_key, p.is_active,
                           p.is_enabled, p.is_builtin, p.active_model, p.default_parameters,
                           p.models_fetched_at, p.created_at, p.updated_at,
                           COALESCE(
                             NULLIF(p.active_model, ''),
                             (
                               SELECT m.model_name
                               FROM llm_models m
                               WHERE m.provider_id = p.id AND m.is_enabled
                               ORDER BY m.created_at
                               LIMIT 1
                             )
                           ) AS model_name
                    FROM llm_providers p
                    WHERE p.is_active AND p.is_enabled
                    LIMIT 1
                    """
                )
                row = cur.fetchone()
        return _normalize_llm_provider_row(row)

    return _run_with_retry(operation, "get_active_llm_config")


def _build_reading_activity(
    rows: list[dict],
    today: date,
    days: int,
) -> dict:
    safe_days = min(max(days, 28), 366)
    counts: dict[date, int] = {}
    for row in rows:
        activity_date = row.get("activity_date")
        if isinstance(activity_date, datetime):
            activity_date = activity_date.date()
        if not isinstance(activity_date, date):
            continue
        counts[activity_date] = _as_nonnegative_int(row.get("paper_count"))

    start_date = today - timedelta(days=safe_days - 1)
    activity_days = [
        {
            "date": (start_date + timedelta(days=offset)).isoformat(),
            "count": counts.get(start_date + timedelta(days=offset), 0),
        }
        for offset in range(safe_days)
    ]

    month_count = sum(
        count
        for activity_date, count in counts.items()
        if activity_date.year == today.year and activity_date.month == today.month
    )

    # A streak remains current until the end of the following day, so a user
    # who has not read yet today does not lose yesterday's streak prematurely.
    streak_cursor = today
    if counts.get(streak_cursor, 0) <= 0:
        streak_cursor -= timedelta(days=1)
    current_streak = 0
    while counts.get(streak_cursor, 0) > 0:
        current_streak += 1
        streak_cursor -= timedelta(days=1)

    return {
        "days": activity_days,
        "today_count": counts.get(today, 0),
        "month_count": month_count,
        "current_streak": current_streak,
    }


def get_reading_overview(
    user_id: str,
    days: int = 112,
    now: datetime | None = None,
) -> dict:
    safe_days = min(max(days, 28), 366)
    tz = _reading_timezone()
    timezone_name = getattr(tz, "key", settings.hf_daily.timezone)
    local_now = now.astimezone(tz) if now is not None else datetime.now(tz)
    today = local_now.date()
    tomorrow_start = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    activity_end_utc = tomorrow_start.astimezone(timezone.utc)

    def operation() -> dict:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        (first_viewed_at AT TIME ZONE %s)::date AS activity_date,
                        COUNT(*) AS paper_count
                    FROM paper_marks
                    WHERE user_id = %s
                      AND first_viewed_at IS NOT NULL
                      AND first_viewed_at < %s
                    GROUP BY 1
                    ORDER BY 1
                    """,
                    (timezone_name, user_id, activity_end_utc),
                )
                activity_rows = cur.fetchall()

        activity = _build_reading_activity(activity_rows, today, safe_days)

        return {
            "timezone": timezone_name,
            "activity": activity,
        }

    return _run_with_retry(operation, f"get_reading_overview:{user_id}:{safe_days}")


def get_paper_marks(user_id: str, paper_ids: list[str]) -> dict[str, dict]:
    if not paper_ids:
        return {}

    def operation() -> dict[str, dict]:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT paper_id, viewed, liked, favorited,
                           first_viewed_at, viewed_at, liked_at, favorited_at,
                           last_opened_at, note, updated_at
                    FROM paper_marks
                    WHERE user_id = %s AND paper_id = ANY(%s)
                    """,
                    (user_id, paper_ids),
                )
                rows = cur.fetchall()
        return {
            row["paper_id"]: {
                "viewed": bool(row["viewed"]),
                "liked": bool(row["liked"]),
                "favorited": bool(row["favorited"]),
                "first_viewed_at": row["first_viewed_at"],
                "viewed_at": row["viewed_at"],
                "liked_at": row["liked_at"],
                "favorited_at": row["favorited_at"],
                "last_opened_at": row.get("last_opened_at"),
                "note": row.get("note"),
                "updated_at": row["updated_at"],
            }
            for row in rows
        }

    return _run_with_retry(operation, f"get_paper_marks:{user_id}")


def _load_keywords_for_papers(papers: list[dict]) -> tuple[list[dict], bool]:
    """Attach the keywords list to each paper dict; returns (papers, ok)."""
    if not papers:
        return papers, True
    with _get_connection() as conn:
        keyword_map = _fetch_keywords_for_papers(conn, [p["id"] for p in papers])
    for paper in papers:
        paper["keywords"] = keyword_map.get(paper["id"], [])
    return papers, True


def list_marked_papers(
    user_id: str,
    mark_filter: str,
    sort: str,
    offset: int,
    limit: int,
    search: str | None = None,
    category: str | None = None,
) -> tuple[list[dict], int]:
    filter_clauses = {
        # "all" = everything in the library, including added-but-never-opened
        # papers (a mark row with all flags false — created by adding a paper
        # or by a passive detail-page open).
        "all": "TRUE",
        "viewed": "pm.viewed = TRUE",
        "liked": "pm.liked = TRUE",
        "favorited": "pm.favorited = TRUE",
    }
    sort_clauses = {
        # 手动「已看过」时间；passive opens 不在此列（见 opened_at）。
        "viewed_at": "pm.viewed_at DESC NULLS LAST, pm.updated_at DESC",
        "liked_at": "pm.liked_at DESC NULLS LAST, pm.updated_at DESC",
        "favorited_first": "pm.favorited DESC, pm.favorited_at DESC NULLS LAST, pm.liked_at DESC NULLS LAST, pm.viewed_at DESC NULLS LAST, pm.updated_at DESC",
        "liked_first": "pm.favorited DESC, pm.favorited_at DESC NULLS LAST, pm.liked_at DESC NULLS LAST, pm.viewed_at DESC NULLS LAST, pm.updated_at DESC",
        # 最近一次点进详情并停留（last_opened_at）；与手动 viewed_at 区分。
        "opened_at": "pm.last_opened_at DESC NULLS LAST, pm.updated_at DESC",
        "title": "LOWER(p.title) ASC NULLS LAST",
    }
    where_clause = filter_clauses.get(mark_filter, filter_clauses["all"])
    order_clause = sort_clauses.get(sort, sort_clauses["viewed_at"])

    search_term = (search or "").strip()
    search_clause = ""
    search_params: list[str] = []
    if search_term:
        like = f"%{search_term}%"
        search_clause = " AND (p.title ILIKE %s OR p.abstract ILIKE %s OR p.keywords::text ILIKE %s)"
        search_params = [like, like, like]

    # Category filter: "uncategorized" = no assignment at all; a category id
    # (validated upstream) = assigned anywhere in that category's subtree.
    category_clause = ""
    category_params: list[str] = []
    if category == "uncategorized":
        category_clause = """
          AND NOT EXISTS (
              SELECT 1 FROM paper_category_assignments pca
              WHERE pca.user_id = pm.user_id AND pca.paper_id = pm.paper_id
          )"""
    elif category:
        category_clause = """
          AND pm.paper_id IN (
              SELECT pca.paper_id
              FROM paper_category_assignments pca
              WHERE pca.user_id = %s AND pca.category_id IN (
                  WITH RECURSIVE subtree AS (
                      SELECT id FROM paper_categories WHERE user_id = %s AND id = %s
                      UNION ALL
                      SELECT c.id FROM paper_categories c JOIN subtree s ON c.parent_id = s.id
                  )
                  SELECT id FROM subtree
              )
          )"""
        category_params = [user_id, user_id, category]

    def operation() -> tuple[list[dict], int]:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                        p.id,
                        p.title,
                        p.abstract,
                        p.keywords,
                        p.pdf,
                        p.venue,
                        p.primary_area,
                        p.published_year,
                        p.llm_response,
                        p.created_at,
                        (
                            SELECT ap.published_at
                            FROM arxiv_papers ap
                            WHERE ap.paper_id = p.id
                        ) AS published_at,
                        pm.viewed,
                        pm.liked,
                        pm.favorited,
                        COALESCE(
                            (SELECT jsonb_agg(jsonb_build_object('id', pca_c.id, 'name', pca_c.name) ORDER BY pca_c.name)
                             FROM paper_category_assignments pca
                             JOIN paper_categories pca_c ON pca_c.id = pca.category_id
                             WHERE pca.user_id = pm.user_id AND pca.paper_id = pm.paper_id),
                            '[]'::jsonb
                        ) AS categories,
                        COALESCE(
                            (SELECT jsonb_agg(jsonb_build_object('name', a.author_name) ORDER BY a.author_order)
                             FROM authors a WHERE a.paper_id = p.id),
                            '[]'::jsonb
                        ) AS authors,
                        pm.first_viewed_at,
                        pm.viewed_at,
                        pm.liked_at,
                        pm.favorited_at,
                        pm.last_opened_at,
                        pm.note,
                        pm.updated_at AS mark_updated_at
                    FROM paper_marks pm
                    JOIN papers p ON p.id = pm.paper_id
                    WHERE pm.user_id = %s AND {where_clause}{search_clause}{category_clause}
                    ORDER BY {order_clause}, p.id ASC
                    LIMIT %s OFFSET %s
                    """,
                    (user_id, *search_params, *category_params, limit, offset),
                )
                rows = cur.fetchall()

                cur.execute(
                    f"""
                    SELECT COUNT(*) AS total
                    FROM paper_marks pm
                    JOIN papers p ON p.id = pm.paper_id
                    WHERE pm.user_id = %s AND {where_clause}{search_clause}{category_clause}
                    """,
                    (user_id, *search_params, *category_params),
                )
                total = int((cur.fetchone() or {}).get("total") or 0)

        papers = [
            {
                "id": row["id"],
                "title": row.get("title"),
                "abstract": row.get("abstract"),
                "keywords": row.get("keywords") or [],
                "authors": [a["name"] for a in (row.get("authors") or [])],
                "pdf": _paper_pdf_for_output(row["id"], row.get("pdf")),
                "venue": row.get("venue"),
                "primary_area": row.get("primary_area"),
                "published_year": row.get("published_year"),
                "llm_response": row.get("llm_response"),
                "created_at": row.get("created_at"),
                "published_at": row.get("published_at"),
            }
            for row in rows
        ]
        papers, _ = _load_keywords_for_papers(papers)

        items = [
            {
                "paper": paper,
                "mark": {
                    "viewed": bool(row["viewed"]),
                    "liked": bool(row["liked"]),
                    "favorited": bool(row["favorited"]),
                    "categories": row.get("categories") or [],
                    "first_viewed_at": row["first_viewed_at"],
                    "viewed_at": row["viewed_at"],
                    "liked_at": row["liked_at"],
                    "favorited_at": row["favorited_at"],
                    "last_opened_at": row.get("last_opened_at"),
                    "note": row.get("note"),
                    "updated_at": row["mark_updated_at"],
                },
            }
            for paper, row in zip(papers, rows)
        ]
        return items, total

    return _run_with_retry(operation, f"list_marked_papers:{user_id}:{mark_filter}:{sort}")


def set_paper_mark(
    user_id: str,
    paper_id: str,
    viewed: bool | None = None,
    liked: bool | None = None,
    favorited: bool | None = None,
) -> dict:
    """Write the manual mark flags (viewed / liked / favorited).

    "viewed" is the user's deliberate "I have read this" mark — only set it
    from explicit user actions (the detail-page button, like/favorite
    implying read). Passive opens are recorded by record_paper_opened instead
    and never touch these flags.
    """
    def operation() -> dict:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT viewed, liked, favorited
                    FROM paper_marks
                    WHERE user_id = %s AND paper_id = %s
                    """,
                    (user_id, paper_id),
                )
                existing = cur.fetchone() or {"viewed": False, "liked": False, "favorited": False}
                next_viewed = bool(existing["viewed"]) if viewed is None else viewed
                next_liked = bool(existing["liked"]) if liked is None else liked
                next_favorited = bool(existing["favorited"]) if favorited is None else favorited
                if next_liked or next_favorited:
                    next_viewed = True
                if not next_viewed:
                    next_liked = False
                    next_favorited = False

                cur.execute(
                    """
                    INSERT INTO paper_marks (
                        user_id, paper_id, viewed, liked, favorited,
                        first_viewed_at, viewed_at, liked_at, favorited_at, updated_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s,
                        CASE WHEN %s THEN NOW() ELSE NULL END,
                        CASE WHEN %s THEN NOW() ELSE NULL END,
                        CASE WHEN %s THEN NOW() ELSE NULL END,
                        CASE WHEN %s THEN NOW() ELSE NULL END,
                        NOW()
                    )
                    ON CONFLICT (user_id, paper_id) DO UPDATE SET
                        viewed = EXCLUDED.viewed,
                        liked = EXCLUDED.liked,
                        favorited = EXCLUDED.favorited,
                        first_viewed_at = COALESCE(paper_marks.first_viewed_at, EXCLUDED.first_viewed_at),
                        viewed_at = CASE
                            WHEN EXCLUDED.viewed THEN COALESCE(paper_marks.viewed_at, NOW())
                            ELSE NULL
                        END,
                        liked_at = CASE
                            WHEN EXCLUDED.liked THEN COALESCE(paper_marks.liked_at, NOW())
                            ELSE NULL
                        END,
                        favorited_at = CASE
                            WHEN EXCLUDED.favorited THEN COALESCE(paper_marks.favorited_at, NOW())
                            ELSE NULL
                        END,
                        updated_at = NOW()
                    RETURNING paper_id, viewed, liked, favorited,
                              first_viewed_at, viewed_at, liked_at, favorited_at,
                              last_opened_at, updated_at
                    """,
                    (
                        user_id,
                        paper_id,
                        next_viewed,
                        next_liked,
                        next_favorited,
                        next_viewed,
                        next_viewed,
                        next_liked,
                        next_favorited,
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        return {
            "paper_id": row["paper_id"],
            "viewed": bool(row["viewed"]),
            "liked": bool(row["liked"]),
            "favorited": bool(row["favorited"]),
            "first_viewed_at": row["first_viewed_at"],
            "viewed_at": row["viewed_at"],
            "liked_at": row["liked_at"],
            "favorited_at": row["favorited_at"],
            "last_opened_at": row.get("last_opened_at"),
            "updated_at": row["updated_at"],
        }

    return _run_with_retry(operation, f"set_paper_mark:{user_id}:{paper_id}")


def record_paper_opened(user_id: str, paper_id: str) -> dict:
    """Record a qualifying detail-page open (clicked in and stayed).

    Only refreshes last_opened_at and ensures the row exists — never touches
    the manual viewed/liked/favorited flags. Creates the mark row if absent
    so the paper exists in marks (but stays "not viewed"); the all-filter in
    list_marked_papers is updated separately to include these rows.
    """
    def operation() -> dict:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO paper_marks (
                        user_id, paper_id, viewed, liked, favorited,
                        last_opened_at, created_at, updated_at
                    )
                    VALUES (%s, %s, FALSE, FALSE, FALSE, NOW(), NOW(), NOW())
                    ON CONFLICT (user_id, paper_id) DO UPDATE SET
                        last_opened_at = NOW(),
                        updated_at = NOW()
                    RETURNING paper_id, viewed, liked, favorited,
                              first_viewed_at, viewed_at, liked_at, favorited_at,
                              last_opened_at, updated_at
                    """,
                    (user_id, paper_id),
                )
                row = cur.fetchone()
            conn.commit()
        return {
            "paper_id": row["paper_id"],
            "viewed": bool(row["viewed"]),
            "liked": bool(row["liked"]),
            "favorited": bool(row["favorited"]),
            "first_viewed_at": row["first_viewed_at"],
            "viewed_at": row["viewed_at"],
            "liked_at": row["liked_at"],
            "favorited_at": row["favorited_at"],
            "last_opened_at": row["last_opened_at"],
            "updated_at": row["updated_at"],
        }

    return _run_with_retry(operation, f"record_paper_opened:{user_id}:{paper_id}")


def delete_paper_mark(user_id: str, paper_id: str) -> bool:
    """Delete a user's mark row for a paper (removes it from their My Papers)."""
    def operation() -> bool:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM paper_marks WHERE user_id = %s AND paper_id = %s",
                    (user_id, paper_id),
                )
            conn.commit()
        return True

    return _run_with_retry(operation, f"delete_paper_mark:{user_id}:{paper_id}")


def get_export_snapshot(
    user_id: str,
    paper_ids: list[str] | None = None,
) -> list[dict]:
    """Snapshot of marked papers for library export (no llm payloads trimmed).

    Each item carries the paper row (incl. llm_response/abstract_zh/created_at),
    authors, keywords (from the keywords table — the UI-facing source),
    arxiv meta, the user's mark, and the user's category assignment ids.
    paper_ids=None means every marked paper.
    """
    def operation() -> list[dict]:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                if paper_ids is None:
                    cur.execute(
                        """
                        SELECT
                            p.id, p.title, p.abstract, p.keywords AS keywords_jsonb,
                            p.pdf, p.venue, p.primary_area, p.llm_response,
                            p.abstract_zh, p.created_at,
                            pm.viewed, pm.liked, pm.favorited,
                            pm.first_viewed_at, pm.viewed_at, pm.liked_at,
                            pm.favorited_at, pm.last_opened_at, pm.note, pm.updated_at
                        FROM paper_marks pm
                        JOIN papers p ON p.id = pm.paper_id
                        WHERE pm.user_id = %s
                        ORDER BY pm.updated_at DESC, p.id ASC
                        """,
                        (user_id,),
                    )
                else:
                    if not paper_ids:
                        return []
                    cur.execute(
                        """
                        SELECT
                            p.id, p.title, p.abstract, p.keywords AS keywords_jsonb,
                            p.pdf, p.venue, p.primary_area, p.llm_response,
                            p.abstract_zh, p.created_at,
                            pm.viewed, pm.liked, pm.favorited,
                            pm.first_viewed_at, pm.viewed_at, pm.liked_at,
                            pm.favorited_at, pm.last_opened_at, pm.note, pm.updated_at
                        FROM paper_marks pm
                        JOIN papers p ON p.id = pm.paper_id
                        WHERE pm.user_id = %s AND p.id = ANY(%s)
                        ORDER BY pm.updated_at DESC, p.id ASC
                        """,
                        (user_id, list(paper_ids)),
                    )
                papers = [dict(row) for row in cur.fetchall()]
                if not papers:
                    return []

                paper_id_list = [p["id"] for p in papers]

                cur.execute(
                    """
                    SELECT paper_id, author_name
                    FROM authors
                    WHERE paper_id = ANY(%s)
                    ORDER BY paper_id, author_order
                    """,
                    (paper_id_list,),
                )
                authors_by_paper: dict[str, list[str]] = {}
                for row in cur.fetchall():
                    authors_by_paper.setdefault(row["paper_id"], []).append(row["author_name"])

                keyword_map = _fetch_keywords_for_papers(conn, paper_id_list)

                cur.execute(
                    """
                    SELECT paper_id, arxiv_id, arxiv_url, pdf_url,
                           published_at, arxiv_updated_at, added_at, metadata
                    FROM arxiv_papers
                    WHERE paper_id = ANY(%s)
                    """,
                    (paper_id_list,),
                )
                arxiv_by_paper = {row["paper_id"]: dict(row) for row in cur.fetchall()}

                cur.execute(
                    """
                    SELECT paper_id, category_id
                    FROM paper_category_assignments
                    WHERE user_id = %s AND paper_id = ANY(%s)
                    """,
                    (user_id, paper_id_list),
                )
                categories_by_paper: dict[str, list[str]] = {}
                for row in cur.fetchall():
                    categories_by_paper.setdefault(row["paper_id"], []).append(str(row["category_id"]))

        for paper in papers:
            paper_id = paper["id"]
            paper["authors"] = authors_by_paper.get(paper_id, [])
            paper["keywords"] = keyword_map.get(paper_id, [])
            arxiv_row = arxiv_by_paper.get(paper_id)
            paper["arxiv"] = _arxiv_meta_from_row(arxiv_row) if arxiv_row else None
            paper["category_ids"] = categories_by_paper.get(paper_id, [])
        return papers

    scope = "all" if paper_ids is None else str(len(paper_ids))
    return _run_with_retry(operation, f"get_export_snapshot:{user_id}:{scope}")


def clear_paper_analysis_cache(paper_id: str) -> None:
    """Clear the paper's computed analysis caches (LLM response + code availability)
    so the next view re-analyzes from scratch. Paper metadata stays intact.
    """
    def operation() -> None:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE papers
                    SET llm_response = NULL,
                        code_status = 'unknown',
                        code_url = NULL,
                        code_evidence = NULL,
                        code_meta = '{}'::jsonb,
                        code_checked_at = NULL
                    WHERE id = %s
                    """,
                    (paper_id,),
                )
            conn.commit()

    _run_with_retry(operation, f"clear_paper_analysis_cache:{paper_id}")


def get_abstract_zh_map(paper_ids: list[str]) -> dict[str, str | None]:
    """Return {paper_id: abstract_zh_or_None} for the given papers (cached translations only)."""
    if not paper_ids:
        return {}
    unique_ids = list({pid for pid in paper_ids if pid})

    def operation() -> dict[str, str | None]:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, abstract_zh FROM papers WHERE id = ANY(%s)",
                    (unique_ids,),
                )
                return {row["id"]: row["abstract_zh"] for row in cur.fetchall()}

    return _run_with_retry(operation, "get_abstract_zh_map")


def set_abstract_zh(paper_id: str, abstract_zh: str) -> None:
    """Cache a Chinese translation of the paper's abstract."""
    def operation() -> None:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE papers
                    SET abstract_zh = %s, abstract_zh_at = NOW()
                    WHERE id = %s
                    """,
                    (abstract_zh, paper_id),
                )
            conn.commit()

    _run_with_retry(operation, f"set_abstract_zh:{paper_id}")


def get_chat_sessions_for_account(account_user_id: str, paper_id: str) -> list:
    if not DATABASE_URL:
        return []

    def operation() -> list:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM chat_sessions
                    WHERE account_user_id = %s AND paper_id = %s
                    ORDER BY created_at DESC
                    """,
                    (account_user_id, paper_id),
                )
                return [_normalize_session_row(row) for row in cur.fetchall()]

    return _run_with_retry(operation, f"get_chat_sessions_for_account:{account_user_id}:{paper_id}")


def get_chat_session(session_id: str) -> dict | None:
    if not DATABASE_URL:
        return None

    def operation() -> dict | None:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM chat_sessions WHERE id = %s", (session_id,))
                return _normalize_session_row(cur.fetchone())

    return _run_with_retry(operation, f"get_chat_session:{session_id}")


def create_chat_session(
    session_id: str,
    user_id: str,
    paper_id: str,
    title: str,
    account_user_id: str | None = None,
):
    if not DATABASE_URL:
        return

    def operation() -> None:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO chat_sessions (id, user_id, paper_id, title, account_user_id)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (session_id, user_id, paper_id, title, account_user_id),
                )
            conn.commit()

    _run_with_retry(operation, f"create_chat_session:{session_id}")


def get_chat_messages(session_id: str) -> list:
    if not DATABASE_URL:
        return []

    def operation() -> list:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT role, content, created_at
                    FROM chat_messages
                    WHERE session_id = %s
                    ORDER BY created_at
                    """,
                    (session_id,),
                )
                return cur.fetchall()

    return _run_with_retry(operation, f"get_chat_messages:{session_id}")


def save_chat_message(session_id: str, role: str, content: str):
    if not DATABASE_URL:
        return

    def operation() -> None:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO chat_messages (session_id, role, content)
                    VALUES (%s, %s, %s)
                    """,
                    (session_id, role, content),
                )
            conn.commit()

    _run_with_retry(operation, f"save_chat_message:{session_id}:{role}")


def delete_chat_session(session_id: str):
    if not DATABASE_URL:
        return

    def operation() -> None:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM chat_messages WHERE session_id = %s", (session_id,))
                cur.execute("DELETE FROM chat_sessions WHERE id = %s", (session_id,))
            conn.commit()

    _run_with_retry(operation, f"delete_chat_session:{session_id}")


def delete_last_chat_message_pair(session_id: str):
    """Delete the last user+assistant message pair from a session."""
    if not DATABASE_URL:
        return

    def operation() -> None:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id
                    FROM chat_messages
                    WHERE session_id = %s
                    ORDER BY created_at DESC
                    LIMIT 2
                    """,
                    (session_id,),
                )
                rows = cur.fetchall()
                if rows:
                    cur.execute(
                        "DELETE FROM chat_messages WHERE id = ANY(%s)",
                        ([row["id"] for row in rows],),
                    )
            conn.commit()

    _run_with_retry(operation, f"delete_last_chat_message_pair:{session_id}")


