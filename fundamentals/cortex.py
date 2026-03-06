"""
Snowflake Cortex AI integration for trade analysis.

Sends technical signals and SEC fundamentals to a Snowflake Cortex LLM
via the SNOWFLAKE.CORTEX.COMPLETE SQL function and parses the
BUY / HOLD / SELL recommendation from the response.
"""

import logging
import re
from typing import Any

import snowflake.connector
from fundamentals.client import SnowflakeClient

logger = logging.getLogger(__name__)

# Supported recommendation tokens (case-insensitive match)
_RECOMMENDATION_PATTERN = re.compile(r"\b(BUY|HOLD|SELL)\b", re.IGNORECASE)

# Allowed Snowflake Cortex model names — validated before use in SQL
_ALLOWED_MODELS: frozenset[str] = frozenset(
    {
        "llama3-70b",
        "llama3-8b",
        "mistral-large",
        "mistral-large2",
        "mixtral-8x7b",
        "snowflake-arctic",
        "reka-flash",
        "reka-core",
        "jamba-instruct",
        "jamba-1.5-mini",
        "jamba-1.5-large",
    }
)

_PROMPT_TEMPLATE = """\
You are an experienced equity analyst and trader.
Based on the technical indicators and latest SEC fundamental data below,
decide whether to BUY, HOLD, or SELL the stock.

{technical_block}

SEC Fundamentals (most recent filing):
{fundamentals_block}

Instructions:
- Start your response with exactly one of: BUY, HOLD, or SELL.
- Follow with a concise 2-4 sentence explanation of your reasoning.
- Consider both momentum signals and fundamental quality.
- Be conservative - only recommend BUY when the evidence is convincing.
"""


def analyze_trade(
    client: SnowflakeClient,
    symbol: str,
    technical_block: str,
    fundamentals_block: str,
    model: str = "llama3-70b",
) -> dict[str, Any]:
    """Use Snowflake Cortex to evaluate a potential trade.

    Args:
        client:             An open :class:`~fundamentals.client.SnowflakeClient`.
        symbol:             Ticker symbol (used only for logging).
        technical_block:    Pre-formatted technical-indicator text
                            (from :func:`signals.technical.format_signals_for_prompt`).
        fundamentals_block: Pre-formatted fundamentals text
                            (from :func:`fundamentals.sec_filings.format_fundamentals_for_prompt`).
        model:              Snowflake Cortex model name (default ``llama3-70b``).

    Returns:
        Dict with keys:
            ``recommendation`` (str): "BUY", "HOLD", or "SELL".
            ``reasoning``      (str): Full model response.
            ``model``          (str): Model name used.

    Raises:
        ValueError: If *model* is not in the list of allowed Cortex model names.
    """
    if model not in _ALLOWED_MODELS:
        raise ValueError(
            f"Unknown Cortex model '{model}'. "
            f"Allowed values: {sorted(_ALLOWED_MODELS)}"
        )

    prompt = _PROMPT_TEMPLATE.format(
        technical_block=technical_block,
        fundamentals_block=fundamentals_block,
    )

    # Use parameterized binding for both the model name and prompt text to
    # prevent SQL injection.  Snowflake connector supports %(name)s binding.
    query = "SELECT SNOWFLAKE.CORTEX.COMPLETE(%(model)s, %(prompt)s) AS RESPONSE"

    try:
        rows = client.execute_query(query, {"model": model, "prompt": prompt})
        response_text = (rows[0].get("RESPONSE") or rows[0].get("response") or "").strip()
    except snowflake.connector.Error as exc:
        logger.error("Cortex analysis failed for %s: %s", symbol, exc)
        return {"recommendation": "HOLD", "reasoning": str(exc), "model": model}

    recommendation = _parse_recommendation(response_text)
    logger.info("Cortex recommendation for %s: %s", symbol, recommendation)

    return {
        "recommendation": recommendation,
        "reasoning": response_text,
        "model": model,
    }


def _parse_recommendation(response_text: str) -> str:
    """Extract the first BUY / HOLD / SELL token from the model response.

    Defaults to "HOLD" when no clear recommendation is found.
    """
    match = _RECOMMENDATION_PATTERN.search(response_text)
    if match:
        return match.group(0).upper()
    logger.warning("Could not parse recommendation from Cortex response; defaulting to HOLD")
    return "HOLD"
