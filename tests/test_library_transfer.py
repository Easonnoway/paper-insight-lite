import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import database  # noqa: E402
import library_transfer as lt  # noqa: E402
from library_transfer import (  # noqa: E402
    ExportArxivMeta,
    ExportCategory,
    ExportMark,
    ExportPaper,
    TransferError,
    normalize_mark,
    plan_categories,
    validate_payload,
)


def make_valid_payload_raw():
    return {
        "format": "paper-insight-lite.export",
        "version": 1,
        "exported_at": "2026-09-14T08:00:00+00:00",
        "categories": [
            {"id": "c1", "parent_id": None, "name": "ML", "position": 0},
            {"id": "c2", "parent_id": "c1", "name": "Safety", "position": 0},
        ],
        "papers": [
            {
                "id": "arxiv:1706.03762",
                "title": "Attention",
                "keywords": ["transformer"],
                "authors": ["Vaswani"],
                "llm_response": "file ai",
                "abstract_zh": "文件中文摘要",
                "mark": {"viewed": True, "liked": True},
                "category_ids": ["c2"],
                "arxiv": {"arxiv_id": "1706.03762"},
            },
            {
                "id": "or:note1",
                "title": "Conf Paper",
                "mark": {"viewed": True},
                "category_ids": ["c1"],
            },
        ],
    }


# ---------- validate_payload ----------

def test_validate_payload_accepts_valid():
    payload = validate_payload(make_valid_payload_raw())
    assert payload.format == "paper-insight-lite.export"
    assert len(payload.papers) == 2
    assert payload.papers[0].arxiv.arxiv_id == "1706.03762"


def test_validate_payload_preserves_category_color():
    raw = make_valid_payload_raw()
    raw["categories"][0]["color"] = "teal"
    raw["categories"][1]["color"] = None
    payload = validate_payload(raw)
    assert payload.categories[0].color == "teal"
    assert payload.categories[1].color is None


def test_validate_payload_tolerates_missing_color():
    # Files exported by older builds carry no color field at all.
    raw = make_valid_payload_raw()
    for category in raw["categories"]:
        category.pop("color", None)
    payload = validate_payload(raw)
    assert all(category.color is None for category in payload.categories)


def test_validate_payload_rejects_wrong_format():
    raw = make_valid_payload_raw()
    raw["format"] = "other"
    try:
        validate_payload(raw)
        raise AssertionError("expected TransferError")
    except TransferError as exc:
        assert "格式" in str(exc)


def test_validate_payload_rejects_newer_version():
    raw = make_valid_payload_raw()
    raw["version"] = 2
    try:
        validate_payload(raw)
        raise AssertionError("expected TransferError")
    except TransferError as exc:
        assert "版本过新" in str(exc)


def test_validate_payload_rejects_duplicate_paper_id():
    raw = make_valid_payload_raw()
    raw["papers"][1]["id"] = raw["papers"][0]["id"]
    try:
        validate_payload(raw)
        raise AssertionError("expected TransferError")
    except TransferError as exc:
        assert "重复的论文" in str(exc)


def test_validate_payload_rejects_duplicate_arxiv_id():
    raw = make_valid_payload_raw()
    raw["papers"][1]["arxiv"] = {"arxiv_id": "1706.03762"}
    try:
        validate_payload(raw)
        raise AssertionError("expected TransferError")
    except TransferError as exc:
        assert "重复的 arXiv" in str(exc)


def test_validate_payload_rejects_unknown_category_reference():
    raw = make_valid_payload_raw()
    raw["papers"][0]["category_ids"] = ["ghost"]
    try:
        validate_payload(raw)
        raise AssertionError("expected TransferError")
    except TransferError as exc:
        assert "不存在的分类" in str(exc)


def test_validate_payload_rejects_dangling_parent():
    raw = make_valid_payload_raw()
    raw["categories"][1]["parent_id"] = "ghost-parent"
    try:
        validate_payload(raw)
        raise AssertionError("expected TransferError")
    except TransferError as exc:
        assert "父分类" in str(exc)


