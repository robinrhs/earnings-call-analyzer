"""Rough cost estimate per run.

The model is billed per token: input tokens (the prompt plus the transcript) and output
tokens (the JSON brief plus the model's internal reasoning, which is billed as output).
The estimate uses list prices and a crude characters-per-token rule, so treat it as an
order of magnitude, not a quote. After a real run the actual token counts reported by
the API are used instead.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# USD per million tokens (input, output), Anthropic API list prices.
# Snapshot dated 2026-10-06; check https://platform.claude.com/docs/en/about-claude/pricing
PRICES_AS_OF = "2026-10-06"
PRICES_PER_MILLION: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),  # for prompts up to 100K tokens
    "claude-fable-5-1": (10.00, 50.00),
}

# English text averages roughly four characters per token. Newer Claude tokenizers can
# produce noticeably more tokens for the same text, so the estimate may be low.
CHARS_PER_TOKEN = 4

# Output is hard to predict: the JSON brief is a few thousand tokens and the model's
# reasoning adds more. We show a low and a high case instead of a single number.
OUTPUT_TOKENS_LOW = 4_000
OUTPUT_TOKENS_HIGH = 12_000


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Cost in USD, or None if the model is not in the price table."""
    prices = PRICES_PER_MILLION.get(model)
    if prices is None:
        return None
    input_price, output_price = prices
    return input_tokens / 1e6 * input_price + output_tokens / 1e6 * output_price


@dataclass(frozen=True)
class CostEstimate:
    model: str
    input_tokens: int
    low_usd: float | None
    high_usd: float | None


def estimate_run(model: str, prompt_text: str) -> CostEstimate:
    tokens = estimate_tokens(prompt_text)
    return CostEstimate(
        model=model,
        input_tokens=tokens,
        low_usd=cost_usd(model, tokens, OUTPUT_TOKENS_LOW),
        high_usd=cost_usd(model, tokens, OUTPUT_TOKENS_HIGH),
    )
