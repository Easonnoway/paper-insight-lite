import asyncio
import json
import math
import logging
import os
import re
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timezone
import contextlib
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from sse_starlette.sse import EventSourceResponse

from pydantic import BaseModel, Field, field_validator

from auth import hash_password, normalize_email
from config import settings
from llm import ManagedLLM
from migrations import apply_migrations
from analysis_context import build_analysis_prompt, build_chat_context_parts
from prompt import build_open_in_ai_prompt
from translation import translate_abstract
from utils import get_or_cache_paper_content, ReaderError, OpenReviewError, truncate_content_for_llm, delete_paper_content_cache
from arxiv import (
    ArxivError,
    ArxivInvalidInputError,
    ArxivNotFoundError,
    arxiv_id_from_paper_id,
    fetch_arxiv_paper,
)
from database import (
    DatabaseError,
    LlmProviderConflict,
    create_llm_provider,
    create_chat_session,
    delete_chat_session,
    delete_last_chat_message_pair,
    ensure_admin_user,
    ensure_default_llm_providers,
    get_chat_messages,
    get_chat_session,
    get_chat_sessions_for_account,
    get_active_llm_config,
    get_paper,
    get_papers_by_ids,
    get_paper_marks,
    get_reading_overview,
    get_user_by_email,
    list_llm_providers,
    list_marked_papers,
    record_paper_opened,
    save_chat_message,
    set_active_llm_provider,
    update_llm_provider,
    update_llm_provider_api_key,
    set_paper_mark,
    delete_paper_mark,
    clear_paper_analysis_cache,
    get_abstract_zh_map,
    set_abstract_zh,
    update_llm_response,
    upsert_arxiv_paper,
    upsert_manual_paper,
)
from chat import ChatSession
from background_tasks import BackgroundAnalyzer
from markdown_utils import normalize_llm_markdown
import auto_backup
import library_transfer
from library_transfer import PayloadTooLargeError
from paper_notes import MAX_NOTE_LENGTH, set_paper_note
from paper_categories import (
    assign_paper_category,
    clear_paper_categories,
    clear_user_paper_categories,
    create_paper_category,
    delete_paper_category,
    list_paper_categories,
    move_paper_category,
    rename_paper_category,
    set_paper_category_color,
    unassign_paper_category,
    validate_uuid as validate_category_uuid,
)

logger = logging.getLogger(__name__)

llm = ManagedLLM()
chat_sessions: dict[str, ChatSession] = {}
# Kept as a plain instance (no scheduler loop): the analysis SSE endpoint
# calls update_code_availability inline.
background_analyzer = BackgroundAnalyzer(llm, check_interval=settings.background_analysis.check_interval_seconds)
_backup_loop_task: asyncio.Task | None = None

def bootstrap_admin_user() -> None:
    if not settings.admin.email or not settings.admin.initial_password:
        logger.info("未配置 admin.email/admin.initial_password，跳过初始管理员创建")
        return

    normalized = normalize_email(settings.admin.email)
    ensure_admin_user(
        settings.admin.email.strip(),
        normalized,
        hash_password(settings.admin.initial_password),
    )
    logger.info("初始管理员已确认: %s", normalized)


def bootstrap_llm_providers() -> None:
    # Seed all builtin providers (even keyless ones, as is_enabled=False) so a
    # fresh install has the full list to edit from the settings dialog — no
    # config.yaml edit or restart needed. The auto-selected active provider
    # must have a key (guarded in ensure_default_llm_providers).
    candidates = [
            {
                "provider_key": "step",
                "name": "Step",
                "base_url": settings.llm.step_base_url,
                "api_key": settings.llm.step_api_key,
                "active_model": "step-3.5-flash-2603",
                "models": ["step-3.5-flash-2603"],
            },
            {
                "provider_key": "openrouter",
                "name": "OpenRouter",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": settings.llm.open_router_api_key,
                "active_model": "stepfun/step-3.5-flash:free",
                "models": ["stepfun/step-3.5-flash:free"],
                "default_parameters": {"max_completion_tokens": 12000},
            },
            {
                "provider_key": "siliconflow",
                "name": "SiliconFlow",
                "base_url": "https://api.siliconflow.cn/v1",
                "api_key": settings.llm.siliconflow_api_key,
                "active_model": "Pro/MiniMaxAI/MiniMax-M2.5",
                "models": ["Pro/MiniMaxAI/MiniMax-M2.5"],
            },
            {
                "provider_key": "arkplan",
                "name": "ArkPlan",
                "base_url": "https://ark.cn-beijing.volces.com/api/coding/v3",
                "api_key": settings.llm.arkplan_api_key,
                "active_model": "ark-code-latest",
                "models": ["ark-code-latest"],
            },
            {
                "provider_key": "openai",
                "name": "OpenAI",
                "base_url": "https://api.openai.com/v1",
                "api_key": settings.llm.openai_api_key,
                "active_model": "gpt-4.1-mini",
                "models": ["gpt-4.1-mini"],
            },
            {
                "provider_key": "deepseek",
                "name": "DeepSeek",
                "base_url": "https://api.deepseek.com",
                "api_key": settings.llm.deepseek_api_key,
                "active_model": "deepseek-chat",
                "models": ["deepseek-chat", "deepseek-reasoner"],
            },
    ]
    ensure_default_llm_providers(candidates)
    logger.info("LLM 供应商配置已确认")


def ensure_llm_configured() -> None:
    if not llm.is_configured():
        raise HTTPException(status_code=503, detail="当前 LLM 供应商、模型或 API Key 未配置")

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await asyncio.to_thread(apply_migrations)
    except Exception as exc:
        logger.error("数据库 migration 失败: %s", exc)
        raise

    try:
        await asyncio.to_thread(bootstrap_admin_user)
    except DatabaseError as exc:
        logger.warning("初始管理员创建失败: %s", exc)

    try:
        await asyncio.to_thread(bootstrap_llm_providers)
    except DatabaseError as exc:
        logger.warning("LLM 供应商初始化失败: %s", exc)

    global _backup_loop_task
    _backup_loop_task = asyncio.create_task(auto_backup.run_backup_loop(), name="auto-backup-loop")

    yield

    background_analyzer.stop()
    if _backup_loop_task is not None:
        _backup_loop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _backup_loop_task
    logger.info("后台任务已停止")

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors.allowed_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str
    session_id: str
    user_id: str | None = None


class AbstractZhRequest(BaseModel):
    ids: list[str]


class PaperMarkPayload(BaseModel):
    viewed: bool | None = None
    liked: bool | None = None
    favorited: bool | None = None


class PaperNotePayload(BaseModel):
    note: str | None = Field(default=None, max_length=MAX_NOTE_LENGTH)


class ArxivPaperRequest(BaseModel):
    input: str


class ManualPaperRequest(BaseModel):
    """Hand-entered paper (arXiv-unreachable sources). Length caps mirror
    library_transfer's export limits."""

    title: str = Field(min_length=1, max_length=2000)
    authors: list[str] = Field(default_factory=list, max_length=200)
    abstract: str | None = Field(default=None, max_length=200_000)
    pdf: str | None = Field(default=None, max_length=2000)
    venue: str | None = Field(default=None, max_length=500)
    keywords: list[str] = Field(default_factory=list, max_length=100)
    published_year: int | None = Field(default=None, ge=1900, le=2100)

    @field_validator("title", "abstract", "pdf", "venue", mode="before")
    @classmethod
    def _strip_strings(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("authors", "keywords", mode="before")
    @classmethod
    def _strip_items(cls, v):
        if isinstance(v, list):
            return [s for item in v if isinstance(item, str) and (s := item.strip())]
        return v


def public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "email": user["email"],
        "role": user["role"],
        "is_active": user["is_active"],
        "email_verified": user["email_verified"],
        "created_at": user.get("created_at"),
        "last_login_at": user.get("last_login_at"),
    }


def public_feishu_settings(settings_row: dict | None) -> dict:
    if not settings_row:
        return {
            "configured": False,
            "webhook_url_masked": None,
            "enabled": False,
            "daily_push_count": 3,
            "last_tested_at": None,
            "last_test_status": None,
            "last_test_error": None,
        }
    return {
        "configured": True,
        "webhook_url_masked": mask_feishu_webhook_url(settings_row.get("webhook_url")),
        "enabled": bool(settings_row.get("enabled")),
        "daily_push_count": settings_row.get("daily_push_count") or 3,
        "last_tested_at": settings_row.get("last_tested_at"),
        "last_test_status": settings_row.get("last_test_status"),
        "last_test_error": settings_row.get("last_test_error"),
    }


def public_active_llm_config(config: dict | None) -> dict:
    if not config:
        return {
            "configured": False,
            "provider_key": None,
            "provider_name": None,
            "model_name": None,
        }

    model_name = config.get("model_name") or config.get("active_model")
    return {
        "configured": bool(config.get("api_key") and config.get("base_url") and model_name),
        "provider_key": config.get("provider_key"),
        "provider_name": config.get("name"),
        "model_name": model_name,
    }


# --- Single-user mode -------------------------------------------------------
# This build has no login UI. The account named by config.yaml `admin.email`
# (seeded by bootstrap_admin_user at startup) is the one and only user, and
# every auth dependency resolves to them unconditionally.

_single_user_cache: dict | None = None


def get_current_user_optional(request: Request) -> dict | None:
    global _single_user_cache
    if _single_user_cache is not None:
        return _single_user_cache
    try:
        _single_user_cache = get_user_by_email(normalize_email(settings.admin.email))
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc
    if not _single_user_cache:
        raise HTTPException(status_code=503, detail="单用户账号尚未初始化，请检查 config.yaml 的 admin 配置后重启")
    return _single_user_cache


def require_current_user(request: Request) -> dict:
    return get_current_user_optional(request)