def test_validate_payload_rejects_category_cycle():
    raw = make_valid_payload_raw()
    raw["categories"] = [
        {"id": "c1", "parent_id": "c2", "name": "A", "position": 0},
        {"id": "c2", "parent_id": "c1", "name": "B", "position": 0},
    ]
    try:
        validate_payload(raw)
        raise AssertionError("expected TransferError")
    except TransferError as exc:
        assert "循环" in str(exc)


def test_validate_payload_downgrades_bad_timestamp():
    raw = make_valid_payload_raw()
    raw["papers"][0]["mark"]["viewed_at"] = "not-a-date"
    payload = validate_payload(raw)
    assert payload.papers[0].mark.viewed_at is None


def test_validate_payload_interprets_naive_timestamps_as_utc():
    raw = make_valid_payload_raw()
    raw["papers"][0]["created_at"] = "2026-01-01T00:00:00"
    payload = validate_payload(raw)
    assert payload.papers[0].created_at.tzinfo is not None


# ---------- normalize_mark ----------

def test_normalize_mark_liked_implies_viewed():
    mark = ExportMark(viewed=False, liked=True)
    normalized = normalize_mark(mark, None)
    assert normalized["viewed"] is True
    assert normalized["liked"] is True


def test_normalize_mark_all_false_becomes_viewed():
    mark = ExportMark(viewed=False, liked=False, favorited=False)
    normalized = normalize_mark(mark, None)
    assert normalized["viewed"] is True
    assert normalized["liked"] is False


def test_normalize_mark_viewed_at_fallback_chain():
    from datetime import datetime, timezone

    exported_at = datetime(2026, 9, 14, tzinfo=timezone.utc)
    mark = ExportMark(viewed=True)
    normalized = normalize_mark(mark, exported_at)
    assert normalized["viewed_at"] == exported_at
    assert normalized["first_viewed_at"] == exported_at

    mark_with_first = ExportMark(viewed=True, first_viewed_at=exported_at)
    normalized = normalize_mark(mark_with_first, None)
    assert normalized["viewed_at"] == exported_at


def test_normalize_mark_clears_action_timestamps_when_not_marked():
    from datetime import datetime, timezone

    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    mark = ExportMark(viewed=True, liked=False, liked_at=ts, favorited=False, favorited_at=ts)
    normalized = normalize_mark(mark, None)
    assert normalized["liked_at"] is None
    assert normalized["favorited_at"] is None


# ---------- plan_categories ----------

LOCAL_TREE = [
    {"id": "l-ml", "parent_id": None, "name": "ML", "position": 0},
    {"id": "l-nlp", "parent_id": None, "name": "NLP", "position": 1},
]


def test_plan_categories_merges_same_name_same_level():
    file_cats = [
        ExportCategory(id="c1", parent_id=None, name="ML", position=0),
    ]
    plan = plan_categories(file_cats, {"c1"}, LOCAL_TREE)
    assert plan["export_to_local"]["c1"] == "l-ml"
    assert plan["creates"] == []
    assert plan["created_count"] == 0
    assert plan["reused_count"] == 1


def test_plan_categories_creates_missing_child_with_resolved_parent():
    file_cats = [
        ExportCategory(id="c1", parent_id=None, name="ML", position=0),
        ExportCategory(id="c2", parent_id="c1", name="Safety", position=0, color="violet"),
    ]
    plan = plan_categories(file_cats, {"c2"}, LOCAL_TREE)
    # Parent ML reuses l-ml; only Safety is created under it (color carried).
    assert plan["creates"] == [
        {
            "export_id": "c2", "parent_id": "l-ml", "name": "Safety",
            "position": 0, "color": "violet",
        },
    ]
    assert plan["created_count"] == 1
    assert plan["reused_count"] == 1


def test_plan_categories_same_name_different_level_not_merged():
    file_cats = [
        ExportCategory(id="c1", parent_id=None, name="ML", position=0),
        ExportCategory(id="c2", parent_id="c1", name="NLP", position=0),
    ]
    plan = plan_categories(file_cats, {"c2"}, LOCAL_TREE)
    # "NLP" exists at top level, but the file puts it under ML — no merge.
    created = [c for c in plan["creates"] if c["name"] == "NLP"]
    assert len(created) == 1
    assert created[0]["parent_id"] == "l-ml"


