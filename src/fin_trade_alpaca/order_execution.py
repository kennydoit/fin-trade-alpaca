"""Order sizing and submission against the Alpaca trading API.

Rationale (cohesion + risk isolation):
    Everything in this module talks to Alpaca (or nothing at all -- the
    sizing helpers are pure Decimal math). Grouping "how much cash goes into
    each order" and "how do we actually submit/poll orders" together keeps
    all money-movement code in one auditable place, separate from strategy
    config parsing, calendar gating, and momentum-tilt math.
"""

from __future__ import annotations

import time
from decimal import ROUND_DOWN, Decimal

from alpaca.common.exceptions import APIError
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

from fin_trade_alpaca.credentials import ModeCredentials

_TERMINAL_ORDER_STATUSES = ("filled", "canceled", "rejected", "expired")


def choose_spendable_cash(available_cash: Decimal, max_notional: Decimal | None) -> Decimal:
    """Cap the cash available to spend this run at ``max_notional`` (if provided)."""
    if available_cash <= Decimal("0"):
        return Decimal("0")
    if max_notional is None:
        return available_cash
    if max_notional <= Decimal("0"):
        return Decimal("0")
    return min(available_cash, max_notional)


def distribute_notionals(
    spendable_cash: Decimal,
    symbol_weights: dict[str, Decimal],
    min_order_notional: Decimal,
) -> dict[str, Decimal]:
    """Convert symbol weights into dollar notionals, dropping orders below the minimum size."""
    notionals: dict[str, Decimal] = {}
    for symbol, weight in symbol_weights.items():
        if weight <= 0:
            continue
        notional = (spendable_cash * weight).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        if notional >= min_order_notional:
            notionals[symbol] = notional
    return notionals


def submit_orders(
    client: TradingClient,
    notionals: dict[str, Decimal],
    dry_run: bool,
) -> list[str]:
    """Submit fractional-notional market BUY orders for the core/growth buckets."""
    if not notionals:
        print("No orders to submit after min-order filter. Leaving cash unallocated.")
        return []

    print("Order plan:")
    for symbol, notional in sorted(notionals.items()):
        print(f"  BUY {symbol}: ${notional}")

    if dry_run:
        print("Dry run enabled; no orders submitted.")
        return []

    submitted_ids: list[str] = []
    for symbol, notional in sorted(notionals.items()):
        req = MarketOrderRequest(
            symbol=symbol,
            notional=float(notional),
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
        try:
            order = client.submit_order(req)
            submitted_ids.append(order.id)
            print(f"Submitted {symbol} order id={order.id} notional=${notional}")
        except APIError as ex:
            print(f"Order failed for {symbol}: {ex}")

    return submitted_ids


def submit_short_term_orders(
    client: TradingClient,
    orders: list[dict],
    dry_run: bool,
    creds: ModeCredentials | None = None,
) -> list[str]:
    """Submit quantity-based market BUY orders for the short-term (screener-driven) bucket.

    ``orders`` items: ``{symbol, notional, price, stop_pct, take_pct}``.

    NOTE: Alpaca does not support bracket orders with fractional shares, so
    this submits a plain market order and logs the intended stop/take
    targets; a separate post-fill step is responsible for attaching
    protective orders (see ``tools/add_position_protections.py``).
    """
    submitted: list[str] = []
    if not orders:
        return submitted

    data_client = None
    if creds:
        try:
            data_client = StockHistoricalDataClient(api_key=creds.api_key, secret_key=creds.api_secret)
        except Exception as e:
            print(f"  WARNING: Could not create data client: {e}")

    print("Short-term order plan:")
    for o in orders:
        print(f"  BUY {o['symbol']}: ${o['notional']} with stop={o['stop_pct']}% take={o['take_pct']}%")

    if dry_run:
        print("Dry run enabled; no short-term orders submitted.")
        return submitted

    for o in orders:
        symbol = o["symbol"]
        notional = o["notional"]
        screener_price = o.get("price")
        stop_pct = float(o.get("stop_pct", 0.0))
        take_pct = float(o.get("take_pct", 0.0))
        try:
            current_price = screener_price
            if data_client and screener_price:
                try:
                    request = StockLatestQuoteRequest(symbol_or_symbols=symbol)
                    quote = data_client.get_stock_latest_quote(request)
                    if symbol in quote and quote[symbol].ask_price:
                        current_price = float(quote[symbol].ask_price)
                        print(
                            f"  {symbol}: using live ask price ${current_price:.2f} (screener: ${screener_price:.2f})"
                        )
                    else:
                        print(f"  {symbol}: no live quote, using screener price ${screener_price:.2f}")
                except Exception as e:
                    print(f"  {symbol}: could not fetch live price ({e}), using screener price ${screener_price:.2f}")

            if current_price is None or current_price <= 0:
                print(f"  WARNING {symbol}: price is None or invalid ({current_price}), skipping order")
                continue

            qty = float(notional) / float(current_price)
            take_price = round(float(current_price) * (1.0 + take_pct / 100.0), 2)
            stop_price = round(float(current_price) * (1.0 + stop_pct / 100.0), 2)

            print(
                f"  {symbol}: qty={qty:.6f}, stop=${stop_price:.2f} ({stop_pct}%), take=${take_price:.2f} ({take_pct}%)"
            )

            req = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
            )
            order = client.submit_order(req)
            submitted.append(order.id)

            print(f"✓ Submitted {symbol} order id={order.id} qty={qty:.6f}")
            print("  ⚠️  Note: Stop loss/take profit will need to be added separately after fill")
            print(f"     Target stop=${stop_price:.2f}, take=${take_price:.2f}")
        except APIError as ex:
            print(f"Short-term order failed for {symbol}: {ex}")
        except Exception as ex:
            print(f"Short-term order unexpected error for {symbol}: {ex}")

    return submitted