def assert_chat_owner(session_id: str, user_id: str) -> dict | None:
    session_row = get_chat_session(session_id)
    if session_row and session_row.get("account_user_id") != user_id:
        raise HTTPException(status_code=403, detail="无权访问该会话")
    return session_row


@app.get("/auth/me")
async def me(user: dict = Depends(require_current_user)):
    return {"user": public_user(user)}


@app.get("/llm/active")
async def get_active_llm():
    try:
        return public_active_llm_config(get_active_llm_config())
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc


class LlmActiveRequest(BaseModel):
    provider_id: str
    model_name: str | None = None


def mask_api_key(api_key: str | None) -> str | None:
    if not api_key:
        return None
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:4]}...{api_key[-4:]}"


def public_llm_provider(provider: dict) -> dict:
    """Serialize one provider row for the settings dialog (key masked)."""
    return {
        "id": provider["id"],
        "provider_key": provider.get("provider_key"),
        "name": provider["name"],
        "base_url": provider["base_url"],
        "has_api_key": bool(provider.get("api_key")),
        "api_key_masked": mask_api_key(provider.get("api_key")),
        "is_active": bool(provider.get("is_active")),
        "is_enabled": bool(provider.get("is_enabled")),
        "active_model": provider.get("active_model"),
        "models": [
            {"model_name": m["model_name"], "is_enabled": bool(m.get("is_enabled", True))}
            for m in (provider.get("models") or [])
        ],
    }


@app.get("/llm/providers")
async def list_llm_providers_endpoint():
    """Providers + models for the settings dialog (key masked, never returned)."""
    try:
        providers = list_llm_providers()
        return {
            "providers": [public_llm_provider(p) for p in providers]
        }
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc


@app.post("/llm/active")
async def set_active_llm_endpoint(req: LlmActiveRequest):
    try:
        provider = set_active_llm_provider(req.provider_id, req.model_name)
        if not provider:
            raise HTTPException(status_code=404, detail="供应商不存在或已停用")
        return public_active_llm_config(get_active_llm_config())
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc


class LlmApiKeyUpdateRequest(BaseModel):
    api_key: str


@app.patch("/llm/providers/{provider_id}/api-key")
async def update_llm_provider_api_key_endpoint(provider_id: str, req: LlmApiKeyUpdateRequest):
    """Set (or clear with an empty string) a provider's API key for the dialog."""
    api_key = req.api_key.strip()
    if api_key and len(api_key) < 8:
        raise HTTPException(status_code=400, detail="API Key 长度不正确")
    try:
        provider = update_llm_provider_api_key(provider_id, api_key or None)
        if not provider:
            raise HTTPException(status_code=404, detail="供应商不存在")
        return {
            "id": provider["id"],
            "name": provider["name"],
            "has_api_key": bool(provider.get("api_key")),
            "api_key_masked": mask_api_key(provider.get("api_key")),
        }
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc


class LlmProviderCreateRequest(BaseModel):
    provider_key: str
    name: str
    base_url: str
    api_key: str | None = None
    active_model: str | None = None


@app.post("/llm/providers")
async def create_llm_provider_endpoint(req: LlmProviderCreateRequest):
    """Add a custom provider (e.g. a local vLLM endpoint) from the dialog."""
    name = req.name.strip()
    base_url = req.base_url.strip()
    if not name:
        raise HTTPException(status_code=400, detail="供应商名称不能为空")
    if not base_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Base URL 必须以 http:// 或 https:// 开头")
    if req.api_key and len(req.api_key.strip()) < 8:
        raise HTTPException(status_code=400, detail="API Key 长度不正确")
    try:
        provider = create_llm_provider(
            req.provider_key, name, base_url, req.api_key, req.active_model,
        )
    except LlmProviderConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, DatabaseError) as exc:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc
    return public_llm_provider(provider)


class LlmProviderUpdateRequest(BaseModel):
    name: str
    base_url: str


@app.patch("/llm/providers/{provider_id}")
async def update_llm_provider_endpoint(provider_id: str, req: LlmProviderUpdateRequest):
    """Edit a provider's display name and base URL from the dialog."""
    name = req.name.strip()
    base_url = req.base_url.strip()
    if not name:
        raise HTTPException(status_code=400, detail="供应商名称不能为空")
    if not base_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Base URL 必须以 http:// 或 https:// 开头")
    try:
        provider = update_llm_provider(provider_id, name, base_url)
        if not provider:
            raise HTTPException(status_code=404, detail="供应商不存在")
        return public_llm_provider(provider)
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc


@app.get("/me/paper-marks")
async def list_my_paper_marks(request: Request, paper_ids: str = ""):
    ids = [paper_id for paper_id in paper_ids.split(",") if paper_id]
    try:
        user = get_current_user_optional(request)
        if not user:
            return {"marks": {}}
        return {"marks": get_paper_marks(user["id"], ids)}
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc


@app.get("/me/reading-overview")
async def my_reading_overview(
    days: int = 112,
    user: dict = Depends(require_current_user),
):
    safe_days = min(max(days, 28), 366)
    try:
        return get_reading_overview(user["id"], safe_days)
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc



@app.get("/me/papers")
async def list_my_papers(
    page: int = 1,
    limit: int = 12,
    filter: str = "all",
    sort: str = "viewed_at",
    search: str = "",
    category: str = "all",
    user: dict = Depends(require_current_user),
):
    if filter not in {"all", "viewed", "liked", "favorited"}:
        raise HTTPException(status_code=400, detail="filter must be all, viewed, liked, or favorited")
    if sort not in {"viewed_at", "liked_at", "liked_first", "favorited_first", "opened_at", "title"}:
        raise HTTPException(status_code=400, detail="unsupported sort")

    category_param: str | None = None
    if category not in ("all", ""):
        if category == "uncategorized":
            category_param = "uncategorized"
        else:
            category_param = validate_category_uuid(category)
            if not category_param:
                raise HTTPException(status_code=400, detail="category must be all, uncategorized, or a category id")

    safe_page = max(page, 1)
    safe_limit = min(max(limit, 1), 50)
    offset = (safe_page - 1) * safe_limit
    search_term = search.strip()[:200] or None
    try:
        items, total = list_marked_papers(
            user["id"], filter, sort, offset, safe_limit,
            search=search_term, category=category_param,
        )
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc

    return {
        "items": items,
        "total": total,
        "page": safe_page,
        "pages": math.ceil(total / safe_limit) if total > 0 else 1,
    }


@app.put("/papers/{paper_id}/mark")
async def update_my_paper_mark(
    paper_id: str,
    req: PaperMarkPayload,
    user: dict = Depends(require_current_user),
):
    try:
        return set_paper_mark(
            user["id"],
            paper_id,
            viewed=req.viewed,
            liked=req.liked,
            favorited=req.favorited,
        )
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc


@app.post("/papers/{paper_id}/opened")
async def record_my_paper_opened(
    paper_id: str,
    user: dict = Depends(require_current_user),
):
    """Record a qualifying detail-page open (frontend calls this after the
    10s dwell). Only refreshes last_opened_at; the manual "viewed" mark is
    untouched."""
    try:
        return await asyncio.to_thread(record_paper_opened, user["id"], paper_id)
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc


@app.delete("/papers/{paper_id}/mark")
async def remove_my_paper_mark(
    paper_id: str,
    user: dict = Depends(require_current_user),
):
    """Remove a paper from the user's My Papers and clear its analysis caches.

    Deletes the user's mark (so it leaves My Papers) and clears the paper's
    cached analysis (LLM response, code availability, on-disk content cache).
    The paper itself stays in the library (conference counts unchanged).
    """
    try:
        delete_paper_mark(user["id"], paper_id)
        clear_paper_analysis_cache(paper_id)
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc
    delete_paper_content_cache(paper_id)
    return {"ok": True}


@app.put("/papers/{paper_id}/note")
async def update_my_paper_note(
    paper_id: str,
    req: PaperNotePayload,
    user: dict = Depends(require_current_user),
):
    """Upsert the user's per-paper note. Empty/None clears it.

    Writing a non-empty note to an unmarked paper marks it viewed so the
    paper shows up in My Papers (see paper_notes.set_paper_note).
    """
    try:
        return set_paper_note(user["id"], paper_id, req.note)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc


class PaperCategoryRequest(BaseModel):
    category_id: str
    action: str = "assign"  # "assign" | "unassign"


class PaperCategoryCreateRequest(BaseModel):
    name: str
    parent_id: str | None = None


class PaperCategoryUpdateRequest(BaseModel):
    # One thing per PATCH: rename OR move OR recolor.
    name: str | None = None
    parent_id: str | None = None
    color: str | None = None


class CategorizeApplyRequest(BaseModel):
    category_name: str
    parent_id: str | None = None
    reuse_category_id: str | None = None
    paper_ids: list[str]


@app.get("/me/paper-categories")
async def get_my_paper_categories(user: dict = Depends(require_current_user)):
    try:
        return list_paper_categories(user["id"])
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc


