import asyncio
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pytest

import app as app_module
import auto_backup
import database
from auto_backup import (
    BackupConflictError,
    KEEP_COUNT,
    _backup_lock,
    build_backup_file_name,
    is_backup_due,
    list_backup_files,
    prune_old_backups,
    run_backup,
    run_backup_tick,
    validate_backup_directory,
)


# ---------- fake connection (pattern from test_paper_notes.py) ----------

class SettingsCursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = []
        self.current_rows = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, query, params=None):
        self.calls.append((" ".join(query.split()), params))
        self.current_rows = list(self.rows)

    def fetchone(self):
        return self.current_rows[0] if self.current_rows else None

    def fetchall(self):
        return list(self.current_rows)


class SettingsConnection:
    def __init__(self, cursor):
        self.cursor_instance = cursor
        self.commits = 0

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1


def install(monkeypatch, cursor):
    connection = SettingsConnection(cursor)

    @contextmanager
    def fake_get_connection():
        yield connection

    monkeypatch.setattr(database, "_get_connection", fake_get_connection)
    return connection


def settings_row(**overrides):
    row = {
        "enabled": True,
        "directory": "/tmp/some-backup",
        "interval_kind": "daily",
        "last_backup_at": None,
        "last_backup_status": None,
        "last_backup_error": None,
    }
    row.update(overrides)
    return row


# ---------- settings CRUD ----------

def test_get_settings_returns_defaults_without_row(monkeypatch):
    cursor = SettingsCursor(rows=[])
    install(monkeypatch, cursor)

    settings = auto_backup.get_auto_backup_settings("user-1")

    assert settings == {
        "enabled": False,
        "directory": "",
        "interval_kind": "daily",
        "last_backup_at": None,
        "last_backup_status": None,
        "last_backup_error": None,
    }


def test_get_settings_maps_row(monkeypatch):
    cursor = SettingsCursor(rows=[settings_row()])
    install(monkeypatch, cursor)

    settings = auto_backup.get_auto_backup_settings("user-1")

    assert settings["enabled"] is True
    assert settings["directory"] == "/tmp/some-backup"


def test_upsert_uses_on_conflict_and_skips_status_columns(monkeypatch):
    cursor = SettingsCursor(rows=[settings_row()])
    install(monkeypatch, cursor)

    auto_backup.upsert_auto_backup_settings(
        "user-1", enabled=True, directory="/tmp/bk", interval_kind="weekly",
    )

    sql, params = cursor.calls[0]
    assert "ON CONFLICT (user_id) DO UPDATE SET" in sql
    # The status columns are never written by the config upsert.
    update_section = sql.split("DO UPDATE SET", 1)[1].split("RETURNING", 1)[0]
    assert "last_backup_at" not in update_section
    assert ("user-1", True, "/tmp/bk", "weekly") == params


def test_upsert_rejects_unknown_interval(monkeypatch):
    install(monkeypatch, SettingsCursor())

    with pytest.raises(ValueError):
        auto_backup.upsert_auto_backup_settings(
            "user-1", enabled=True, directory="/tmp/bk", interval_kind="hourly",
        )


def test_upsert_rejects_enabled_with_empty_directory(monkeypatch):
    install(monkeypatch, SettingsCursor())

    with pytest.raises(ValueError):
        auto_backup.upsert_auto_backup_settings(
            "user-1", enabled=True, directory="  ", interval_kind="daily",
        )


# ---------- directory validation (tmp_path) ----------

def test_validate_backup_directory_creates_and_probes(tmp_path):
    target = tmp_path / "nested" / "backup"
    result = validate_backup_directory(str(target))

    assert result.is_dir()
    assert not (target / auto_backup.PROBE_FILE_NAME).exists()


def test_validate_backup_directory_rejects_relative_path(tmp_path):
    with pytest.raises(ValueError) as excinfo:
        validate_backup_directory("relative/dir")
    assert "绝对路径" in str(excinfo.value)


def test_validate_backup_directory_rejects_unwritable(tmp_path):
    readonly = tmp_path / "locked"
    readonly.mkdir()
    readonly.chmod(0o555)
    try:
        with pytest.raises(ValueError) as excinfo:
            validate_backup_directory(str(readonly / "sub"))
        assert "目录不可写" in str(excinfo.value)
    finally:
        readonly.chmod(0o755)


# ---------- pure helpers ----------

def test_build_backup_file_name_format():
    name = build_backup_file_name(datetime(2026, 9, 18, 8, 5))
    assert name == "paper-library-20260918-0805.json"


