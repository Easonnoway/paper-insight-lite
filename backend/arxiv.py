import html as html_module
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any
from urllib.parse import unquote, urlparse

import requests

logger = logging.getLogger(__name__)

ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_ABS_URL_PREFIX = "https://arxiv.org/abs/"
ARXIV_PDF_URL_PREFIX = "https://arxiv.org/pdf/"
ARXIV_TIMEOUT_SECONDS = 15
MAX_RETRIES = 3
ARXIV_RATE_LIMIT_WAIT_SECONDS = 15

ATOM_NS = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"

_CITATION_META_PATTERN = re.compile(
    r'name="citation_(?P<key>[a-z_]+)"\s+content="(?P<value>[^"]*)"',
    re.IGNORECASE,
)

_MODERN_ARXIV_ID_PATTERN = re.compile(
    r"^(?:arxiv:)?(?P<id>\d{4}\.\d{4,5})(?:v\d+)?$",
    re.IGNORECASE,
)
_LEGACY_ARXIV_ID_PATTERN = re.compile(
    r"^(?:arxiv:)?(?P<id>[a-z-]+(?:\.[a-z-]+)?/\d{7})(?:v\d+)?$",
    re.IGNORECASE,
)


class ArxivError(Exception):
    """Base arXiv integration error."""


class ArxivInvalidInputError(ArxivError):
    """The user input is not an arXiv URL or ID."""


class ArxivNotFoundError(ArxivError):
    """The arXiv API did not return article metadata."""