@app.post("/me/paper-categories")
async def create_my_paper_category(
    req: PaperCategoryCreateRequest,
    user: dict = Depends(require_current_user),
):
    parent_id = validate_category_uuid(req.parent_id) if req.parent_id else None
    if req.parent_id and not parent_id:
        raise HTTPException(status_code=400, detail="parent_id 不是有效的分类 id")
    try:
        category = create_paper_category(user["id"], req.name, parent_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc
    return {"category": category}


@app.patch("/me/paper-categories/{category_id}")
async def update_my_paper_category(
    category_id: str,
    req: PaperCategoryUpdateRequest,
    user: dict = Depends(require_current_user),
):
    provided = req.model_fields_set
    provided_fields = provided & {"name", "parent_id", "color"}
    if not provided_fields:
        raise HTTPException(status_code=400, detail="name、parent_id 与 color 至少提供一个")
    if len(provided_fields) > 1:
        raise HTTPException(status_code=400, detail="仅支持一次修改一个属性")
    try:
        if "name" in provided and req.name is not None:
            category = rename_paper_category(user["id"], category_id, req.name)
        elif "parent_id" in provided:
            parent_id = validate_category_uuid(req.parent_id) if req.parent_id else None
            if req.parent_id and not parent_id:
                raise HTTPException(status_code=400, detail="parent_id 不是有效的分类 id")
            category = move_paper_category(user["id"], category_id, parent_id)
        elif "color" in provided:
            category = set_paper_category_color(user["id"], category_id, req.color)
        else:
            raise HTTPException(status_code=400, detail="仅支持重命名、移动或改色")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc
    return {"category": category}


@app.delete("/me/paper-categories/{category_id}")
async def delete_my_paper_category(
    category_id: str,
    user: dict = Depends(require_current_user),
):
    try:
        deleted_subcategories = delete_paper_category(user["id"], category_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc
    return {"ok": True, "deleted_subcategories": deleted_subcategories}


@app.put("/papers/{paper_id}/category")
async def set_my_paper_category(
    paper_id: str,
    req: PaperCategoryRequest,
    user: dict = Depends(require_current_user),
):
    """Assign/unassign a paper to one of the user's categories (multi-category)."""
    category_id = validate_category_uuid(req.category_id)
    if not category_id:
        raise HTTPException(status_code=400, detail="category_id 不是有效的分类 id")
    if req.action not in {"assign", "unassign"}:
        raise HTTPException(status_code=400, detail="action 仅支持 assign 或 unassign")
    try:
        if req.action == "assign":
            assign_paper_category(user["id"], paper_id, category_id)
        else:
            unassign_paper_category(user["id"], paper_id, category_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc
    return {"ok": True, "category_id": category_id, "action": req.action}


@app.delete("/papers/{paper_id}/categories")
async def clear_my_paper_categories(paper_id: str, user: dict = Depends(require_current_user)):
    """Drop a paper from every category (back to 未分类)."""
    try:
        clear_paper_categories(user["id"], paper_id)
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc
    return {"ok": True}


CATEGORIZE_SYSTEM_PROMPT = """你是一名 AI 研究方向的论文分类助手。用户已有一个分类树（可能为空）。
我会给出：
1. 现有分类列表（带编号，缩进表示层级，例如 "3. LLM 安全 > 3.1 越狱防御"）
2. 待分类论文列表（编号、标题、摘要）
要求：
- 优先把论文归入现有分类（assignments 里用分类编号）
- 只有当论文与所有现有分类都不匹配时才建议新分类：new_categories 里每项含 name 和 parent_index（挂到哪个现有分类下，顶层新分类 parent_index 为 null）
- 新分类总数不超过 3 个；名称为简短中文，不超过 12 个字，不得与现有分类重名
- 每篇论文至少归入一个分类；特别匹配的可以归入多个（assignments 值可以是编号或编号数组）
只输出 JSON，格式：
{"new_categories": [{"name": "新类名", "parent_index": 3}],
 "assignments": {"1": 5, "2": 4, "3": [2, 8]}}
现有分类编号 1..N；新建分类隐式编号从 N+1 开始，可在 assignments 中引用。不要输出 JSON 以外的任何内容。"""


PAPER_AUTO_CATEGORIZE_PROMPT = """你是一名论文分类助手。我会给出：
1. 用户现有的分类列表（带编号，缩进表示层级）
2. 一篇论文的标题和摘要
要求：
- 只能从现有分类中挑选，绝对不要发明新分类
- 只选与论文主题明确相关的分类，宁缺毋滥；没有合适的就返回空数组
- 最多选 3 个，按相关度从高到低排列
只输出 JSON：{"categories": [编号, ...]}
不要输出 JSON 以外的任何内容。"""


def _format_category_tree_for_prompt(categories: list[dict]) -> tuple[str, dict[str, int]]:
    """Render the flat category list as an indented numbered outline."""
    by_parent: dict[str | None, list[dict]] = {}
    for cat in categories:
        by_parent.setdefault(cat["parent_id"], []).append(cat)

    lines: list[str] = []
    counter = {"n": 0}
    indexes: dict[str, int] = {}

    def walk(parent: str | None, depth: int) -> None:
        for cat in sorted(by_parent.get(parent, []), key=lambda c: (c["position"], c["name"])):
            counter["n"] += 1
            indexes[cat["id"]] = counter["n"]
            indent = "  " * depth
            lines.append(f"{counter['n']}. {indent}{cat['name']}")
            walk(cat["id"], depth + 1)

    walk(None, 0)
    return "\n".join(lines), indexes


def _normalize_assignment_value(value: object) -> list[int]:
    """assignments 值可能是 int 或 [int, ...]；统一成 list[int]。"""
    if isinstance(value, (int, float)):
        return [int(value)]
    if isinstance(value, list):
        return [int(v) for v in value if isinstance(v, (int, float))]
    return []


async def _llm_chat_collect(messages: list, job: dict | None = None, **kwargs) -> tuple[str, str]:
    """Stream a chat call, returning (content, reasoning).

    When job is given, its reasoning field is appended live so a polling
    frontend can watch the model think.
    """
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    async for chunk in llm.chat_stream_events(messages, **kwargs):
        if chunk.kind == "reasoning":
            reasoning_parts.append(chunk.content)
            if job is not None:
                job["reasoning"] = "".join(reasoning_parts)
        else:
            content_parts.append(chunk.content)
    return "".join(content_parts), "".join(reasoning_parts)


@dataclass
class CompletedToolCall:
    """A fully-assembled tool call ready for execution."""
    id: str
    name: str
    arguments: dict


async def _llm_chat_turn(
    messages: list,
    job: dict | None = None,
    **kwargs,
) -> tuple[str, str, list[CompletedToolCall]]:
    """One agent turn: stream a chat call with tools, returning
    (content, reasoning, tool_calls).

    Tool-call streaming deltas are aggregated by index into complete calls
    (arguments arrive in JSON fragments). reasoning is appended to job live,
    same as _llm_chat_collect.
    """
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_call_state: dict[int, dict] = {}
    # Per-turn reasoning is appended to the job log (not overwritten) so the
    # tool-call trace lines from earlier turns survive.
    base_length = len(job.get("reasoning") or "") if job is not None else 0
    async for chunk in llm.chat_stream_events(messages, **kwargs):
        if chunk.kind == "reasoning":
            reasoning_parts.append(chunk.content)
            if job is not None:
                job["reasoning"] = job.get("reasoning", "")[:base_length] + "".join(reasoning_parts)
        elif chunk.kind == "tool_call":
            delta = json.loads(chunk.content)
            state = tool_call_state.setdefault(delta["index"], {"id": "", "name": "", "arguments": ""})
            if delta.get("id"):
                state["id"] += delta["id"]
            if delta.get("name"):
                state["name"] += delta["name"]
            state["arguments"] += delta.get("arguments") or ""
        else:
            content_parts.append(chunk.content)

    tool_calls: list[CompletedToolCall] = []
    for index in sorted(tool_call_state):
        state = tool_call_state[index]
        try:
            arguments = json.loads(state["arguments"]) if state["arguments"] else {}
        except json.JSONDecodeError:
            arguments = {}
        if isinstance(arguments, dict):
            tool_calls.append(CompletedToolCall(
                id=state["id"] or f"call_{index}",
                name=state["name"],
                arguments=arguments,
            ))
    return "".join(content_parts), "".join(reasoning_parts), tool_calls


# Background AI categorization jobs. In-memory only: a backend restart drops
# them, which is acceptable — the user just re-runs the job.
categorize_jobs: dict[str, dict] = {}
CATEGORIZE_JOB_TTL_SECONDS = 30 * 60


def _serialize_categorize_job(job: dict) -> dict:
    reasoning = job.get("reasoning") or ""
    # Keep the tool-call trace lines (they lead the log) plus the tail of the
    # model's own thinking, so a long run never hides the trajectory.
    trace_lines = [line for line in reasoning.splitlines() if line.startswith("▸")]
    body = reasoning[-4000:]
    if trace_lines and not any(line.startswith("▸") for line in body.splitlines()):
        body = "\n".join(trace_lines) + "\n…\n" + body
    return {
        "id": job["id"],
        "kind": job["kind"],
        "status": job["status"],
        "description": job.get("description"),
        "target_category_name": job.get("target_category_name"),
        "reasoning_tail": body,
        "suggestion": job.get("suggestion"),
        "auto_result": job.get("auto_result"),
        "error": job.get("error"),
        "created_at": job["created_at"],
    }


def _prune_categorize_jobs(user_id: str) -> None:
    cutoff = datetime.now(timezone.utc).timestamp() - CATEGORIZE_JOB_TTL_SECONDS
    stale = [
        job_id for job_id, job in categorize_jobs.items()
        if job["user_id"] == user_id
        and job["status"] in {"done", "error"}
        and job["created_at"].timestamp() < cutoff
    ]
    for job_id in stale:
        del categorize_jobs[job_id]


async def _run_suggest_job(job: dict) -> None:
    """Describe-to-categorize, agent-style: the LLM navigates the category
    tree via tool calls (list_categories / list_papers / search_papers) and
    only reads what it needs, then emits the final JSON. No writes."""
    MAX_AGENT_TURNS = 10
    try:
        # Stable per-job mapping: short id (P01..) -> paper snapshot.
        paper_registry: dict[str, dict] = {}

        messages: list[dict] = [
            {"role": "system", "content": CATEGORIZE_AGENT_SYSTEM_PROMPT},
        ]
        if job.get("target_category_name"):
            messages.append({"role": "user", "content": (
                f"用户描述：{job['description']}\n"
                f"目标分类已指定为「{job['target_category_name']}」：只挑选论文，"
                "最终 JSON 的 category_name / parent_category 置 null，"
                "reuse_category 填「" + job["target_category_name"] + "」。"
            )})
        else:
            messages.append({"role": "user", "content": f"用户描述：{job['description']}"})

        reply = ""
        for _turn in range(MAX_AGENT_TURNS):
            # Per-turn retry: a transient stream failure (read timeout, reset)
            # shouldn't kill the whole job — the turn is replayed from the
            # same message list, which is safe (no side effects before the call).
            turn_error: Exception | None = None
            for _attempt in range(3):
                try:
                    content, _reasoning, tool_calls = await _llm_chat_turn(
                        messages,
                        job=job,
                        tools=CATEGORIZE_TOOLS,
                        temperature=0,
                        _usage_context="categorize_suggest",
                    )
                    turn_error = None
                    break
                except Exception as exc:  # noqa: BLE001 — retried below
                    turn_error = exc
                    job["reasoning"] += f"\n（第 {_attempt + 1} 次调用失败：{exc}，重试…）"
                    await asyncio.sleep(1 + _attempt)
            if turn_error is not None:
                raise turn_error
            if not tool_calls:
                reply = content
                break

            messages.append({
                "role": "assistant",
                "content": content or None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
                    }
                    for tc in tool_calls
                ],
            })
            ctx = {
                "user_id": job["user_id"],
                "paper_registry": paper_registry,
            }
            for tc in tool_calls:
                result = await asyncio.to_thread(_execute_categorize_tool, tc.name, tc.arguments, ctx)
                job["reasoning"] += f"\n▸ {tc.name}({_summarize_tool_args(tc.arguments)}) → {result.splitlines()[0][:120] if result else '空'}"
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
        else:
            job["status"] = "error"
            job["error"] = "AI 检索轮次过多，请换个更明确的描述重试"
            return

        try:
            tree = list_paper_categories(job["user_id"])
        except DatabaseError:
            job["status"] = "error"
            job["error"] = "数据库暂时不可用，请稍后重试"
            return

        import re as _re
        try:
            match = _re.search(r"\{.*\}", reply, flags=_re.DOTALL)
            payload = json.loads(match.group(0)) if match else json.loads(reply)
            matched_refs = payload.get("matched") or []
            if not isinstance(matched_refs, list):
                matched_refs = []
        except (ValueError, json.JSONDecodeError):
            job["status"] = "error"
            job["error"] = "AI 建议解析失败，请重试"
            return

        def _category_id_by_name(name_value: object) -> str | None:
            name = str(name_value or "").strip()
            if not name:
                return None
            node = next((c for c in tree["categories"] if c["name"] == name), None)
            return node["id"] if node else None

        if job.get("target_category_id"):
            target_category = next(
                (c for c in tree["categories"] if c["id"] == job["target_category_id"]), None
            )
            category_name: str | None = None
            parent_id: str | None = None
            reuse_category_id: str | None = target_category["id"] if target_category else None
        else:
            category_name = str(payload.get("category_name") or "").strip()[:12]
            if not category_name:
                job["status"] = "error"
                job["error"] = "AI 未能提炼分类名，请换个说法重试"
                return
            parent_id = _category_id_by_name(payload.get("parent_category"))
            reuse_category_id = _category_id_by_name(payload.get("reuse_category"))
            # Same-name sibling of the suggested parent: reuse instead of creating.
            existing_by_parent_name: dict[tuple[str | None, str], dict] = {
                (cat["parent_id"], cat["name"]): cat for cat in tree["categories"]
            }
            twin = existing_by_parent_name.get((parent_id, category_name))
            if twin is not None:
                reuse_category_id = twin["id"]

        matched = []
        seen: set[str] = set()
        for ref in matched_refs:
            snapshot = paper_registry.get(str(ref).strip())
            if not snapshot or snapshot["paper_id"] in seen:
                continue
            seen.add(snapshot["paper_id"])
            matched.append(snapshot)

        if not matched:
            job["status"] = "error"
            job["error"] = "AI 没有找到符合描述的论文，请换个说法重试"
            return

        job["suggestion"] = {
            "category_name": category_name,
            "parent_id": parent_id,
            "reuse_category_id": reuse_category_id,
            "matched": matched,
            "total": len(paper_registry),
        }
        job["status"] = "done"
    except Exception as exc:  # noqa: BLE001 — background job boundary
        logger.exception("categorize suggest job %s failed", job["id"])
        job["status"] = "error"
        job["error"] = f"AI 挑选失败：{exc}"


async def _run_auto_job(job: dict) -> None:
    """Auto-categorize the user's papers (writes assignments)."""
    mode = "uncategorized" if job["kind"] == "auto_uncategorized" else "full"
    user_id = job["user_id"]
    try:
        try:
            tree = list_paper_categories(user_id)
            category_filter = "uncategorized" if mode == "uncategorized" else None
            items, _total = list_marked_papers(user_id, "all", "viewed_at", 0, 200, category=category_filter)
        except DatabaseError:
            job["status"] = "error"
            job["error"] = "数据库暂时不可用，请稍后重试"
            return
        if not items:
            job["status"] = "error"
            job["error"] = "没有可分类的论文"
            return

        outline, index_by_category_id = _format_category_tree_for_prompt(tree["categories"])
        category_id_by_index: dict[int, str] = {idx: cid for cid, idx in index_by_category_id.items()}
        next_index = len(category_id_by_index) + 1

        paper_lines = []
        for idx, item in enumerate(items, start=1):
            paper = item["paper"]
            abstract = paper.get("abstract") or ""
            paper_lines.append(f"{idx}. {paper.get('title','')}｜{abstract}")

        user_prompt = (
            f"现有分类列表（{len(category_id_by_index)} 个）：\n{outline or '（空）'}\n\n"
            + "待分类论文列表：\n" + "\n".join(paper_lines)
        )

        reply, _reasoning = await _llm_chat_collect(
            [
                {"role": "system", "content": CATEGORIZE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            job=job,
            temperature=0,
            _usage_context="auto_categorize",
        )

        import re as _re
        try:
            match = _re.search(r"\{.*\}", reply, flags=_re.DOTALL)
            payload = json.loads(match.group(0)) if match else json.loads(reply)
            assignments = payload.get("assignments") or {}
            new_categories = payload.get("new_categories") or []
        except (ValueError, json.JSONDecodeError):
            job["status"] = "error"
            job["error"] = "AI 分类输出解析失败，请重试"
            return

        created_category_ids: dict[int, str] = {}
        created_names: list[str] = []
        existing_by_parent_name: dict[tuple[str | None, str], str] = {
            (cat["parent_id"], cat["name"]): cat["id"] for cat in tree["categories"]
        }
        for suggestion in new_categories[:5]:
            if not isinstance(suggestion, dict):
                continue
            name = str(suggestion.get("name") or "").strip()
            if not name or len(created_names) >= 3:
                break
            parent_index = suggestion.get("parent_index")
            parent_id: str | None = None
            if parent_index is not None:
                parent_id = category_id_by_index.get(int(parent_index)) if str(parent_index).isdigit() else None
                if parent_id is None:
                    continue
            existing_id = existing_by_parent_name.get((parent_id, name))
            if existing_id:
                created_category_ids[next_index] = existing_id
                next_index += 1
                continue
            try:
                node = create_paper_category(user_id, name, parent_id)
            except (ValueError, LookupError, DatabaseError):
                continue
            created_category_ids[next_index] = node["id"]
            next_index += 1
            created_names.append(node["name"])
            existing_by_parent_name[(parent_id, node["name"])] = node["id"]

        def resolve_target(index_value: object) -> str | None:
            try:
                index = int(index_value)
            except (TypeError, ValueError):
                return None
            return category_id_by_index.get(index) or created_category_ids.get(index)

        paper_id_by_index = {str(i): item["paper"]["id"] for i, item in enumerate(items, start=1)}

        if mode == "full":
            try:
                clear_user_paper_categories(user_id)
            except DatabaseError:
                job["status"] = "error"
                job["error"] = "数据库暂时不可用，请稍后重试"
                return

        updated = 0
        for idx, value in assignments.items():
            paper_id = paper_id_by_index.get(str(idx))
            if not paper_id:
                continue
            for target in _normalize_assignment_value(value):
                category_id = resolve_target(target)
                if not category_id:
                    continue
                try:
                    assign_paper_category(user_id, paper_id, category_id)
                    updated += 1
                except (LookupError, DatabaseError):
                    pass

        job["auto_result"] = {
            "updated": updated,
            "total": len(items),
            "created_categories": created_names,
        }
        job["status"] = "done"
    except Exception as exc:  # noqa: BLE001 — background job boundary
        logger.exception("categorize auto job %s failed", job["id"])
        job["status"] = "error"
        job["error"] = f"AI 分类失败：{exc}"


class CategorizeJobRequest(BaseModel):
    kind: str  # "suggest" | "auto_uncategorized" | "auto_full"
    description: str | None = None
    target_category_id: str | None = None


@app.post("/me/papers/categorize-jobs")
async def start_categorize_job(
    req: CategorizeJobRequest,
    user: dict = Depends(require_current_user),
):
    """Kick off a background AI categorization job; returns immediately."""
    if req.kind not in {"suggest", "auto_uncategorized", "auto_full"}:
        raise HTTPException(status_code=400, detail="kind 仅支持 suggest / auto_uncategorized / auto_full")
    ensure_llm_configured()

    description = (req.description or "").strip()
    target_category_id: str | None = None
    target_category_name: str | None = None
    if req.kind == "suggest":
        if not description:
            raise HTTPException(status_code=400, detail="描述不能为空")
        if len(description) > 200:
            raise HTTPException(status_code=400, detail="描述过长（限 200 字）")
        if req.target_category_id:
            target_category_id = validate_category_uuid(req.target_category_id)
            if not target_category_id:
                raise HTTPException(status_code=400, detail="target_category_id 不是有效的分类 id")
    elif description or req.target_category_id:
        raise HTTPException(status_code=400, detail="该 kind 不接受 description / target_category_id")

    try:
        target_category_name = None
        if target_category_id:
            tree = list_paper_categories(user["id"])
            target = next((c for c in tree["categories"] if c["id"] == target_category_id), None)
            if target is None:
                raise HTTPException(status_code=404, detail="目标分类不存在")
            target_category_name = target["name"]
        items, _total = list_marked_papers(user["id"], "all", "viewed_at", 0, 1)
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc
    if not items:
        raise HTTPException(status_code=400, detail="你的论文列表为空")

    job_id = str(uuid.uuid4())
    job = {
        "id": job_id,
        "user_id": user["id"],
        "kind": req.kind,
        "status": "running",
        "description": description or None,
        "target_category_id": target_category_id,
        "target_category_name": target_category_name,
        "reasoning": "",
        "suggestion": None,
        "auto_result": None,
        "error": None,
        "created_at": datetime.now(timezone.utc),
    }
    categorize_jobs[job_id] = job
    runner = _run_suggest_job if req.kind == "suggest" else _run_auto_job
    asyncio.create_task(runner(job))
    return {"job_id": job_id}


@app.get("/me/papers/categorize-jobs")
async def list_categorize_jobs(user: dict = Depends(require_current_user)):
    """All of the user's categorization jobs, newest first."""
    _prune_categorize_jobs(user["id"])
    jobs = [job for job in categorize_jobs.values() if job["user_id"] == user["id"]]
    jobs.sort(key=lambda j: j["created_at"], reverse=True)
    return {"jobs": [_serialize_categorize_job(job) for job in jobs]}


@app.delete("/me/papers/categorize-jobs/{job_id}")
async def dismiss_categorize_job(job_id: str, user: dict = Depends(require_current_user)):
    """Drop a finished (or abandon a running) job from the listing.

    The underlying asyncio task keeps running either way; only the listing
    entry is removed.
    """
    job = categorize_jobs.get(job_id)
    if job is None or job["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="任务不存在")
    del categorize_jobs[job_id]
    return {"ok": True}


CATEGORIZE_AGENT_SYSTEM_PROMPT = """你是一名 AI 研究方向的论文检索 agent，负责在用户的个人论文库里按自然语言描述定位论文。
你可以调用工具逐步探索，不要猜测论文内容：
- list_categories()：查看用户的分类树（含每个分类的论文数）——先调它了解结构
- list_papers(category, query?, offset?, limit?)：读取某个分类（含其子分类）下的论文，返回 P 编号、标题、完整摘要、现有分类；limit 默认 10、最大 15，可用 offset 翻页
- search_papers(query, offset?, limit?)：在用户全部论文里按关键词搜索标题/摘要/关键词

工作流程：
1. 用户描述限定了分类范围时（如「LLM安全与对齐类别下关于COT安全的」），只在该分类子树内挑选，调用 list_papers(category=该分类) 即可，不要全库扫描
2. 描述没有范围时，先 list_categories()，再进入最相关的分类读取论文；必要时用 search_papers 补充跨主题的论文
3. 挑选标准：宁缺毋滥，只选与描述主题明确相关的论文；论文可能在多个分类下，读到重复的 P 编号会保持一致
4. 充分浏览后停止调用工具，直接输出最终结论 JSON（不要输出 JSON 以外的任何内容）：
{"category_name": "简短中文分类名", "parent_category": "挂载到哪个现有分类名下或 null", "reuse_category": "与现有哪个分类高度重合或 null", "matched": ["P01", "P03"]}
- category_name：从用户描述提炼，不超过 12 个字，去掉「关于/我想找/的文章」等口语词
- parent_category：新分类挂在现有分类树下哪个节点（用分类名），放顶层为 null
- reuse_category：用户描述与现有某分类含义高度重合时填该分类名（此时不会新建）
- matched：用工具结果里的 P 编号引用论文；一个都没有匹配时输出空数组"""

CATEGORIZE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_categories",
            "description": "查看用户的分类树（每个分类附论文数）。无参数。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_papers",
            "description": "读取某分类（含子分类）下的论文：P编号、标题、完整摘要、现有分类。limit 默认 10、最大 15；offset 翻页。",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "分类名（来自 list_categories 的结果）"},
                    "query": {"type": "string", "description": "可选：在该分类内按关键词过滤标题/摘要"},
                    "offset": {"type": "integer", "description": "可选：跳过前 N 条，默认 0"},
                    "limit": {"type": "integer", "description": "可选：返回条数，默认 10，最大 15"},
                },
                "required": ["category"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_papers",
            "description": "在用户全部论文里按关键词搜索标题/摘要/关键词。limit 默认 10、最大 15。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "offset": {"type": "integer", "description": "可选：跳过前 N 条，默认 0"},
                    "limit": {"type": "integer", "description": "可选：返回条数，默认 10，最大 15"},
                },
                "required": ["query"],
            },
        },
    },
]