def test_is_backup_due_matrix():
    now = datetime(2026, 9, 18, 12, tzinfo=timezone.utc)
    kwargs = {"enabled": True, "directory": "/tmp/bk", "now": now}

    # Never backed up -> immediately due (restart catch-up).
    assert is_backup_due(last_backup_at=None, interval_kind="daily", **kwargs)
    # 30h ago with a daily interval -> due.
    assert is_backup_due(
        last_backup_at=now - timedelta(hours=30), interval_kind="daily", **kwargs)
    # 20h ago with a daily interval -> not yet.
    assert not is_backup_due(
        last_backup_at=now - timedelta(hours=20), interval_kind="daily", **kwargs)
    # 20h ago with every_3_days -> not yet.
    assert not is_backup_due(
        last_backup_at=now - timedelta(hours=20), interval_kind="every_3_days", **kwargs)
    # Exactly at the interval boundary -> due.
    assert is_backup_due(
        last_backup_at=now - timedelta(days=3), interval_kind="every_3_days", **kwargs)
    # Disabled / empty directory / unknown interval never run.
    assert not is_backup_due(
        enabled=False, directory="/tmp/bk", last_backup_at=None,
        interval_kind="daily", now=now)
    assert not is_backup_due(
        enabled=True, directory="", last_backup_at=None,
        interval_kind="daily", now=now)
    assert not is_backup_due(
        enabled=True, directory="/tmp/bk", last_backup_at=None,
        interval_kind="hourly", now=now)


# ---------- core backup (tmp_path + stubbed export) ----------

@pytest.fixture()
def stub_export(monkeypatch):
    monkeypatch.setattr(
        auto_backup.library_transfer, "build_export_payload",
        lambda user_id, paper_ids=None: {"format": "x", "papers": [{"id": "p1"}]},
    )
    monkeypatch.setattr(
        auto_backup.library_transfer, "dumps_payload",
        lambda payload: '{"format":"x"}',
    )


def test_run_backup_writes_file_and_records_success(monkeypatch, tmp_path, stub_export):
    cursor = SettingsCursor()
    connection = install(monkeypatch, cursor)

    result = run_backup("user-1", str(tmp_path))

    assert (tmp_path / result["file_name"]).read_text() == '{"format":"x"}'
    assert result["pruned"] == []
    sql, params = cursor.calls[0]
    assert "SET last_backup_at" in sql
    assert "success" in params
    assert connection.commits == 1


def test_run_backup_records_failure_and_reraises(monkeypatch, tmp_path, stub_export):
    cursor = SettingsCursor()
    install(monkeypatch, cursor)

    def boom(path, content):
        raise OSError("disk full")

    monkeypatch.setattr(auto_backup, "_atomic_write_text", boom)

    with pytest.raises(OSError):
        run_backup("user-1", str(tmp_path))

    sql, params = cursor.calls[0]
    assert "failed" in params
    assert "disk full" in params


def test_run_backup_prunes_to_keep_count(monkeypatch, tmp_path, stub_export):
    install(monkeypatch, SettingsCursor())
    for i in range(12):
        (tmp_path / f"paper-library-202601{i:02d}-0000.json").write_text("old")

    result = run_backup("user-1", str(tmp_path))

    files = list_backup_files(tmp_path)
    assert len(files) == KEEP_COUNT
    # 12 pre-seeded + 1 fresh = 13 files; the oldest 3 are pruned.
    assert len(result["pruned"]) == 3
    assert "paper-library-20260100-0000.json" in result["pruned"]
    assert "paper-library-20260102-0000.json" in result["pruned"]


def test_run_backup_same_minute_collision_gets_suffix(monkeypatch, tmp_path, stub_export):
    install(monkeypatch, SettingsCursor())
    (tmp_path / "paper-library-20260918-0800.json").write_text("existing")

    result = run_backup(
        "user-1", str(tmp_path),
        now=datetime(2026, 9, 18, 0, 0, tzinfo=timezone.utc),
    )

    assert result["file_name"] == "paper-library-20260918-0800-2.json"


def test_run_backup_conflict_when_lock_held(monkeypatch, tmp_path, stub_export):
    install(monkeypatch, SettingsCursor())
    assert _backup_lock.acquire()
    try:
        with pytest.raises(BackupConflictError):
            run_backup("user-1", str(tmp_path))
    finally:
        _backup_lock.release()


def test_prune_ignores_foreign_files(monkeypatch, tmp_path):
    (tmp_path / "paper-library-garbage.json").write_text("odd")
    (tmp_path / "other.json").write_text("unrelated")

    removed = prune_old_backups(tmp_path, keep=2)

    assert removed == []
    assert (tmp_path / "paper-library-garbage.json").exists()


# ---------- scheduler tick ----------

