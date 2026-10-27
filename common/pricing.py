"""Ціни Groq on-demand, USD за 1M токенів.

Джерело: https://console.groq.com/docs/models (перевірено 27.10.2026).
Prompt caching: кешовані вхідні токени — 50% ціни (лише gpt-oss).
Batch API: -50% від on-demand (не сумується з кешуванням).
"""

PRICES = {
    # model: (input, output, supports_prompt_cache)
    "openai/gpt-oss-20b": (0.075, 0.30, True),
    "openai/gpt-oss-120b": (0.15, 0.60, True),
    "qwen/qwen3.8-27b": (0.80, 4.00, False),
}

CACHED_INPUT_DISCOUNT = 0.5


def cost_usd(model: str, prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0) -> float:
    """Вартість одного виклику з урахуванням кешованих вхідних токенів."""
    price_in, price_out, supports_cache = PRICES[model]
    if not supports_cache:
        cached_tokens = 0
    fresh = prompt_tokens - cached_tokens
    return (
        fresh * price_in
        + cached_tokens * price_in * CACHED_INPUT_DISCOUNT
        + completion_tokens * price_out
    ) / 1_000_000
