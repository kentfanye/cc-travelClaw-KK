"""
重试装饰器 — 对LLM API调用做指数退避重试。
"""

from __future__ import annotations

import logging

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

import openai

logger = logging.getLogger("travelclaw.retry")

# 需要重试的OpenAI兼容异常
RETRYABLE_EXCEPTIONS = (
    openai.RateLimitError,
    openai.APIConnectionError,
    openai.InternalServerError,
)


def with_retry(max_attempts: int = 3, base_delay: float = 1.0):
    """
    为同步函数添加重试装饰器。
    对 RateLimitError / APIConnectionError / InternalServerError 做指数退避。
    """
    return retry(
        retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=base_delay, min=base_delay, max=30),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )


def with_async_retry(max_attempts: int = 3, base_delay: float = 1.0):
    """
    为异步函数添加重试装饰器。
    """
    return retry(
        retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=base_delay, min=base_delay, max=30),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