def _summarize_tool_args(args: dict) -> str:
    """Compact one-line rendering of tool args for the reasoning trace."""
    parts = [f"{key}={value!r}" for key, value in args.items()]
    return ", ".join(parts)[:80]


def _register_agent_papers(registry: dict[str, dict], items: list[dict]) -> None:
    """Assign stable short ids (P01..) to papers; repeated reads reuse ids."""
    existing_paper_ids = {entry["paper_id"] for entry in registry.values()}
    counter = 1
    for item in items:
        paper_id = item["paper"]["id"]
        if paper_id in existing_paper_ids:
            continue
        while f"P{counter:02d}" in registry:
            counter += 1
        registry[f"P{counter:02d}"] = {
            "paper_id": paper_id,
            "title": item["paper"].get("title") or "",
            "existing_categories": item["mark"].get("categories") or [],
        }
        existing_paper_ids.add(paper_id)


def _format_agent_paper_lines(registry: dict[str, dict], items: list[dict], papers_by_id: dict[str, dict]) -> list[str]:
    """Render tool-result paper lines (short id + title + full abstract + categories)."""
    lines = []
    for item in items:
        paper_id = item["paper"]["id"]
        short_id = next((key for key, entry in registry.items() if entry["paper_id"] == paper_id), None)
        abstract = papers_by_id.get(paper_id, {}).get("abstract") or ""
        categories = ", ".join(c["name"] for c in (item["mark"].get("categories") or []))
        lines.append(
            f"{short_id}. {item['paper'].get('title','')}｜{abstract}"
            + (f"｜现有分类: {categories}" if categories else "")
        )
    return lines


