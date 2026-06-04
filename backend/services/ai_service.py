"""
DeepSeek API 服务层 — TriDoc 唯一 AI 引擎。

特性:
  - OpenAI 兼容协议，base_url 指向 DeepSeek
  - 内置 3 次重试 + 2 秒间隔
  - 所有参数针对 DeepSeek 特性调优
  - 单一模型策略：deepseek-chat（避免多模型质量不一致）

DeepSeek 关键参数:
  - model: deepseek-chat (V3, 64K context)
  - temperature: 0.1-0.5 范围效果最佳（DeepSeek 在此区间表现稳定）
  - JSON mode: 支持 response_format json_object，但需在 system prompt 中明确告知
  - DeepSeek 对 system prompt 遵循度高，适合结构化指令
"""

from __future__ import annotations

import os
import time
import logging
from functools import wraps
from typing import TypeVar, Callable, Any

from openai import OpenAI

logger = logging.getLogger("tri_doc.ai_service")

# =============================================================================
# DeepSeek 配置（唯一引擎）
# =============================================================================
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")  # V3

# 重试配置
MAX_RETRIES = 3
RETRY_DELAY_SEC = 2

# DeepSeek 推荐温度区间
TEMP_PRECISE = 0.1   # 术语提取、JSON 结构化输出
TEMP_BALANCED = 0.3  # 翻译
TEMP_CREATIVE = 0.5  # 润色、自然化

_client: OpenAI | None = None


def get_client() -> OpenAI:
    """延迟初始化 DeepSeek 客户端（OpenAI 兼容协议）。"""
    global _client
    if _client is None:
        if not DEEPSEEK_API_KEY:
            logger.warning("DEEPSEEK_API_KEY 未设置 — AI 调用将失败")
        _client = OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
            timeout=120.0,  # DeepSeek 偶有较长响应时间
            max_retries=0,  # 由我们的 with_retry 统一控制
        )
    return _client


# =============================================================================
# 通用重试装饰器
# =============================================================================
F = TypeVar("F", bound=Callable[..., Any])


def with_retry(
    max_retries: int = MAX_RETRIES,
    delay_sec: float = RETRY_DELAY_SEC,
) -> Callable[[F], F]:
    """
    自动重试装饰器。

    重试条件：网络错误、超时、5xx、429 rate limit。
    不重试：4xx（参数错误重试无意义）。
    """

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_err: Exception | None = None
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    last_err = exc
                    err_str = str(exc).lower()
                    # 4xx 不重试
                    if any(code in err_str for code in ["400", "401", "402", "403", "404", "422"]):
                        logger.error("[FATAL] %s: %s — 客户端错误，不重试", func.__qualname__, exc)
                        raise
                    if attempt < max_retries:
                        logger.warning(
                            "[retry %d/%d] %s 失败: %s — %ds 后重试",
                            attempt, max_retries, func.__qualname__, exc, delay_sec,
                        )
                        time.sleep(delay_sec)
            logger.error(
                "[FAILED] %s: %d 次重试后仍失败 — %s",
                func.__qualname__, max_retries, last_err,
            )
            raise last_err  # type: ignore[misc]
        return wrapper  # type: ignore[return-value]
    return decorator


# =============================================================================
# AI 调用接口
# =============================================================================

@with_retry(max_retries=MAX_RETRIES, delay_sec=RETRY_DELAY_SEC)
def _extract_content(resp) -> str:
    """Extract response content, falling back to reasoning_content for reasoning models."""
    msg = resp.choices[0].message
    content = msg.content
    if not content:
        reasoning = getattr(msg, "reasoning_content", None)
        if reasoning:
            return reasoning.strip()
    return (content or "").strip()


def chat(
    system_prompt: str,
    user_message: str,
    *,
    model: str = DEEPSEEK_MODEL,
    temperature: float = TEMP_BALANCED,
    max_tokens: int = 16384,
) -> str:
    """
    通用 Chat Completions — 自由文本输出。
    适用于：润色、翻译、自然化。
    max_tokens 默认 16384，满足 reasoning 模型（如 deepseek-v4-pro）的思考开销。
    """
    client = get_client()
    resp = client.chat.completions.create(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    )
    return _extract_content(resp)


@with_retry(max_retries=MAX_RETRIES, delay_sec=RETRY_DELAY_SEC)
def chat_json(
    system_prompt: str,
    user_message: str,
    *,
    model: str = DEEPSEEK_MODEL,
    temperature: float = TEMP_PRECISE,
    max_tokens: int = 16384,
) -> str:
    """
    结构化 JSON 输出 — DeepSeek 支持 response_format json_object。

    注意：DeepSeek 的 JSON mode 要求在 system prompt 中明确声明
    "You must respond with a valid JSON object"，
    否则偶有 markdown 包裹的情况。
    max_tokens 设为 16384 以兼容 reasoning 模型。
    """
    client = get_client()
    resp = client.chat.completions.create(
        model=model,
        temperature=temperature,
        max_tokens=16384,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    )
    return _extract_content(resp)


# =============================================================================
# 便捷方法 — 按阶段预设 temperature
# =============================================================================
def chat_precise(system_prompt: str, user_message: str) -> str:
    """低温精确调用（术语提取、归一化、校验）。"""
    return chat(system_prompt, user_message, temperature=TEMP_PRECISE)


def chat_balanced(system_prompt: str, user_message: str) -> str:
    """中温平衡调用（翻译）。"""
    return chat(system_prompt, user_message, temperature=TEMP_BALANCED)


def chat_creative(system_prompt: str, user_message: str) -> str:
    """高温创意调用（润色、自然化）。"""
    return chat(system_prompt, user_message, temperature=TEMP_CREATIVE)


def chat_json_precise(system_prompt: str, user_message: str) -> str:
    """结构化 JSON 输出 — 专用低温。"""
    return chat_json(system_prompt, user_message, temperature=TEMP_PRECISE)