def test_tick_runs_backup_when_due(monkeypatch):
    monkeypatch.setattr(
        auto_backup, "find_enabled_backup_settings",
        lambda: {"user_id": "u1", "directory": "/tmp/bk",
                 "interval_kind": "daily", "last_backup_at": None},
    )
    calls = []
    monkeypatch.setattr(
        auto_backup, "run_backup",
        lambda user_id, directory, **kw: calls.append((user_id, directory)) or {"file_name": "x.json"},
    )

    asyncio.run(run_backup_tick())

    assert calls == [("u1", "/tmp/bk")]


def test_tick_skips_when_not_due(monkeypatch):
    monkeypatch.setattr(
        auto_backup, "find_enabled_backup_settings",
        lambda: {"user_id": "u1", "directory": "/tmp/bk",
                 "interval_kind": "daily",
                 "last_backup_at": datetime.now(timezone.utc)},
    )
    calls = []
    monkeypatch.setattr(
        auto_backup, "run_backup",
        lambda user_id, directory, **kw: calls.append((user_id, directory)),
    )

    asyncio.run(run_backup_tick())

    assert calls == []


def test_tick_swallows_conflict(monkeypatch):
    monkeypatch.setattr(
        auto_backup, "find_enabled_backup_settings",
        lambda: {"user_id": "u1", "directory": "/tmp/bk",
                 "interval_kind": "daily", "last_backup_at": None},
    )

    def conflict(user_id, directory, **kw):
        raise BackupConflictError("已有一次备份正在执行")

    monkeypatch.setattr(auto_backup, "run_backup", conflict)

    # Must not raise.
    asyncio.run(run_backup_tick())


def test_tick_skips_when_no_settings(monkeypatch):
    monkeypatch.setattr(auto_backup, "find_enabled_backup_settings", lambda: None)
    calls = []
    monkeypatch.setattr(
        auto_backup, "run_backup",
        lambda user_id, directory, **kw: calls.append((user_id, directory)),
    )

    asyncio.run(run_backup_tick())

    assert calls == []


# ---------- endpoints ----------

def test_get_backup_settings_endpoint(monkeypatch):
    monkeypatch.setattr(
        app_module.auto_backup, "get_auto_backup_settings",
        lambda user_id: dict(auto_backup.DEFAULT_SETTINGS),
    )

    result = asyncio.run(app_module.get_my_backup_settings(user={"id": "u1"}))

    assert result["enabled"] is False
    assert result["next_backup_at"] is None


def test_put_backup_settings_rejects_bad_interval(monkeypatch):
    def bad_upsert(*args, **kwargs):
        raise ValueError("备份间隔仅支持 daily、every_3_days、weekly")

    monkeypatch.setattr(app_module.auto_backup, "upsert_auto_backup_settings", bad_upsert)

    try:
        asyncio.run(app_module.update_my_backup_settings(
            req=app_module.AutoBackupSettingsRequest(
                enabled=True, directory="/tmp/bk", interval_kind="hourly",
            ),
            user={"id": "u1"},
        ))
        raise AssertionError("expected HTTPException")
    except Exception as exc:  # noqa: BLE001
        import fastapi
        assert isinstance(exc, fastapi.HTTPException)
        assert exc.status_code == 400


def test_run_backup_endpoint_requires_saved_directory(monkeypatch):
    monkeypatch.setattr(
        app_module.auto_backup, "get_auto_backup_settings",
        lambda user_id: dict(auto_backup.DEFAULT_SETTINGS),
    )

    import fastapi
    try:
        asyncio.run(app_module.run_my_backup_now(user={"id": "u1"}))
        raise AssertionError("expected HTTPException")
    except fastapi.HTTPException as exc:
        assert exc.status_code == 400
        assert "请先配置" in exc.detail


def test_run_backup_endpoint_maps_conflict_to_409(monkeypatch):
    monkeypatch.setattr(
        app_module.auto_backup, "get_auto_backup_settings",
        lambda user_id: {**auto_backup.DEFAULT_SETTINGS, "directory": "/tmp/bk"},
    )

    def conflict(user_id, directory, **kw):
        raise BackupConflictError("已有一次备份正在执行")

    monkeypatch.setattr(app_module.auto_backup, "run_backup", conflict)

    import fastapi
    try:
        asyncio.run(app_module.run_my_backup_now(user={"id": "u1"}))
        raise AssertionError("expected HTTPException")
    except fastapi.HTTPException as exc:
        assert exc.status_code == 409


def test_run_backup_endpoint_returns_file_name(monkeypatch):
    monkeypatch.setattr(
        app_module.auto_backup, "get_auto_backup_settings",
        lambda user_id: {**auto_backup.DEFAULT_SETTINGS, "directory": "/tmp/bk"},
    )
    monkeypatch.setattr(
        app_module.auto_backup, "run_backup",
        lambda user_id, directory, **kw: {"file_name": "paper-library-x.json"},
    )

    result = asyncio.run(app_module.run_my_backup_now(user={"id": "u1"}))

    assert result["file_name"] == "paper-library-x.json"