def test_plan_categories_ignores_unreferenced_categories():
    file_cats = [
        ExportCategory(id="c1", parent_id=None, name="Unused", position=0),
        ExportCategory(id="c2", parent_id=None, name="ML", position=1),
    ]
    plan = plan_categories(file_cats, {"c2"}, LOCAL_TREE)
    assert all(c["name"] != "Unused" for c in plan["creates"])
    assert "c1" not in plan["export_to_local"]


def test_plan_categories_chain_parent_created_before_child():
    file_cats = [
        ExportCategory(id="a", parent_id=None, name="Top", position=0),
        ExportCategory(id="b", parent_id="a", name="Mid", position=0),
        ExportCategory(id="c", parent_id="b", name="Leaf", position=0),
    ]
    plan = plan_categories(file_cats, {"c"}, [])
    names = [c["name"] for c in plan["creates"]]
    assert names == ["Top", "Mid", "Leaf"]
    # Placeholders let children reference parents created in the same run.
    assert plan["export_to_local"]["b"] == "new:b"
    assert plan["export_to_local"]["c"] == "new:c"


# ---------- apply_import (fake connection) ----------

class RecordingCursor:
    """Records executed SQL; answers SELECTs from scripted result sets."""

    def __init__(self, select_results):
        self.select_results = select_results
        self.executed = []
        self.current_rows = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, query, params=None):
        self.executed.append((" ".join(query.split()), params))
        if "RETURNING id" in query and "paper_categories" in query:
            self.current_rows = [{"id": "new-cat-1"}]
            return
        if "FROM paper_categories" in query and "INSERT" not in query:
            self.current_rows = self.select_results.get("categories", [])
            return
        if "FROM papers p" in query:
            self.current_rows = self.select_results.get("papers", [])
            return
        self.current_rows = []

    def executemany(self, query, params_seq):
        for params in params_seq:
            self.execute(query, params)

    def fetchone(self):
        return self.current_rows[0] if self.current_rows else None

    def fetchall(self):
        return list(self.current_rows)

    @property
    def rowcount(self):
        return 1 if self.current_rows else 0


class RecordingConnection:
    def __init__(self, cursor):
        self.cursor_instance = cursor
        self.commits = 0

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1


def install_recording_connection(monkeypatch, connection):
    @contextmanager
    def fake_get_connection():
        yield connection

    monkeypatch.setattr(database, "_get_connection", fake_get_connection)


# A syntactically valid UUID — user_id columns are compared against the
# users(id) uuid column, so tests must pass a castable value.
TEST_USER_ID = "d8edbc93-cb6f-4571-82ee-b8020fe6d224"


def test_apply_import_new_paper_inserts_all_five_tables(monkeypatch):
    cursor = RecordingCursor(select_results={
        "papers": [],  # nothing exists locally
        "categories": [],
    })
    connection = RecordingConnection(cursor)
    install_recording_connection(monkeypatch, connection)

    payload = validate_payload(make_valid_payload_raw())
    result = lt.apply_import(payload, ["arxiv:1706.03762", "or:note1"], TEST_USER_ID)

    assert result["created_papers"] == 2
    assert result["merged_papers"] == 0
    sql_text = " ".join(sql for sql, _ in cursor.executed)
    assert "INSERT INTO papers" in sql_text
    assert "INSERT INTO authors" in sql_text
    assert "INSERT INTO keywords" in sql_text
    assert "INSERT INTO arxiv_papers" in sql_text
    assert "INSERT INTO paper_marks" in sql_text
    assert "INSERT INTO paper_categories" in sql_text
    assert "INSERT INTO paper_category_assignments" in sql_text
    assert connection.commits == 1