def _collapse_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _candidate_from_url(raw_value: str) -> str | None:
    parsed = urlparse(raw_value)
    host = parsed.netloc.casefold()
    if not host or not (host == "arxiv.org" or host.endswith(".arxiv.org")):
        return None

    parts = [unquote(part) for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2 or parts[0] not in {"abs", "pdf", "html", "e-print"}:
        return None

    candidate = "/".join(parts[1:])
    if candidate.endswith(".pdf"):
        candidate = candidate[:-4]
    return candidate


def normalize_arxiv_id(raw_value: str) -> str | None:
    value = raw_value.strip()
    if not value:
        return None

    candidate = _candidate_from_url(value) or value
    candidate = candidate.strip()
    if candidate.casefold().startswith("arxiv:"):
        candidate = candidate.split(":", 1)[1].strip()
    if candidate.endswith(".pdf"):
        candidate = candidate[:-4]

    for pattern in (_MODERN_ARXIV_ID_PATTERN, _LEGACY_ARXIV_ID_PATTERN):
        match = pattern.match(candidate)
        if match:
            return match.group("id").casefold()

    return None


def extract_arxiv_id(raw_value: str) -> str:
    arxiv_id = normalize_arxiv_id(raw_value)
    if not arxiv_id:
        raise ArxivInvalidInputError("请输入有效的 arXiv 链接或 ID")
    return arxiv_id


def build_arxiv_paper_id(arxiv_id: str) -> str:
    return f"arxiv:{arxiv_id.replace('/', '_')}"


def arxiv_id_from_paper_id(paper_id: str) -> str | None:
    if not paper_id.startswith("arxiv:"):
        return None
    return normalize_arxiv_id(paper_id.removeprefix("arxiv:").replace("_", "/"))


def build_arxiv_abs_url(arxiv_id: str) -> str:
    return f"{ARXIV_ABS_URL_PREFIX}{arxiv_id}"


def build_arxiv_pdf_url(arxiv_id: str) -> str:
    return f"{ARXIV_PDF_URL_PREFIX}{arxiv_id}"


def _entry_text(entry: ET.Element, tag: str) -> str:
    element = entry.find(f"{ATOM_NS}{tag}")
    return _collapse_text(element.text if element is not None else None)


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("无法解析 arXiv 时间戳: %s", value)
        return None


def _entry_arxiv_id(entry: ET.Element) -> str | None:
    entry_id = _entry_text(entry, "id")
    if not entry_id:
        return None
    parsed = urlparse(entry_id)
    if parsed.path.startswith("/abs/"):
        return normalize_arxiv_id(unquote(parsed.path.removeprefix("/abs/")))
    return normalize_arxiv_id(entry_id)


def _entry_pdf_url(entry: ET.Element, arxiv_id: str) -> str:
    for link in entry.findall(f"{ATOM_NS}link"):
        if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf":
            href = link.attrib.get("href")
            if href:
                return href
    return build_arxiv_pdf_url(arxiv_id)


def _entry_categories(entry: ET.Element) -> list[str]:
    categories: list[str] = []
    for category in entry.findall(f"{ATOM_NS}category"):
        term = _collapse_text(category.attrib.get("term"))
        if term and term not in categories:
            categories.append(term)
    return categories


def _entry_authors(entry: ET.Element) -> list[str]:
    authors: list[str] = []
    for author in entry.findall(f"{ATOM_NS}author"):
        name = _collapse_text(author.findtext(f"{ATOM_NS}name"))
        if name:
            authors.append(name)
    return authors


def _entry_optional_text(entry: ET.Element, tag: str) -> str | None:
    value = _collapse_text(entry.findtext(f"{ARXIV_NS}{tag}"))
    return value or None


def _normalize_entry(entry: ET.Element, requested_arxiv_id: str) -> dict[str, Any]:
    title = _entry_text(entry, "title")
    if title == "Error":
        summary = _entry_text(entry, "summary")
        raise ArxivNotFoundError(summary or f"arXiv paper not found: {requested_arxiv_id}")

    arxiv_id = _entry_arxiv_id(entry) or requested_arxiv_id
    paper_id = build_arxiv_paper_id(arxiv_id)
    abstract = _entry_text(entry, "summary")
    categories = _entry_categories(entry)
    primary_category = entry.find(f"{ARXIV_NS}primary_category")
    primary_area = _collapse_text(primary_category.attrib.get("term")) if primary_category is not None else None
    published_raw = _entry_text(entry, "published")
    updated_raw = _entry_text(entry, "updated")
    pdf_url = _entry_pdf_url(entry, arxiv_id)

    return {
        "paper": {
            "id": paper_id,
            "title": title,
            "abstract": abstract,
            "authors": _entry_authors(entry),
            "keywords": categories,
            "pdf": pdf_url,
            "venue": "arXiv",
            "primary_area": primary_area,
        },
        "arxiv": {
            "arxiv_id": arxiv_id,
            "arxiv_url": build_arxiv_abs_url(arxiv_id),
            "pdf_url": pdf_url,
            "published_at": _parse_datetime(published_raw),
            "updated_at": _parse_datetime(updated_raw),
            "primary_category": primary_area,
            "categories": categories,
            "comment": _entry_optional_text(entry, "comment"),
            "journal_ref": _entry_optional_text(entry, "journal_ref"),
            "doi": _entry_optional_text(entry, "doi"),
            "raw": {
                "entry_id": _entry_text(entry, "id"),
                "published": published_raw,
                "updated": updated_raw,
                "categories": categories,
                "primary_category": primary_area,
            },
        },
    }


def parse_arxiv_api_response(payload: str, requested_arxiv_id: str) -> dict[str, Any]:
    root = ET.fromstring(payload)
    entry = root.find(f"{ATOM_NS}entry")
    if entry is None:
        raise ArxivNotFoundError(f"arXiv paper not found: {requested_arxiv_id}")
    return _normalize_entry(entry, requested_arxiv_id)


def fetch_arxiv_paper(raw_value: str) -> dict[str, Any]:
    arxiv_id = extract_arxiv_id(raw_value)
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(
                ARXIV_API_URL,
                params={"id_list": arxiv_id, "max_results": "1"},
                timeout=ARXIV_TIMEOUT_SECONDS,
                headers={"Accept": "application/atom+xml", "User-Agent": "Paper Insight/1.0"},
            )
            if response.status_code == 429:
                # Rate limited — retrying just burns the user's time and
                # refreshes the limiter's window. The /abs/ page (different
                # rate-limit policy) is the fallback, so go there now.
                logger.warning("arXiv API 限流 (429)，降级抓取 abs 页面: %s", arxiv_id)
                break
            response.raise_for_status()
            return parse_arxiv_api_response(response.text, arxiv_id)
        except requests.RequestException as exc:
            last_error = exc
            # 超时/连接失败几乎总是限流丢包，重试也是 hang——立刻降级。
            logger.warning("arXiv API 不可用 (%s): %s", type(exc).__name__, arxiv_id)
            break

    # API 被限流/丢包时，abs 网页通常仍可访问（不同限流策略）——降级抓元数据。
    logger.warning("arXiv API 不可用，降级抓取 abs 页面: %s", arxiv_id)
    try:
        return fetch_arxiv_paper_from_abs_page(arxiv_id)
    except (ArxivNotFoundError, ArxivInvalidInputError):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("abs 页面降级也失败: %s (%s)", arxiv_id, exc)
        raise ArxivError(
            f"arXiv API 暂不可用（限流中），且降级抓取失败，请稍后再试: {arxiv_id}"
        ) from (exc or last_error)


def _citation_meta_map(page_html: str) -> dict[str, list[str]]:
    """Extract all citation_* meta tags; repeated keys (e.g. citation_author)
    collect into ordered lists."""
    values: dict[str, list[str]] = {}
    for match in _CITATION_META_PATTERN.finditer(page_html):
        key = match.group("key").lower()
        value = html_module.unescape(match.group("value")).strip()
        if value:
            values.setdefault(key, []).append(value)
    return values


def fetch_arxiv_paper_from_abs_page(arxiv_id: str) -> dict[str, Any]:
    """Fallback when the export API is rate-limited: scrape citation_ meta
    tags from the paper's /abs/ page. Returns the same payload shape as
    parse_arxiv_api_response (minus API-only fields like comment/doi)."""
    response = requests.get(
        build_arxiv_abs_url(arxiv_id),
        timeout=ARXIV_TIMEOUT_SECONDS,
        headers={"User-Agent": "Paper Insight/1.0"},
    )
    response.raise_for_status()
    if "No paper found" in response.text or response.status_code == 404:
        raise ArxivNotFoundError(f"arXiv paper not found: {arxiv_id}")

    meta = _citation_meta_map(response.text)
    scraped_id = (meta.get("arxiv_id") or [arxiv_id])[0]
    normalized_id = normalize_arxiv_id(scraped_id) or arxiv_id
    titles = meta.get("title")
    if not titles:
        raise ArxivNotFoundError(f"arXiv 页面解析失败（无标题）: {arxiv_id}")

    title = html_module.unescape(titles[0])
    abstract = " ".join(meta.get("abstract", [])) or None
    authors = meta.get("author", [])
    date_raw = (meta.get("online_date") or meta.get("date") or [None])[0]
    published_at = None
    if date_raw:
        for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
            try:
                published_at = datetime.strptime(date_raw, fmt)
                break
            except ValueError:
                continue
    pdf_url = (meta.get("pdf_url") or [build_arxiv_pdf_url(normalized_id)])[0]

    return {
        "paper": {
            "id": build_arxiv_paper_id(normalized_id),
            "title": title,
            "abstract": abstract,
            "authors": authors,
            "keywords": [],
            "pdf": pdf_url,
            "venue": "arXiv",
            "primary_area": None,
        },
        "arxiv": {
            "arxiv_id": normalized_id,
            "arxiv_url": build_arxiv_abs_url(normalized_id),
            "pdf_url": pdf_url,
            "published_at": published_at,
            "updated_at": None,
            "primary_category": None,
            "categories": [],
            "comment": None,
            "journal_ref": None,
            "doi": None,
            "raw": {"source": "abs_page_fallback", "citation_date": date_raw},
        },
    }