def poll_orders_for_fills(
    client: TradingClient, order_ids: list[str], timeout_sec: int = 180, poll_interval: int = 5
) -> dict[str, str]:
    """Poll Alpaca for the given order IDs until all reach a terminal status or timeout.

    Returns a mapping of ``order_id -> final_status`` (``"unknown"`` if the
    order couldn't be found or the client call failed).
    """
    if not order_ids:
        return {}

    deadline = time.time() + float(timeout_sec)
    pending = set(order_ids)
    final_status: dict[str, str] = {}

    def _find_order_by_id(oid: str):
        # Try common SDK entrypoints in order of preference, falling back to list-and-find.
        try:
            return client.get_order(oid)
        except Exception:
            pass
        try:
            orders = client.get_orders()
            return next((x for x in orders if getattr(x, "id", None) == oid), None)
        except Exception:
            pass
        try:
            orders = client.get_all_orders()
            return next((x for x in orders if getattr(x, "id", None) == oid), None)
        except Exception:
            pass
        return None

    def _status_name_from_order(o) -> str:
        if o is None:
            return "unknown"
        st = getattr(o, "status", None)
        if st is None:
            return "unknown"
        return str(st.name).lower() if hasattr(st, "name") else str(st).lower()

    print(f"Polling for fills for {len(order_ids)} orders, timeout={timeout_sec}s")
    while pending and time.time() < deadline:
        for oid in list(pending):
            try:
                status = _status_name_from_order(_find_order_by_id(oid))
            except Exception as ex:
                status = f"error:{ex}"

            print(f"  order {oid} status={status}")

            if status in _TERMINAL_ORDER_STATUSES:
                final_status[oid] = status
                pending.remove(oid)

        if pending:
            time.sleep(max(1, poll_interval))

    # For any remaining pending orders, attempt one last time to capture a status.
    for oid in pending:
        try:
            final_status[oid] = _status_name_from_order(_find_order_by_id(oid))
        except Exception:
            final_status[oid] = "unknown"

    return final_status