def test_apply_import_existing_paper_merges_without_mark_or_paper_insert(monkeypatch):
    cursor = RecordingCursor(select_results={
        "papers": [
            {"id": "arxiv:1706.03762", "arxiv_id": "1706.03762",
             "llm_response": "existing", "abstract_zh": None},
        ],
        "categories": [],
    })
    connection = RecordingConnection(cursor)
    install_recording_connection(monkeypatch, connection)

    payload = validate_payload(make_valid_payload_raw())
    result = lt.apply_import(payload, ["arxiv:1706.03762"], TEST_USER_ID)

    assert result["merged_papers"] == 1
    assert result["created_papers"] == 0
    sql_text = " ".join(sql for sql, _ in cursor.executed)
    # No papers INSERT and no paper_marks INSERT for the existing paper.
    assert "INSERT INTO papers" not in sql_text
    assert "INSERT INTO paper_marks" not in sql_text
    # But the empty abstract_zh gets filled via UPDATE.
    assert "SET abstract_zh" in sql_text
    # llm_response is NOT overwritten (local already has one).
    assert "SET llm_response" not in sql_text


def test_apply_import_rejects_empty_selection():
    payload = validate_payload(make_valid_payload_raw())
    try:
        lt.apply_import(payload, [], "user-1")
        raise AssertionError("expected TransferError")
    except TransferError as exc:
        assert "至少选择一篇" in str(exc)


def test_apply_import_rejects_unknown_selection(monkeypatch):
    payload = validate_payload(make_valid_payload_raw())
    try:
        lt.apply_import(payload, ["ghost-id"], "user-1")
        raise AssertionError("expected TransferError")
    except TransferError as exc:
        assert "文件中不存在" in str(exc)


# ---------- export payload shape ----------

def test_build_export_payload_maps_snapshot(monkeypatch):
    from datetime import datetime, timezone

    created = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(database, "get_export_snapshot", lambda user_id, paper_ids=None: [{
        "id": "arxiv:1706.03762",
        "title": "Attention",
        "abstract": "abs",
        "keywords_jsonb": [],
        "pdf": "https://arxiv.org/pdf/1706.03762",
        "venue": "arXiv",
        "primary_area": None,
        "llm_response": "ai",
        "abstract_zh": "中文",
        "created_at": created,
        "viewed": True,
        "liked": True,
        "favorited": False,
        "first_viewed_at": created,
        "viewed_at": created,
        "liked_at": created,
        "favorited_at": None,
        "updated_at": created,
        "authors": ["Vaswani"],
        "keywords": ["transformer"],
        "arxiv": {
            "arxiv_id": "1706.03762",
            "arxiv_url": "https://arxiv.org/abs/1706.03762",
            "pdf_url": "https://arxiv.org/pdf/1706.03762",
            "published_at": created,
            "updated_at": None,
            "added_at": created,
            "metadata": {},
        },
        "category_ids": [],
    }])

    payload = lt.build_export_payload("user-1")
    assert payload["format"] == lt.EXPORT_FORMAT
    assert payload["version"] == 1
    paper = payload["papers"][0]
    assert paper["id"] == "arxiv:1706.03762"
    assert paper["keywords"] == ["transformer"]
    assert paper["authors"] == ["Vaswani"]
    assert paper["arxiv"]["arxiv_id"] == "1706.03762"
    assert paper["mark"]["liked"] is True
    assert payload["categories"] == []
    # Round-trips through validate_payload.
    assert validate_payload(payload).papers[0].title == "Attention"


