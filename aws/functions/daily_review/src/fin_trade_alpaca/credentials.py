"""Alpaca credential resolution and CI safety gates.

Rationale (predictability + testability):
    Credential resolution and the CI "don't place real orders by accident"
    guardrail were previously buried inside the 1,250-line
    ``runners/optimize_and_buy.py`` orchestration script. Pulling them into a
    standalone, dependency-free module makes them:
      - independently unit-testable (pure functions of env vars / args, no
        Alpaca client or network access required), and
      - reusable by any runner/tool that needs credentials without importing
        the entire order-execution script (several ``tools/*.py`` scripts
        already do exactly this).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ModeCredentials:
    """Resolved Alpaca credentials for a single trading mode (paper or live)."""

    api_key: str | None
    api_secret: str | None
    oauth_token: str | None
    paper: bool


def is_ci_runtime() -> bool:
    """Return True when running inside a CI pipeline (GitHub Actions or generic CI)."""
    return os.getenv("GITHUB_ACTIONS", "").lower() == "true" or os.getenv("CI", "").lower() == "true"


def require_ci_approval_for_real_orders(mode: str, dry_run: bool) -> None:
    """Block real order placement inside CI unless explicitly approved.

    This is a deliberate safety gate: automated pipelines must default to
    dry-run behavior. An operator has to opt in per-run by setting
    ``ALLOW_REAL_ORDERS=true`` (and ``ALLOW_LIVE_TRADING=true`` for live mode)
    as approved secrets before any CI-triggered run can submit real orders.

    Raises:
        PermissionError: if a non-dry-run order is attempted in CI without
            the required approval environment variables set.
    """
    if dry_run or not is_ci_runtime():
        return

    if os.getenv("ALLOW_REAL_ORDERS", "").lower() != "true":
        raise PermissionError(
            "Refusing non-dry-run order placement in CI. Set ALLOW_REAL_ORDERS=true in approved secrets."
        )

    if mode == "live" and os.getenv("ALLOW_LIVE_TRADING", "").lower() != "true":
        raise PermissionError("Refusing live order placement in CI. Set ALLOW_LIVE_TRADING=true in approved secrets.")


def resolve_credentials(mode: str) -> ModeCredentials:
    """Resolve Alpaca API credentials for the given trading mode.

    Prefers an OAuth token when present, otherwise falls back to an API
    key/secret pair. Paper and live modes read from distinct environment
    variable namespaces (``ALPACA_PAPER_*`` / ``ALPACA_LIVE_*``) with legacy
    fallbacks for backward compatibility.

    Args:
        mode: ``"paper"`` or ``"live"``.

    Raises:
        ValueError: if neither an OAuth token nor a complete API key/secret
            pair can be found for the requested mode.
    """
    if mode == "paper":
        oauth_token = os.getenv("ALPACA_PAPER_OAUTH_TOKEN") or os.getenv("ALPACA_OAUTH_TOKEN")
        api_key = os.getenv("ALPACA_PAPER_API_KEY") or os.getenv("ALPACA_API_KEY")
        api_secret = os.getenv("ALPACA_PAPER_API_SECRET") or os.getenv("ALPACA_API_SECRET")
        paper = True
    else:
        oauth_token = os.getenv("ALPACA_LIVE_OAUTH_TOKEN") or os.getenv("ALPACA_OAUTH_TOKEN")
        api_key = os.getenv("ALPACA_LIVE_API_KEY") or os.getenv("ALPACA_INDIVIDUAL_API_KEY")
        api_secret = os.getenv("ALPACA_LIVE_API_SECRET") or os.getenv("ALPACA_INDIVIDUAL_API_SECRET_KEY")
        paper = False

    if oauth_token:
        return ModeCredentials(api_key=None, api_secret=None, oauth_token=oauth_token, paper=paper)

    missing = []
    if not api_key:
        missing.append("API key")
    if not api_secret:
        missing.append("API secret")
    if missing:
        raise ValueError(
            f"Missing Alpaca credentials for {mode} mode: {', '.join(missing)}. "
            f"Alternatively set {'ALPACA_PAPER_OAUTH_TOKEN' if mode == 'paper' else 'ALPACA_LIVE_OAUTH_TOKEN'}"
        )

    return ModeCredentials(api_key=api_key, api_secret=api_secret, oauth_token=None, paper=paper)
