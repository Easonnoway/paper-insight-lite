#!/usr/bin/env python3
"""Backfill code_availability tags for papers that already have an AI analysis
(llm_response) but no code_status yet. Uses the active LLM (DeepSeek).

Only classifies papers WITH llm_response — does NOT analyze papers without one
(those get code tags automatically when viewed). Bounded, cheap.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "backend"))

from code_availability import classify_code_availability_from_text
from database import get_papers_pending_code_availability, update_paper_code_availability
from llm import ManagedLLM

BATCH = 25


async def main() -> None:
    llm = ManagedLLM()
    if not llm.is_configured():
        print("LLM 未配置（检查 config.yaml + active provider）")
        sys.exit(1)

    processed = 0
    failed = 0
    while True:
        papers = get_papers_pending_code_availability(limit=BATCH)
        if not papers:
            break
        for paper in papers:
            pid = paper["id"]
            try:
                result = await classify_code_availability_from_text(
                    llm, paper, paper.get("llm_response"), source="llm_response"
                )
                await asyncio.to_thread(
                    update_paper_code_availability,
                    pid,
                    result["status"],
                    result.get("code_url"),
                    result.get("evidence"),
                    result.get("meta"),
                )
                processed += 1
                print(f"[{processed}] {pid} -> {result['status']}")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                # Mark as checked (unknown) so it doesn't loop forever on a bad paper.
                await asyncio.to_thread(
                    update_paper_code_availability,
                    pid,
                    "unknown",
                    None,
                    f"backfill error: {exc}"[:500],
                    {"reason": "backfill_error", "error": str(exc)[:500]},
                )
                print(f"[!] {pid} 失败（标记 unknown）: {exc}")

    print(f"\n完成：分类 {processed} 篇，失败 {failed} 篇。")


if __name__ == "__main__":
    asyncio.run(main())