def test_apply_import_inserts_category_color(monkeypatch):
    """Created categories carry the file color; reused ones keep local color."""
    payload = validate_payload(make_valid_payload_raw())
    payload.categories[1].color = "rose"

    executed: list[tuple[str, tuple]] = []

    class ColorCursor:
        rowcount = 1

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params=None):
            executed.append((sql, params))
            sql_compact = " ".join(sql.split())
            if "FROM paper_marks" in sql_compact:
                self.current_rows = []
            elif "FROM paper_categories" in sql_compact and "SELECT id" in sql_compact:
                # Local category tree read for plan_categories.
                self.current_rows = [
                    {"id": "l-ml", "parent_id": None, "name": "ML", "position": 0},
                ]
            elif sql_compact.startswith("SELECT p.id"):
                self.current_rows = []
            else:
                self.current_rows = []

        def fetchone(self):
            return self.current_rows[0] if self.current_rows else None

        def fetchall(self):
            return list(self.current_rows)

        def executemany(self, sql, seq):
            for params in seq:
                self.execute(sql, params)

    class ColorConnection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return ColorCursor()

        def commit(self):
            return None

        def rollback(self):
            return None

    @contextmanager
    def fake_connection():
        yield ColorConnection()

    monkeypatch.setattr(database, "_get_connection", fake_connection)
    result = lt.apply_import(payload, {"arxiv:1706.03762"}, TEST_USER_ID)
    assert result["created_papers"] == 1

    insert_sqls = [
        (" ".join(sql.split()), params)
        for sql, params in executed
        if "INSERT INTO paper_categories" in sql
    ]
    assert insert_sqls, "expected a paper_categories INSERT"
    # ML is reused (no INSERT); Safety created with its file color.
    safety_params = next(params for _, params in insert_sqls if "Safety" in str(params))
    assert "rose" in safety_params


# ---------- note round-trip ----------

def raw_with_note():
    """make_valid_payload_raw() plus a note on the first paper only."""
    raw = make_valid_payload_raw()
    raw["papers"][0]["note"] = "文件里的笔记"
    return raw


def test_validate_payload_tolerates_missing_note():
    # Files exported by older builds carry no note field at all.
    payload = validate_payload(make_valid_payload_raw())
    assert all(paper.note is None for paper in payload.papers)


def test_validate_payload_preserves_note():
    payload = validate_payload(raw_with_note())
    assert payload.papers[0].note == "文件里的笔记"


def test_validate_payload_rejects_overlength_note():
    raw = raw_with_note()
    raw["papers"][0]["note"] = "x" * (lt.MAX_NOTE_LENGTH + 1)
    try:
        validate_payload(raw)
        raise AssertionError("expected TransferError")
    except TransferError as exc:
        assert "papers.0.note" in str(exc)


def test_build_export_payload_includes_note(monkeypatch):
    from datetime import datetime, timezone

    created = datetime(2026, 1, 1, tzinfo=timezone.utc)
    snapshot = {
        "id": "arxiv:1706.03762",
        "title": "Attention",
        "abstract": "abs",
        "keywords_jsonb": [],
        "pdf": None,
        "venue": None,
        "primary_area": None,
        "llm_response": None,
        "abstract_zh": None,
        "note": "我的笔记",
        "created_at": created,
        "viewed": True,
        "liked": False,
        "favorited": False,
        "first_viewed_at": None,
        "viewed_at": created,
        "liked_at": None,
        "favorited_at": None,
        "updated_at": created,
        "authors": [],
        "keywords": [],
        "arxiv": None,
        "category_ids": [],
    }
    monkeypatch.setattr(
        database, "get_export_snapshot", lambda user_id, paper_ids=None: [snapshot]
    )

    payload = lt.build_export_payload("user-1")
    assert payload["papers"][0]["note"] == "我的笔记"
    assert validate_payload(payload).papers[0].note == "我的笔记"


def test_apply_import_new_paper_inserts_mark_with_note(monkeypatch):
    cursor = RecordingCursor(select_results={"papers": [], "categories": []})
    connection = RecordingConnection(cursor)
    install_recording_connection(monkeypatch, connection)

    payload = validate_payload(raw_with_note())
    lt.apply_import(payload, ["arxiv:1706.03762"], TEST_USER_ID)

    mark_inserts = [
        (" ".join(sql.split()), params)
        for sql, params in cursor.executed
        if "INSERT INTO paper_marks" in sql
    ]
    assert mark_inserts, "expected a paper_marks INSERT"
    assert "文件里的笔记" in str(mark_inserts[0][1])