def _execute_categorize_tool(name: str, args: dict, ctx: dict) -> str:
    """Run one agent tool against the DB; returns text for the LLM.

    Errors are returned as text so the model can correct itself instead of
    failing the job.
    """
    user_id = ctx["user_id"]
    registry: dict[str, dict] = ctx["paper_registry"]
    try:
        if name == "list_categories":
            tree = list_paper_categories(user_id)
            by_parent: dict[str | None, list[dict]] = {}
            for cat in tree["categories"]:
                by_parent.setdefault(cat["parent_id"], []).append(cat)

            lines: list[str] = []

            def walk(parent: str | None, depth: int) -> None:
                for cat in sorted(by_parent.get(parent, []), key=lambda c: (c["position"], c["name"])):
                    lines.append(f"{'  ' * depth}- {cat['name']}（{cat['paper_count']} 篇）")
                    walk(cat["id"], depth + 1)

            walk(None, 0)
            return "分类树：\n" + ("\n".join(lines) if lines else "（还没有任何分类）")

        if name == "list_papers":
            category_name = str(args.get("category") or "").strip()
            if not category_name:
                return "错误：缺少 category 参数。请先调用 list_categories 查看分类名。"
            query = str(args.get("query") or "").strip() or None
            offset = max(int(args.get("offset") or 0), 0)
            limit = min(max(int(args.get("limit") or 10), 1), 15)
            tree = list_paper_categories(user_id)
            node = next(
                (c for c in tree["categories"]
                 if c["name"] == category_name
                 or c["name"].replace(" ", "") == category_name.replace(" ", "")),
                None,
            )
            if node is None:
                return f"错误：没有名为「{category_name}」的分类。请调用 list_categories 查看正确的分类名。"
            items, total = list_marked_papers(
                user_id, "all", "viewed_at", offset, limit,
                search=query, category=node["id"],
            )
            if not items:
                return f"分类「{node['name']}」下没有匹配的论文（共 {total} 篇）。"
            _register_agent_papers(registry, items)
            papers_by_id = get_papers_by_ids([item["paper"]["id"] for item in items])
            lines = _format_agent_paper_lines(registry, items, papers_by_id)
            return (
                f"分类「{node['name']}」下的论文（第 {offset + 1}-{offset + len(items)} 条，共 {total} 篇）：\n"
                + "\n".join(lines)
            )

        if name == "search_papers":
            query = str(args.get("query") or "").strip()
            if not query:
                return "错误：缺少 query 参数。"
            offset = max(int(args.get("offset") or 0), 0)
            limit = min(max(int(args.get("limit") or 10), 1), 15)
            items, total = list_marked_papers(user_id, "all", "viewed_at", offset, limit, search=query)
            if not items:
                return f"按「{query}」搜索没有结果。"
            _register_agent_papers(registry, items)
            papers_by_id = get_papers_by_ids([item["paper"]["id"] for item in items])
            lines = _format_agent_paper_lines(registry, items, papers_by_id)
            return (
                f"按「{query}」搜索（第 {offset + 1}-{offset + len(items)} 条，共 {total} 篇）：\n"
                + "\n".join(lines)
            )

        return f"错误：未知工具 {name}。可用：list_categories / list_papers / search_papers。"
    except (ValueError, LookupError) as exc:
        return f"错误：{exc}"
    except DatabaseError:
        return "错误：数据库暂时不可用，请稍后重试。"


