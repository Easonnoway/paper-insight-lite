"""Automatic library backup (periodic full export to a user-chosen directory).

Settings live in user_auto_backup_settings (db/migrations/028_auto_backup_settings.sql),
one row per user. A background asyncio loop (run_backup_loop, started from the
app lifespan) polls every CHECK_INTERVAL_SECONDS and runs a backup when the
configured interval has elapsed since the last attempt. Manual "back up now"
shares the same run_backup core.

All functions follow the database.py conventions (see paper_categories.py):
_run_with_retry wrapping, DatabaseError on infrastructure failures, ValueError
for user-facing validation problems (app.py -> 4xx).

Semantics note: last_backup_at is the last *attempt* time — failures refresh
it too, so a broken directory cannot cause a per-minute retry storm. The
trade-off is that the next automatic retry waits a full interval; the manual
"back up now" button is the escape hatch.
"""

import asyncio
import logging
import os
import threading
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path

import database
import library_transfer
from database import DatabaseError, NoRetryError, _run_with_retry

logger = logging.getLogger(__name__)

BACKUP_FILE_PREFIX = "paper-library"
BACKUP_FILE_SUFFIX = ".json"
KEEP_COUNT = 10
CHECK_INTERVAL_SECONDS = 60
PROBE_FILE_NAME = ".paper-insight-lite-backup-probe"
MAX_ERROR_LENGTH = 500

INTERVALS: dict[str, timedelta] = {
    "daily": timedelta(days=1),
    "every_3_days": timedelta(days=3),
    "weekly": timedelta(weeks=1),
}

DEFAULT_SETTINGS = {
    "enabled": False,
    "directory": "",
    "interval_kind": "daily",
    "last_backup_at": None,
    "last_backup_status": None,
    "last_backup_error": None,
}


class BackupConflictError(NoRetryError):
    """A backup is already running: surface as HTTP 409."""


_backup_lock = threading.Lock()


# ---------- settings CRUD ----------

def _normalize_settings_row(row: dict) -> dict:
    return {
        "enabled": bool(row["enabled"]),
        "directory": row["directory"],
        "interval_kind": row["interval_kind"],
        "last_backup_at": row.get("last_backup_at"),
        "last_backup_status": row.get("last_backup_status"),
        "last_backup_error": row.get("last_backup_error"),
    }


def get_auto_backup_settings(user_id: str) -> dict:
    """The user's backup settings; defaults when never configured (no 404)."""

    def operation() -> dict:
        with database._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT enabled, directory, interval_kind,
                           last_backup_at, last_backup_status, last_backup_error
                    FROM user_auto_backup_settings
                    WHERE user_id = %s
                    """,
                    (user_id,),
                )
                row = cur.fetchone()
        return _normalize_settings_row(row) if row else dict(DEFAULT_SETTINGS)

    return _run_with_retry(operation, f"get_auto_backup_settings:{user_id}")


def validate_backup_directory(directory: str) -> Path:
    """Require an absolute path we can create and write to (mkdir + probe)."""
    path = Path(directory)
    if not path.is_absolute():
        raise ValueError("备份目录必须是绝对路径（如 /Users/you/Backups）")
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / PROBE_FILE_NAME
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise ValueError(f"目录不可写：{exc}") from exc
    return path


def upsert_auto_backup_settings(
    user_id: str,
    *,
    enabled: bool,
    directory: str,
    interval_kind: str,
) -> dict:
    """Insert or update the settings row (config columns only — the last_*
    status columns are owned by _record_backup_result)."""
    if interval_kind not in INTERVALS:
        raise ValueError("备份间隔仅支持 daily、every_3_days、weekly")
    normalized_directory = (directory or "").strip()
    if enabled and not normalized_directory:
        raise ValueError("启用自动备份前请先填写备份目录")
    if normalized_directory:
        validate_backup_directory(normalized_directory)

    def operation() -> dict:
        with database._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO user_auto_backup_settings
                        (user_id, enabled, directory, interval_kind)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET
                        enabled = EXCLUDED.enabled,
                        directory = EXCLUDED.directory,
                        interval_kind = EXCLUDED.interval_kind,
                        updated_at = NOW()
                    RETURNING enabled, directory, interval_kind,
                              last_backup_at, last_backup_status, last_backup_error
                    """,
                    (user_id, enabled, normalized_directory, interval_kind),
                )
                row = cur.fetchone()
            conn.commit()
        return _normalize_settings_row(row)

    return _run_with_retry(operation, f"upsert_auto_backup_settings:{user_id}")


# ---------- pure helpers ----------

def build_backup_file_name(now_local: datetime) -> str:
    """Timestamped file name in local time (Finder-friendly)."""
    return f"{BACKUP_FILE_PREFIX}-{now_local.strftime('%Y%m%d-%H%M')}{BACKUP_FILE_SUFFIX}"


def is_backup_due(
    *,
    enabled: bool,
    directory: str,
    last_backup_at: datetime | None,
    interval_kind: str,
    now: datetime,
) -> bool:
    """Whether a backup should run now. Never-backed-up enabled settings are
    immediately due (covers the restart catch-up too — the loop's first tick
    runs right after startup)."""
    if not enabled or not directory:
        return False
    interval = INTERVALS.get(interval_kind)
    if interval is None:
        return False
    if last_backup_at is None:
        return True
    return now >= last_backup_at + interval


def list_backup_files(directory: Path) -> list[Path]:
    """Backup files sorted by name — the timestamped names make lexicographic
    order equal chronological order."""
    return sorted(directory.glob(f"{BACKUP_FILE_PREFIX}-*{BACKUP_FILE_SUFFIX}"))