def test_apply_import_existing_paper_fills_empty_note(monkeypatch):
    cursor = RecordingCursor(select_results={
        "papers": [
            {"id": "arxiv:1706.03762", "arxiv_id": "1706.03762",
             "llm_response": "existing", "abstract_zh": None,
             "local_note": None, "has_mark": True},
        ],
        "categories": [],
    })
    connection = RecordingConnection(cursor)
    install_recording_connection(monkeypatch, connection)

    payload = validate_payload(raw_with_note())
    result = lt.apply_import(payload, ["arxiv:1706.03762"], TEST_USER_ID)

    assert result["merged_papers"] == 1
    sql_text = " ".join(sql for sql, _ in cursor.executed)
    assert "SET note" in sql_text
    note_updates = [
        (" ".join(sql.split()), params)
        for sql, params in cursor.executed
        if "SET note" in sql
    ]
    assert "文件里的笔记" in str(note_updates[0][1])


def test_apply_import_existing_paper_keeps_local_note(monkeypatch):
    cursor = RecordingCursor(select_results={
        "papers": [
            {"id": "arxiv:1706.03762", "arxiv_id": "1706.03762",
             "llm_response": None, "abstract_zh": None,
             "local_note": "本地已有笔记", "has_mark": True},
        ],
        "categories": [],
    })
    connection = RecordingConnection(cursor)
    install_recording_connection(monkeypatch, connection)

    payload = validate_payload(raw_with_note())
    lt.apply_import(payload, ["arxiv:1706.03762"], TEST_USER_ID)

    sql_text = " ".join(sql for sql, _ in cursor.executed)
    # The local note wins: no note UPDATE, no mark INSERT.
    assert "SET note" not in sql_text
    assert "INSERT INTO paper_marks" not in sql_text


def test_apply_import_existing_paper_without_mark_creates_mark_with_note(monkeypatch):
    cursor = RecordingCursor(select_results={
        "papers": [
            {"id": "arxiv:1706.03762", "arxiv_id": "1706.03762",
             "llm_response": None, "abstract_zh": None,
             "local_note": None, "has_mark": False},
        ],
        "categories": [],
    })
    connection = RecordingConnection(cursor)
    install_recording_connection(monkeypatch, connection)

    payload = validate_payload(raw_with_note())
    lt.apply_import(payload, ["arxiv:1706.03762"], TEST_USER_ID)

    mark_inserts = [
        (" ".join(sql.split()), params)
        for sql, params in cursor.executed
        if "INSERT INTO paper_marks" in sql
    ]
    assert mark_inserts, "expected a paper_marks INSERT for the note"
    assert "文件里的笔记" in str(mark_inserts[0][1])
    # The note-bearing mark makes the paper visible (viewed=TRUE invariant).
    assert "TRUE" in mark_inserts[0][0]


def test_preview_import_reports_will_fill_note(monkeypatch):
    raw = raw_with_note()
    payload = validate_payload(raw)

    class PreviewCursor:
        def __init__(self, local_note):
            self.local_note = local_note
            self.current_rows = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, query, params=None):
            compact = " ".join(query.split())
            if "FROM paper_categories" in compact:
                self.current_rows = []
            else:
                self.current_rows = [
                    {"id": "arxiv:1706.03762", "arxiv_id": "1706.03762",
                     "llm_response": None, "abstract_zh": None,
                     "local_note": self.local_note},
                ]

        def fetchone(self):
            return self.current_rows[0] if self.current_rows else None

        def fetchall(self):
            return list(self.current_rows)

    class PreviewConnection:
        def __init__(self, local_note):
            self.local_note = local_note

        def cursor(self):
            return PreviewCursor(self.local_note)

    @contextmanager
    def fake_connection():
        yield PreviewConnection(local_note=None)

    monkeypatch.setattr(database, "_get_connection", fake_connection)
    preview = lt.preview_import(payload, TEST_USER_ID)

    paper_row = next(row for row in preview["papers"] if row["id"] == "arxiv:1706.03762")
    assert paper_row["will_fill_note"] is True

    # With a local note present the flag flips to False.
    @contextmanager
    def fake_noted_connection():
        yield PreviewConnection(local_note="本地已有")

    monkeypatch.setattr(database, "_get_connection", fake_noted_connection)
    preview = lt.preview_import(payload, TEST_USER_ID)
    paper_row = next(row for row in preview["papers"] if row["id"] == "arxiv:1706.03762")
    assert paper_row["will_fill_note"] is False
