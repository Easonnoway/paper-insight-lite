import asyncio
import logging
from analysis_context import build_analysis_prompt
from code_availability import classify_code_availability_from_text
from database import (
    get_paper,
    update_llm_response,
    update_paper_code_availability,
)
from markdown_utils import normalize_llm_markdown
from utils import get_or_cache_paper_content, ReaderError, truncate_content_for_llm

logger = logging.getLogger(__name__)

class BackgroundAnalyzer:
    """Single-paper analysis helpers called inline by the analysis SSE endpoint.

    The scheduler loop and keyword enrichment were removed in the lite trim;
    analyze_paper / update_code_availability are kept because the SSE endpoint
    and arXiv ingestion path call them directly.
    """

    def __init__(self, llm, check_interval: int = 3600):
        self.llm = llm
        self.check_interval = check_interval
        self.current_paper_id = None
        self.last_analyzed_paper_id = None
        self.current_code_paper_id = None
        self.last_code_checked_paper_id = None

    async def analyze_paper(self, paper_id: str) -> bool:
        """分析单篇论文，返回是否成功"""
        if not self.llm.is_configured():
            logger.warning("LLM 未配置，跳过论文分析: %s", paper_id)
            return False

        max_retries = 3
        self.current_paper_id = paper_id

        try:
            for attempt in range(max_retries):
                try:
                    paper_info = await asyncio.to_thread(get_paper, paper_id)
                    if not paper_info:
                        logger.error(f"论文 {paper_id} 不存在")
                        return False

                    logger.info(f"[{paper_id}] 读取 PDF...")
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
                            logger.warning(f"[{paper_id}] PDF 读取失败，改用论文元数据分析: {e}")
                    else:
                        content_error = "论文没有可用 PDF 链接"
                        logger.warning(f"[{paper_id}] 未找到 PDF 链接，改用论文元数据分析")

                    logger.info(f"[{paper_id}] 生成分析...")
                    user_prompt = build_analysis_prompt(paper_info, paper_content, content_error)
                    response = await self.llm.get_response(user_prompt)
                    response = normalize_llm_markdown(response, analysis_mode=True)

                    await asyncio.to_thread(update_llm_response, paper_id, response)
                    await self.update_code_availability(paper_info, response)
                    self.last_analyzed_paper_id = paper_id
                    logger.info(f"[{paper_id}] 分析完成: {paper_info.get('title', '')[:50]}")
                    return True

                except Exception as e:
                    logger.warning(f"[{paper_id}] 分析失败 (尝试 {attempt + 1}/{max_retries}): {e}")

                if attempt < max_retries - 1:
                    await asyncio.sleep(2)

            logger.error(f"[{paper_id}] 分析失败，已重试 {max_retries} 次")
            return False
        finally:
            self.current_paper_id = None

    async def update_code_availability(self, paper_info: dict, llm_response: str | None) -> bool:
        paper_id = paper_info.get("id")
        if not paper_id:
            return False
        if not llm_response:
            logger.info("[%s] 没有 llm_response，跳过代码开源状态判断", paper_id)
            return False

        self.current_code_paper_id = paper_id
        try:
            result = await classify_code_availability_from_text(
                self.llm,
                paper_info,
                llm_response,
                source="llm_response",
            )
            await asyncio.to_thread(
                update_paper_code_availability,
                paper_id,
                result["status"],
                result.get("code_url"),
                result.get("evidence"),
                result.get("meta"),
            )
            logger.info("[%s] 代码开源状态判断完成: %s", paper_id, result["status"])
            self.last_code_checked_paper_id = paper_id
            return True
        except Exception as exc:
            logger.warning("[%s] 代码开源状态判断失败: %s", paper_id, exc)
            return False
        finally:
            self.current_code_paper_id = None

    def stop(self):
        """兼容旧调用点（lifespan 收尾）"""
        pass
