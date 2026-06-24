"""
LLM client factory — initializes and caches the ChatGroq instance.

Centralised here so every chain/service shares the same client,
rate-limit pool, and configuration.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from langchain_groq import ChatGroq

from app.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_llm() -> ChatGroq:
    """Return a singleton ChatGroq instance configured from env vars."""
    if not settings.groq_api_key:
        logger.warning(
            "GROQ_API_KEY is not set — AI endpoints will fail. "
            "Set it in .env or as an environment variable."
        )

    llm = ChatGroq(
        api_key=settings.groq_api_key,
        model=settings.llm_model,
        temperature=0,          # deterministic for SQL / structured output
        max_tokens=4096,
        request_timeout=30,
    )
    logger.info("ChatGroq initialised — model=%s", settings.llm_model)
    return llm