@app.post("/me/papers/categorize-apply")
async def categorize_apply_my_papers(
    req: CategorizeApplyRequest,
    user: dict = Depends(require_current_user),
):
    """Apply a previewed AI suggestion (or a hand-edited variant of it).

    Pure DB work — no LLM. reuse_category_id wins over creating; when
    creating hits a same-name sibling, that existing node is reused instead
    so a double-submit never errors.
    """
    name = req.category_name.strip()
    if req.reuse_category_id:
        target_category_id = validate_category_uuid(req.reuse_category_id)
        if not target_category_id:
            raise HTTPException(status_code=400, detail="reuse_category_id 不是有效的分类 id")
        try:
            tree = list_paper_categories(user["id"])
            target = next((c for c in tree["categories"] if c["id"] == target_category_id), None)
            if target is None:
                raise HTTPException(status_code=404, detail="分类不存在")
            category = target
        except DatabaseError as exc:
            raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc
    else:
        if not name:
            raise HTTPException(status_code=400, detail="分类名不能为空")
        parent_id = validate_category_uuid(req.parent_id) if req.parent_id else None
        if req.parent_id and not parent_id:
            raise HTTPException(status_code=400, detail="parent_id 不是有效的分类 id")
        try:
            category = create_paper_category(user["id"], name, parent_id)
        except ValueError:
            # Same-name sibling already exists: reuse it instead of failing.
            try:
                tree = list_paper_categories(user["id"])
                category = next(
                    (c for c in tree["categories"] if c["name"] == name and c["parent_id"] == parent_id),
                    None,
                )
            except DatabaseError as exc:
                raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc
            if category is None:
                raise HTTPException(status_code=409, detail="同级已有同名分类")
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except DatabaseError as exc:
            raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc

    assigned = 0
    for paper_id in req.paper_ids:
        try:
            assign_paper_category(user["id"], paper_id, category["id"])
            assigned += 1
        except (LookupError, DatabaseError):
            pass

    return {"ok": True, "category": category, "assigned": assigned}


MAX_PAPER_CATEGORIES_PER_CALL = 3
PAPER_AUTO_CATEGORIZE_ABSTRACT_LIMIT = 2000