def prune_old_backups(directory: Path, keep: int = KEEP_COUNT) -> list[str]:
    """Keep the newest `keep` backups, delete the rest. Returns removed names."""
    files = list_backup_files(directory)
    removed = []
    for old in files[:-keep] if len(files) > keep else []:
        try:
            old.unlink()
            removed.append(old.name)
        except OSError as exc:
            logger.warning("删除旧备份失败 %s: %s", old.name, exc)
    return removed


def _atomic_write_text(path: Path, content: str) -> None:
    # Same tmp+replace pattern as utils._atomic_write_text (kept local so the
    # utils export surface stays untouched).
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)


def _unique_backup_name(directory: Path, file_name: str) -> str:
    """Append -2, -3, ... when the same-minute file already exists."""
    candidate = directory / file_name
    stem, suffix = file_name.rsplit(BACKUP_FILE_SUFFIX, 1)
    counter = 2
    while candidate.exists():
        candidate = directory / f"{stem}-{counter}{BACKUP_FILE_SUFFIX}"
        counter += 1
    return candidate.name


def _to_local(now_utc: datetime) -> datetime:
    return now_utc.astimezone()


def _record_backup_result(
    user_id: str,
    attempted_at: datetime,
    status: str,
    error: str | None,
) -> None:
    """Update only the status columns; config columns belong to upsert."""

    def operation() -> None:
        with database._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE user_auto_backup_settings
                    SET last_backup_at = %s,
                        last_backup_status = %s,
                        last_backup_error = %s,
                        updated_at = NOW()
                    WHERE user_id = %s
                    """,
                    (attempted_at, status, error, user_id),
                )
            conn.commit()

    _run_with_retry(operation, f"record_backup_result:{user_id}")


# ---------- core backup (shared by manual run and the scheduler) ----------

def run_backup(user_id: str, directory: str, *, now: datetime | None = None) -> dict:
    """Export the full library into `directory` and record the outcome.

    Returns {"file_name", "file_path", "pruned", "last_backup_at"}.
    Raises BackupConflictError if a backup is already running; any other
    exception is recorded as a failed attempt and re-raised.

    An empty library still produces a backup file: the backup should reflect
    the real state, and importing it legitimately restores an empty library
    (unlike the manual export endpoint, which rejects empty exports).
    """
    now = now or datetime.now(timezone.utc)
    if not _backup_lock.acquire(blocking=False):
        raise BackupConflictError("已有一次备份正在执行")
    try:
        payload = library_transfer.build_export_payload(user_id)
        content = library_transfer.dumps_payload(payload)
        target_dir = Path(directory)
        file_name = _unique_backup_name(target_dir, build_backup_file_name(_to_local(now)))
        _atomic_write_text(target_dir / file_name, content)
        pruned = prune_old_backups(target_dir)
        _record_backup_result(user_id, now, "success", None)
        return {
            "file_name": file_name,
            "file_path": str(target_dir / file_name),
            "pruned": pruned,
            "last_backup_at": now.isoformat(),
        }
    except Exception as exc:
        _record_backup_result(user_id, now, "failed", str(exc)[:MAX_ERROR_LENGTH])
        raise
    finally:
        _backup_lock.release()


# ---------- scheduler loop ----------

def find_enabled_backup_settings() -> dict | None:
    """The enabled settings row (single-user library: at most one)."""

    def operation() -> dict | None:
        with database._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT user_id, directory, interval_kind, last_backup_at
                    FROM user_auto_backup_settings
                    WHERE enabled = TRUE AND directory <> ''
                    LIMIT 1
                    """,
                    (),
                )
                row = cur.fetchone()
        return dict(row) if row else None

    return _run_with_retry(operation, "find_enabled_backup_settings")


async def run_backup_tick() -> None:
    """One poll of the scheduler: run a backup if one is due."""
    row = await asyncio.to_thread(find_enabled_backup_settings)
    if row is None:
        return
    if not is_backup_due(
        enabled=True,
        directory=row["directory"],
        last_backup_at=row["last_backup_at"],
        interval_kind=row["interval_kind"],
        now=datetime.now(timezone.utc),
    ):
        return
    try:
        result = await asyncio.to_thread(run_backup, str(row["user_id"]), row["directory"])
        logger.info("自动备份完成: %s", result["file_name"])
    except BackupConflictError:
        # A manual backup is running; the next tick re-checks naturally.
        pass


async def run_backup_loop() -> None:
    """Poll forever. Only CancelledError escapes (via BaseException semantics);
    every other failure is logged and retried next tick. Settings are re-read
    each tick, so saved changes take effect within CHECK_INTERVAL_SECONDS."""
    logger.info("自动备份循环已启动（每 %ss 检查一次到期）", CHECK_INTERVAL_SECONDS)
    while True:
        try:
            await run_backup_tick()
        except Exception as exc:  # noqa: BLE001 — the loop must never exit
            logger.warning("自动备份 tick 失败: %s", exc)
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


__all__ = [
    "BackupConflictError",
    "BACKUP_FILE_PREFIX",
    "CHECK_INTERVAL_SECONDS",
    "KEEP_COUNT",
    "build_backup_file_name",
    "find_enabled_backup_settings",
    "get_auto_backup_settings",
    "is_backup_due",
    "list_backup_files",
    "prune_old_backups",
    "run_backup",
    "run_backup_loop",
    "run_backup_tick",
    "upsert_auto_backup_settings",
    "validate_backup_directory",
]
