"""Lazy translation of paper abstracts (English -> Chinese) via the active LLM."""

from __future__ import annotations

TRANSLATE_SYSTEM_PROMPT = (
    "你是一名学术论文翻译专家。用户会给你一段论文摘要（通常是英文），"
    "请把它翻译成准确、流畅、符合中文学术表达习惯的中文。"
    "只输出译文本身，不要加解释、不要加前缀（如「译文：」）、不要改写事实。"
    "如果摘要本来就是中文，直接原样返回。"
)


async def translate_abstract(llm, abstract: str) -> str:
    """Translate an abstract to Chinese via the active LLM. Returns the translated text."""
    text = (abstract or "").strip()
    if not text:
        return ""
    reply = await llm.chat(
        [
            {"role": "system", "content": TRANSLATE_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        temperature=0,
        _usage_context="abstract_translation",
    )
    return (reply or "").strip()