@app.post("/papers/{paper_id}/auto-categorize")
async def auto_categorize_paper(
    paper_id: str,
    user: dict = Depends(require_current_user),
):
    """Assign ONE paper to suitable EXISTING categories with a single LLM call.

    Additive only: existing assignments are never cleared, and only numeric
    indices into the category outline are honored — the LLM cannot invent
    categories. Synchronous by design (one fast temperature-0 call).
    """
    ensure_llm_configured()
    try:
        paper = await asyncio.to_thread(get_paper, paper_id)
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc
    if paper is None:
        raise HTTPException(status_code=404, detail="论文不存在")

    try:
        tree = await asyncio.to_thread(list_paper_categories, user["id"])
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc
    if not tree["categories"]:
        raise HTTPException(status_code=400, detail="请先创建分类")

    outline, index_by_category_id = _format_category_tree_for_prompt(tree["categories"])
    # The helper maps id -> index; the LLM returns indices, so invert it.
    category_id_by_index = {idx: cid for cid, idx in index_by_category_id.items()}
    name_by_id = {cat["id"]: cat["name"] for cat in tree["categories"]}

    abstract = (paper.get("abstract") or "").strip()
    if len(abstract) > PAPER_AUTO_CATEGORIZE_ABSTRACT_LIMIT:
        abstract = abstract[:PAPER_AUTO_CATEGORIZE_ABSTRACT_LIMIT]
    user_prompt = (
        f"现有分类列表：\n{outline}\n\n"
        f"论文标题：{paper.get('title') or ''}\n"
        f"论文摘要：{abstract or '（无摘要）'}"
    )

    reply, _reasoning = await _llm_chat_collect(
        [
            {"role": "system", "content": PAPER_AUTO_CATEGORIZE_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0,
        _usage_context="auto_categorize",
    )

    try:
        match = re.search(r"\{.*\}", reply, flags=re.DOTALL)
        payload = json.loads(match.group(0)) if match else json.loads(reply)
        picked = payload.get("categories") or []
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail="AI 输出解析失败，请重试") from exc
    if not isinstance(picked, list):
        picked = []

    skipped = 0
    resolved_ids: list[str] = []
    for value in picked:
        try:
            index = int(value)
        except (TypeError, ValueError):
            skipped += 1
            continue
        category_id = category_id_by_index.get(index)
        if not category_id or category_id in resolved_ids:
            skipped += 1
            continue
        resolved_ids.append(category_id)
        if len(resolved_ids) >= MAX_PAPER_CATEGORIES_PER_CALL:
            break

    assigned: list[dict] = []
    for category_id in resolved_ids:
        try:
            assign_paper_category(user["id"], paper_id, category_id)
        except (LookupError, DatabaseError):
            skipped += 1
            continue
        assigned.append({"id": category_id, "name": name_by_id.get(category_id, category_id)})

    result = {"ok": True, "assigned": assigned, "skipped": skipped}
    if not assigned:
        result["message"] = "没有找到合适的分类"
    return result


@app.post("/papers/abstract-zh")
async def get_papers_abstract_zh(
    req: AbstractZhRequest,
    user: dict = Depends(require_current_user),
):
    """Return Chinese abstracts for the given paper ids (lazy: translates and
    caches any that are missing via the active LLM)."""
    ensure_llm_configured()
    ids = [pid for pid in req.ids if pid][:20]
    if not ids:
        return {"results": {}}
    try:
        cached = await asyncio.to_thread(get_abstract_zh_map, ids)
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc

    # Translate the missing ones in parallel, then cache.
    missing = [pid for pid in ids if not cached.get(pid)]
    if missing:
        try:
            papers = await asyncio.to_thread(get_papers_by_ids, missing)
        except DatabaseError as exc:
            raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc

        async def _translate_one(pid: str) -> tuple[str, str | None]:
            paper = papers.get(pid)
            abstract = (paper or {}).get("abstract") if paper else None
            if not abstract:
                return pid, None
            try:
                zh = await translate_abstract(llm, abstract)
                return pid, zh or None
            except Exception as exc:  # noqa: BLE001
                logger.warning("摘要翻译失败 paper=%s: %s", pid, exc)
                return pid, None

        results = await asyncio.gather(*[_translate_one(pid) for pid in missing])
        for pid, zh in results:
            if zh:
                cached[pid] = zh
                try:
                    await asyncio.to_thread(set_abstract_zh, pid, zh)
                except DatabaseError:
                    pass  # translation worked; caching is best-effort

    return {"results": {pid: cached.get(pid) for pid in ids}}


class LibraryExportRequest(BaseModel):
    paper_ids: list[str] | None = None  # None = all marked papers


class LibraryImportPreviewRequest(BaseModel):
    payload: dict


class LibraryImportRequest(BaseModel):
    payload: dict
    paper_ids: list[str]


def _transfer_error_http(exc: Exception) -> HTTPException:
    if isinstance(exc, PayloadTooLargeError):
        return HTTPException(status_code=413, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@app.get("/me/library/export-info")
async def get_library_export_info(user: dict = Depends(require_current_user)):
    try:
        return await asyncio.to_thread(library_transfer.build_export_info, user["id"])
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc


@app.post("/me/library/export")
async def export_library(req: LibraryExportRequest, user: dict = Depends(require_current_user)):
    paper_ids = [pid for pid in (req.paper_ids or []) if pid] or None
    try:
        payload = await asyncio.to_thread(
            library_transfer.build_export_payload, user["id"], paper_ids,
        )
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc
    if not payload.get("papers"):
        raise HTTPException(status_code=400, detail="论文库为空，没有可导出的论文")
    return payload


class AutoBackupSettingsRequest(BaseModel):
    enabled: bool
    directory: str = ""
    interval_kind: str = "daily"


def _backup_settings_response(settings: dict) -> dict:
    """Settings plus the server-computed next run time (the client never
    derives it itself)."""
    next_backup_at = None
    if settings["enabled"] and settings["directory"]:
        interval = auto_backup.INTERVALS.get(settings["interval_kind"])
        if settings["last_backup_at"] is not None and interval is not None:
            next_backup_at = (settings["last_backup_at"] + interval).isoformat()
        else:
            next_backup_at = datetime.now(timezone.utc).isoformat()
    return {**settings, "next_backup_at": next_backup_at}


@app.get("/me/backup-settings")
async def get_my_backup_settings(user: dict = Depends(require_current_user)):
    try:
        settings = await asyncio.to_thread(auto_backup.get_auto_backup_settings, user["id"])
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc
    return _backup_settings_response(settings)


@app.put("/me/backup-settings")
async def update_my_backup_settings(
    req: AutoBackupSettingsRequest,
    user: dict = Depends(require_current_user),
):
    try:
        settings = await asyncio.to_thread(
            auto_backup.upsert_auto_backup_settings,
            user["id"],
            enabled=req.enabled,
            directory=req.directory,
            interval_kind=req.interval_kind,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc
    return _backup_settings_response(settings)


@app.post("/me/backup/run")
async def run_my_backup_now(user: dict = Depends(require_current_user)):
    """Run one backup immediately (synchronous — a local file write takes
    well under a second, and the client gets the fresh status back)."""
    try:
        settings = await asyncio.to_thread(auto_backup.get_auto_backup_settings, user["id"])
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc
    if not settings["directory"]:
        raise HTTPException(status_code=400, detail="请先配置并保存备份目录")
    try:
        return await asyncio.to_thread(auto_backup.run_backup, user["id"], settings["directory"])
    except auto_backup.BackupConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc
    except Exception as exc:  # noqa: BLE001 — run_backup already recorded the failure
        raise HTTPException(status_code=500, detail=f"备份失败：{str(exc)[:200]}") from exc


@app.post("/me/library/import/preview")
async def preview_library_import(req: LibraryImportPreviewRequest, request: Request):
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > library_transfer.MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="文件超过 64MB 上限")
    try:
        payload = library_transfer.validate_payload(req.payload)
    except library_transfer.TransferError as exc:
        raise _transfer_error_http(exc) from exc
    user = require_current_user(request)
    try:
        return await asyncio.to_thread(library_transfer.preview_import, payload, user["id"])
    except library_transfer.TransferError as exc:
        raise _transfer_error_http(exc) from exc


@app.post("/me/library/import")
async def import_library(req: LibraryImportRequest, request: Request):
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > library_transfer.MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="文件超过 64MB 上限")
    # Re-validate fully — never trust state from the preview step.
    try:
        payload = library_transfer.validate_payload(req.payload)
    except library_transfer.TransferError as exc:
        raise _transfer_error_http(exc) from exc
    selected = [pid for pid in req.paper_ids if pid]
    try:
        return await asyncio.to_thread(
            library_transfer.apply_import, payload, selected, require_current_user(request)["id"],
        )
    except library_transfer.TransferError as exc:
        raise _transfer_error_http(exc) from exc



def get_or_fetch_paper_info(paper_id: str) -> dict:
    """Get paper from database, or fetch from OpenReview if not exists."""
    cached = get_paper(paper_id)
    if cached:
        return cached

    arxiv_id = arxiv_id_from_paper_id(paper_id)
    if arxiv_id:
        arxiv_payload = fetch_arxiv_paper(arxiv_id)
        return upsert_arxiv_paper(arxiv_payload["paper"], arxiv_payload["arxiv"])
    # Non-arXiv ids have no fetch path in this build — must already be in DB.
    raise OpenReviewError("Paper not found")


def _openreview_error_status(error: OpenReviewError) -> int:
    return 404 if str(error) == "Paper not found" else 502


@app.get("/paper/{paper_id}/open-in-ai-prompt")
async def get_paper_open_in_ai_prompt(paper_id: str):
    """Prompt used by the paper page's "Open in AI" deep links."""
    try:
        paper_info = get_or_fetch_paper_info(paper_id)
    except ArxivInvalidInputError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ArxivNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ArxivError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except OpenReviewError as e:
        raise HTTPException(status_code=_openreview_error_status(e), detail=str(e))
    except DatabaseError as e:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from e

    pdf_url = paper_info.get("pdf") or f"https://openreview.net/pdf?id={paper_id}"
    return {"prompt": build_open_in_ai_prompt(pdf_url)}


@app.get("/paper/{paper_id}/info")
async def get_paper_info(paper_id: str):
    """获取论文基本信息"""
    try:
        paper_info = get_or_fetch_paper_info(paper_id)
    except ArxivInvalidInputError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ArxivNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ArxivError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except OpenReviewError as e:
        raise HTTPException(status_code=_openreview_error_status(e), detail=str(e))
    except DatabaseError as e:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from e

    if not paper_info:
        raise HTTPException(status_code=404, detail="Paper not found")

    return paper_info




def _stream_file_then_delete(path: str):
    try:
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(65536)
                if not chunk:
                    break
                yield chunk
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass




def _curl_pdf_to_file(pdf_url: str, tmp_path: str) -> int:
    """Fetch a PDF via curl into tmp_path; return the upstream HTTP status code."""
    cmd = [
        # arXiv downloads can be very slow / stall from some networks; allow up
        # to 3 min, retry transient failures, and abort only if truly stalled
        # (<2KB/s for 60s) so we don't wait the full timeout on dead links.
        "curl", "-sSL", "--max-time", "180", "--retry", "2", "--retry-delay", "3",
        "--speed-time", "60", "--speed-limit", "2000",
        "-w", "\n%{http_code}", "-o", tmp_path, pdf_url,
        "-H", "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=200)
    status_tail = (proc.stdout or "").strip().rsplit("\n", 1)[-1] if proc.stdout else "000"
    return int(status_tail) if status_tail.isdigit() else 0


@app.get("/paper/{paper_id}/pdf")
async def proxy_paper_pdf(paper_id: str):
    """Stream a paper's PDF via curl (public hosts; no OpenReview auth path)."""
    paper = await asyncio.to_thread(get_paper, paper_id)
    stored_pdf = (paper or {}).get("pdf") if paper else None
    if isinstance(stored_pdf, str) and stored_pdf.startswith("http"):
        pdf_url = stored_pdf
    else:
        raise HTTPException(status_code=404, detail="论文没有可用的 PDF 链接")

    def _fetch_once() -> int:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp_path = tmp.name
        try:
            return _curl_pdf_to_file(pdf_url, tmp_path), tmp_path
        except subprocess.TimeoutExpired:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise _PdfTimeout()

    try:
        status_code, tmp_path = await asyncio.to_thread(_fetch_once)
    except _PdfTimeout:
        raise HTTPException(status_code=504, detail="获取 PDF 超时")

    if status_code == 0 or status_code >= 400 or not os.path.exists(tmp_path):
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise HTTPException(
            status_code=502,
            detail=f"无法获取 PDF（上游返回 {status_code or 'error'}）",
        )

    return StreamingResponse(
        _stream_file_then_delete(tmp_path),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{paper_id}.pdf"'},
    )


class _PdfTimeout(Exception):
    pass



_background_analysis_tasks: set[asyncio.Task] = set()


def _schedule_background_analysis(paper_id: str) -> None:
    """Kick off AI analysis for a freshly added paper without blocking the
    response. Deduplicates concurrent runs; SSE remains the authoritative
    path when the user actually opens the paper (cached results win)."""
    if not llm.is_configured():
        return
    if any(not task.done() and task.get_name() == f"bg-analysis:{paper_id}" for task in _background_analysis_tasks):
        return

    async def _run() -> None:
        try:
            await background_analyzer.analyze_paper(paper_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("后台分析失败 paper=%s: %s", paper_id, exc)
        finally:
            logger.info("后台分析任务结束 paper=%s", paper_id)

    task = asyncio.create_task(_run(), name=f"bg-analysis:{paper_id}")
    _background_analysis_tasks.add(task)
    task.add_done_callback(_background_analysis_tasks.discard)


@app.post("/arxiv-papers")
async def create_arxiv_paper(req: ArxivPaperRequest, request: Request):
    try:
        user = get_current_user_optional(request)
    except DatabaseError as e:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from e

    added_by_user_id = user["id"] if user else None

    try:
        arxiv_payload = await asyncio.to_thread(fetch_arxiv_paper, req.input)
        paper = await asyncio.to_thread(
            upsert_arxiv_paper,
            arxiv_payload["paper"],
            arxiv_payload["arxiv"],
            added_by_user_id,
        )
    except ArxivInvalidInputError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ArxivNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ArxivError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except DatabaseError as e:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from e

    # 添加即入「我的论文」：不等 AI 分析（AI 分析在详情页打开后才流式进行，
    # 与入列无关）。用户把链接丢进来就期望它出现在列表里——但「看过」是用户
    # 手动标记，添加本身只入列（全 false 的 mark 行），不算看过。
    if added_by_user_id:
        try:
            await asyncio.to_thread(set_paper_mark, added_by_user_id, paper["id"], viewed=False)
        except DatabaseError:
            logger.warning("添加论文后写入标记失败 paper=%s", paper["id"])

    # 后台预跑 AI 分析（fire-and-forget）：用户添加后不必停在详情页等流式输出，
    # 回来时若分析已完成则秒出结果。与详情页 SSE 的缓存逻辑天然兼容。
    _schedule_background_analysis(paper["id"])

    return {"paper": paper}


@app.post("/manual-papers")
async def create_manual_paper(req: ManualPaperRequest, request: Request):
    """Add a hand-entered paper (arXiv-unreachable sources).

    Mirrors /arxiv-papers: insert, enter My Papers with an all-false mark
    (adding ≠ viewed), then kick off the background AI analysis — full text
    when a pdf link was given, metadata-based screening otherwise.
    """
    try:
        user = get_current_user_optional(request)
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc
    added_by_user_id = user["id"] if user else None

    try:
        paper = await asyncio.to_thread(upsert_manual_paper, req.model_dump())
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from exc

    if added_by_user_id:
        try:
            await asyncio.to_thread(set_paper_mark, added_by_user_id, paper["id"], viewed=False)
        except DatabaseError:
            logger.warning("手动添加论文后写入标记失败 paper=%s", paper["id"])

    _schedule_background_analysis(paper["id"])

    return {"paper": paper}


@app.get("/paper/{paper_id}")
async def get_paper_analysis(paper_id: str, reanalyze: bool = False):
    async def generate():
        if not llm.is_configured():
            yield {"event": "error", "data": "config.yaml 未配置有效 LLM API key"}
            return

        # Ensure paper exists in database
        yield {"event": "status", "data": "正在获取论文信息..."}
        try:
            paper_info = await asyncio.to_thread(get_or_fetch_paper_info, paper_id)
        except ArxivInvalidInputError as e:
            yield {"event": "error", "data": str(e)}
            return
        except ArxivNotFoundError as e:
            yield {"event": "error", "data": str(e)}
            return
        except ArxivError as e:
            yield {"event": "error", "data": str(e)}
            return
        except OpenReviewError as e:
            yield {"event": "error", "data": str(e)}
            return
        except DatabaseError:
            yield {"event": "error", "data": "数据库暂时不可用，请稍后重试"}
            return

        # Check if we can return cached analysis
        if not reanalyze and paper_info.get("llm_response"):
            normalized_response = normalize_llm_markdown(paper_info["llm_response"], analysis_mode=True)
            if normalized_response != paper_info["llm_response"]:
                await asyncio.to_thread(update_llm_response, paper_id, normalized_response)
                paper_info["llm_response"] = normalized_response
            if not paper_info.get("code_checked_at"):
                await background_analyzer.update_code_availability(paper_info, normalized_response)
            yield {"data": normalized_response}
            yield {"event": "done", "data": ""}
            return

        # Perform AI analysis
        yield {"event": "status", "data": "正在读取 PDF 内容..."}
        paper_content = None
        content_error = None
        if paper_info.get("pdf"):
            try:
                paper_content = await asyncio.to_thread(
                    get_or_cache_paper_content,
                    paper_id,
                    paper_info["pdf"],
                )
                paper_content = truncate_content_for_llm(paper_content)
            except ReaderError as e:
                content_error = str(e)
                yield {"event": "status", "data": "PDF 正文读取失败，正在基于论文元数据分析..."}
        else:
            content_error = "论文没有可用 PDF 链接"
            yield {"event": "status", "data": "未找到 PDF 链接，正在基于论文元数据分析..."}

        yield {"event": "status", "data": "正在分析论文..."}

        user_prompt = build_analysis_prompt(paper_info, paper_content, content_error)

        full_response = []
        async for stream_chunk in llm.get_response_stream_events(user_prompt):
            if stream_chunk.kind == "reasoning":
                yield {"event": "reasoning", "data": stream_chunk.content}
                continue
            full_response.append(stream_chunk.content)
            yield {"data": stream_chunk.content}

        normalized_response = normalize_llm_markdown("".join(full_response), analysis_mode=True)
        await asyncio.to_thread(update_llm_response, paper_id, normalized_response)
        paper_info["llm_response"] = normalized_response
        await background_analyzer.update_code_availability(paper_info, normalized_response)
        yield {"event": "final", "data": normalized_response}
        yield {"event": "done", "data": ""}

    return EventSourceResponse(generate())


@app.post("/paper/{paper_id}/chat")
async def chat_with_paper(
    paper_id: str,
    req: ChatRequest,
    user: dict = Depends(require_current_user),
):
    ensure_llm_configured()
    session_row = assert_chat_owner(req.session_id, user["id"])
    session = chat_sessions.get(req.session_id)
    is_new_session = session_row is None

    if not session:
        try:
            paper_info = await asyncio.to_thread(get_or_fetch_paper_info, paper_id)
        except ArxivInvalidInputError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except ArxivNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ArxivError as e:
            raise HTTPException(status_code=502, detail=str(e))
        except OpenReviewError as e:
            raise HTTPException(status_code=_openreview_error_status(e), detail=str(e))
        except DatabaseError as e:
            raise HTTPException(status_code=502, detail="Database temporarily unavailable") from e

        context_parts = []
        paper_content = None
        content_error = None
        if paper_info.get("pdf"):
            try:
                paper_content = await asyncio.to_thread(
                    get_or_cache_paper_content,
                    paper_id,
                    paper_info["pdf"],
                )
                paper_content = truncate_content_for_llm(paper_content)
            except ReaderError as e:
                content_error = str(e)
        else:
            content_error = "论文没有可用 PDF 链接"
        context_parts.extend(build_chat_context_parts(paper_info, paper_content, content_error))
        if paper_info.get("llm_response"):
            context_parts.append(f"论文分析：\n{paper_info['llm_response']}")

        history_rows = get_chat_messages(req.session_id) if session_row else []
        if history_rows:
            history = [{"role": r["role"], "content": r["content"]} for r in history_rows]
        else:
            history = None

        session = ChatSession(llm, context="\n\n".join(context_parts), history=history)
        chat_sessions[req.session_id] = session

    async def generate():
        try:
            if is_new_session:
                create_chat_session(
                    req.session_id,
                    user["id"],
                    paper_id,
                    req.message[:50],
                    account_user_id=user["id"],
                )

            chunks = []
            async for stream_chunk in session.send_stream_events(req.message):
                if stream_chunk.kind == "reasoning":
                    yield {"event": "reasoning", "data": stream_chunk.content}
                    continue
                chunks.append(stream_chunk.content)
                yield {"data": stream_chunk.content}

            # Persist messages
            normalized_reply = normalize_llm_markdown("".join(chunks))
            save_chat_message(req.session_id, "user", req.message)
            save_chat_message(req.session_id, "assistant", normalized_reply)

            yield {"event": "final", "data": normalized_reply}
            yield {"event": "done", "data": ""}
        except DatabaseError:
            yield {"event": "error", "data": "数据库暂时不可用，请稍后重试"}

    return EventSourceResponse(generate())


@app.get("/paper/{paper_id}/chat/sessions")
async def list_chat_sessions(paper_id: str, request: Request):
    try:
        user = get_current_user_optional(request)
        if not user:
            return []
        return get_chat_sessions_for_account(user["id"], paper_id)
    except DatabaseError as e:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from e


@app.get("/chat/{session_id}/messages")
async def list_chat_messages(session_id: str, user: dict = Depends(require_current_user)):
    try:
        assert_chat_owner(session_id, user["id"])
        return get_chat_messages(session_id)
    except DatabaseError as e:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from e


@app.delete("/chat/{session_id}")
async def delete_session(session_id: str, user: dict = Depends(require_current_user)):
    chat_sessions.pop(session_id, None)
    try:
        assert_chat_owner(session_id, user["id"])
        delete_chat_session(session_id)
    except DatabaseError as e:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from e
    return {"ok": True}




@app.post("/paper/{paper_id}/chat/regenerate")
async def regenerate_chat(
    paper_id: str,
    req: ChatRequest,
    user: dict = Depends(require_current_user),
):
    """Delete last message pair, then re-send the user message."""
    ensure_llm_configured()
    session_row = assert_chat_owner(req.session_id, user["id"])
    if not session_row:
        raise HTTPException(status_code=404, detail="会话不存在")

    session = chat_sessions.get(req.session_id)
    if session and len(session.history) >= 2:
        session.history = session.history[:-2]
    else:
        chat_sessions.pop(req.session_id, None)
        session = None

    try:
        delete_last_chat_message_pair(req.session_id)
    except DatabaseError as e:
        raise HTTPException(status_code=502, detail="Database temporarily unavailable") from e

    if not session:
        try:
            paper_info = await asyncio.to_thread(get_or_fetch_paper_info, paper_id)
        except ArxivInvalidInputError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except ArxivNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ArxivError as e:
            raise HTTPException(status_code=502, detail=str(e))
        except OpenReviewError as e:
            raise HTTPException(status_code=_openreview_error_status(e), detail=str(e))
        except DatabaseError as e:
            raise HTTPException(status_code=502, detail="Database temporarily unavailable") from e

        context_parts = []
        paper_content = None
        content_error = None
        if paper_info.get("pdf"):
            try:
                paper_content = await asyncio.to_thread(
                    get_or_cache_paper_content,
                    paper_id,
                    paper_info["pdf"],
                )
                paper_content = truncate_content_for_llm(paper_content)
            except ReaderError as e:
                content_error = str(e)
        else:
            content_error = "论文没有可用 PDF 链接"
        context_parts.extend(build_chat_context_parts(paper_info, paper_content, content_error))
        if paper_info.get("llm_response"):
            context_parts.append(f"论文分析：\n{paper_info['llm_response']}")
        history_rows = get_chat_messages(req.session_id)
        history = [{"role": r["role"], "content": r["content"]} for r in history_rows] if history_rows else None
        session = ChatSession(llm, context="\n\n".join(context_parts), history=history)
        chat_sessions[req.session_id] = session

    async def generate():
        try:
            chunks = []
            async for stream_chunk in session.send_stream_events(req.message):
                if stream_chunk.kind == "reasoning":
                    yield {"event": "reasoning", "data": stream_chunk.content}
                    continue
                chunks.append(stream_chunk.content)
                yield {"data": stream_chunk.content}

            normalized_reply = normalize_llm_markdown("".join(chunks))
            save_chat_message(req.session_id, "user", req.message)
            save_chat_message(req.session_id, "assistant", normalized_reply)

            yield {"event": "final", "data": normalized_reply}
            yield {"event": "done", "data": ""}
        except DatabaseError:
            yield {"event": "error", "data": "数据库暂时不可用，请稍后重试"}

    return EventSourceResponse(generate())











# 静态文件服务
REACT_FRONTEND_DIST_DIR = Path(__file__).parent.parent / "frontend-react" / "dist"
IMAGES_DIR = Path(__file__).parent.parent / "images"
CHANGELOG_PATH = Path(__file__).parent.parent / "changelog.md"


def get_frontend_index() -> Path:
    react_index = REACT_FRONTEND_DIST_DIR / "index.html"
    if react_index.exists():
        return react_index
    raise HTTPException(
        status_code=503,
        detail="Frontend build not found. Run `cd frontend-react && npm run build` first.",
    )


# The SPA entry must always revalidate: it references content-hashed asset
# URLs, so a heuristically cached copy would pin users to an old build after
# a deploy. Hashed assets under /assets keep their long cache lifetime.
FRONTEND_INDEX_HEADERS = {"Cache-Control": "no-cache"}


app.mount("/images", StaticFiles(directory=IMAGES_DIR), name="images")
app.mount("/assets", StaticFiles(directory=REACT_FRONTEND_DIST_DIR / "assets", check_dir=False), name="assets")


@app.get("/")
async def serve_frontend():
    return FileResponse(get_frontend_index(), headers=FRONTEND_INDEX_HEADERS)


@app.get("/me")
async def serve_me_frontend():
    return FileResponse(get_frontend_index(), headers=FRONTEND_INDEX_HEADERS)


@app.get("/papers/{paper_id}")
async def serve_paper_frontend(paper_id: str):
    return FileResponse(get_frontend_index(), headers=FRONTEND_INDEX_HEADERS)
