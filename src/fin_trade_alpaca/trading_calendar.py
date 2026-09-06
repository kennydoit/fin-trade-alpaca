"""Market-calendar gating: decide whether a scheduled run should trade today.

Rationale (predictability):
    Scheduled runs (cron/EventBridge/Task Scheduler) fire on a fixed
    wall-clock schedule regardless of market holidays or the configured
    trading cadence. This module is the single source of truth for "should
    today's scheduled invocation actually place orders?" -- keeping that
    decision in one place (rather than duplicated per-runner) means the
    daily/weekly/monthly cadence behaves identically everywhere it's used.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetCalendarRequest


def get_market_open_days(client: TradingClient, start: date, end: date) -> set[date]:
    """Return the set of dates the market is open for trading, inclusive of both ends."""
    cal = client.get_calendar(GetCalendarRequest(start=start, end=end))
    return {entry.date for entry in cal}


def compute_effective_trade_days(client: TradingClient, year: int, month: int) -> dict[str, date]:
    """Compute the effective trading days for the monthly cadence's two anchors.

    The "monthly" cadence trades on the 15th and on the last calendar day of
    the month, but shifts forward to the next open market day when the
    anchor itself falls on a weekend/holiday.

    Returns:
        A dict with keys ``"mid_month"`` and ``"month_end"`` mapped to the
        effective (market-open) date for each anchor.

    Raises:
        RuntimeError: if no market day can be found within a week of an anchor.
    """
    month_last_day = calendar.monthrange(year, month)[1]
    anchors = {
        "mid_month": date(year, month, 15),
        "month_end": date(year, month, month_last_day),
    }

    effective_days: dict[str, date] = {}
    for name, anchor in anchors.items():
        search_end = anchor + timedelta(days=7)
        market_days = sorted(get_market_open_days(client, anchor, search_end))
        valid_days = [d for d in market_days if d >= anchor]
        if not valid_days:
            raise RuntimeError(f"Unable to determine market day for {name} anchor {anchor}.")
        effective_days[name] = valid_days[0]

    return effective_days


def should_run_today(  # noqa: PLR0911 - branchy by design; one return per (frequency, gate) outcome for clarity
    client: TradingClient, today: date, trading_frequency: str = "monthly"
) -> tuple[bool, str]:
    """Determine if trading should occur today based on the configured trading frequency.

    Args:
        client: Alpaca trading client (used to query the market calendar).
        today: Today's date (should be computed in US/Eastern by the caller).
        trading_frequency: One of ``"daily"``, ``"weekly"``, ``"monthly"``.
            - ``"daily"``: trade every market-open day.
            - ``"weekly"``: trade on the first market-open day of each week (usually Monday).
            - ``"monthly"``: trade on the 15th and last day of the month (or the next open day).

    Returns:
        A ``(should_run, reason_message)`` tuple. ``reason_message`` is always
        populated (even when ``should_run`` is False) so callers can log why a
        scheduled run was skipped.

    Raises:
        ValueError: if ``trading_frequency`` is not one of the supported values.
    """
    trading_frequency = trading_frequency.lower()

    if trading_frequency == "daily":
        market_open_today = get_market_open_days(client, today, today)
        if market_open_today:
            return True, f"Date gate passed (daily trading, market open on {today.isoformat()})."
        return False, f"Date gate skipped (market closed on {today.isoformat()})."

    if trading_frequency == "weekly":
        # Trade on the first market-open day of the current week (Mon-Sun).
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)

        market_days = sorted(get_market_open_days(client, week_start, week_end))
        if not market_days:
            return False, f"Date gate skipped (no market days this week starting {week_start.isoformat()})."

        first_market_day = market_days[0]
        if today == first_market_day:
            return True, f"Date gate passed (weekly trading, first market day of week: {first_market_day.isoformat()})."
        return False, (
            f"Date gate skipped (weekly trading, waiting for first market day: {first_market_day.isoformat()})."
        )

    if trading_frequency == "monthly":
        effective_days = compute_effective_trade_days(client, today.year, today.month)
        reason = ", ".join(f"{k}={v.isoformat()}" for k, v in effective_days.items())
        if today in set(effective_days.values()):
            return True, f"Date gate passed ({reason})."
        return False, f"Date gate skipped. Today={today.isoformat()} with targets {reason}."

    raise ValueError(f"Invalid trading_frequency: {trading_frequency}. Must be 'daily', 'weekly', or 'monthly'.")
