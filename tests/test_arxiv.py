import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import database
from arxiv import (
    arxiv_id_from_paper_id,
    build_arxiv_paper_id,
    normalize_arxiv_id,
    parse_arxiv_api_response,
)


ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2605.29707v1</id>
    <updated>2026-05-28T12:34:56Z</updated>
    <published>2026-05-27T01:23:45Z</published>
    <title>  A Useful Paper
      About Agents </title>
    <summary>
      This paper studies agent systems.
    </summary>
    <author><name>Alice Example</name></author>
    <author><name>Bob Example</name></author>
    <arxiv:primary_category term="cs.AI" scheme="http://arxiv.org/schemas/atom"/>
    <category term="cs.AI" scheme="http://arxiv.org/schemas/atom"/>
    <category term="cs.LG" scheme="http://arxiv.org/schemas/atom"/>
    <link title="pdf" href="https://arxiv.org/pdf/2605.29707v1" rel="related" type="application/pdf"/>
  </entry>
</feed>
"""


class FakeCursor:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, query, params=None):
        self.calls.append((query, params))

    def fetchone(self):
        return {"total": 1}

    def fetchall(self):
        return [
            {
                "id": "arxiv:2605.29707",
                "title": "A Useful Paper",
                "abstract": "Summary",
                "keywords": [],
                "pdf": "https://arxiv.org/pdf/2605.29707",
                "venue": "arXiv",
                "primary_area": "cs.AI",
                "llm_response": "analysis",
                "created_at": None,
                "arxiv_id": "2605.29707",
                "arxiv_url": "https://arxiv.org/abs/2605.29707",
                "arxiv_pdf_url": "https://arxiv.org/pdf/2605.29707",
                "arxiv_published_at": datetime(2026, 5, 27, tzinfo=timezone.utc),
                "arxiv_updated_at": datetime(2026, 5, 28, tzinfo=timezone.utc),
                "arxiv_added_at": datetime(2026, 6, 10, tzinfo=timezone.utc),
                "arxiv_added_by_user_id": None,
                "arxiv_metadata": {"primary_category": "cs.AI"},
            }
        ]


class FakeConnection:
    def __init__(self, cursor):
        self.cursor_instance = cursor

    def cursor(self):
        return self.cursor_instance


def test_normalize_arxiv_id_accepts_supported_inputs():
    assert normalize_arxiv_id("2605.29707") == "2605.29707"
    assert normalize_arxiv_id("arXiv:2605.29707v2") == "2605.29707"
    assert normalize_arxiv_id("https://arxiv.org/abs/2605.29707") == "2605.29707"
    assert normalize_arxiv_id("https://arxiv.org/pdf/2605.29707.pdf") == "2605.29707"
    assert normalize_arxiv_id("https://arxiv.org/abs/hep-th/9901001v1") == "hep-th/9901001"
    assert build_arxiv_paper_id("hep-th/9901001") == "arxiv:hep-th_9901001"
    assert arxiv_id_from_paper_id("arxiv:hep-th_9901001") == "hep-th/9901001"


def test_parse_arxiv_api_response_builds_paper_payload():
    payload = parse_arxiv_api_response(ARXIV_XML, "2605.29707")

    assert payload["paper"] == {
        "id": "arxiv:2605.29707",
        "title": "A Useful Paper About Agents",
        "abstract": "This paper studies agent systems.",
        "authors": ["Alice Example", "Bob Example"],
        "keywords": ["cs.AI", "cs.LG"],
        "pdf": "https://arxiv.org/pdf/2605.29707v1",
        "venue": "arXiv",
        "primary_area": "cs.AI",
    }
    assert payload["arxiv"]["arxiv_id"] == "2605.29707"
    assert payload["arxiv"]["published_at"] == datetime(2026, 5, 27, 1, 23, 45, tzinfo=timezone.utc)
    assert payload["arxiv"]["updated_at"] == datetime(2026, 5, 28, 12, 34, 56, tzinfo=timezone.utc)
