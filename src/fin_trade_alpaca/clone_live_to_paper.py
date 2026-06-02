from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
import csv
from datetime import datetime

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

from fin_trade_alpaca.optimize_and_buy import load_environment_for_mode, resolve_credentials


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Clone live Alpaca position dollar values into paper strategy format. "
            "Step 1 liquidates all paper positions. Step 2 writes a strategy JSON "
            "for optimize_and_buy.py to rebuild paper using live dollar targets."
        )
    )
    parser.add_argument(
        "--output",
        default="configs/paper_clone_strategy.json",
        help="Output strategy JSON path. Defaults to configs/paper_clone_strategy.json.",
    )
    parser.add_argument(
        "--wait-timeout-sec",
        type=int,
        default=180,
        help="Max seconds to wait for paper liquidations to clear. Defaults to 180.",
    )
    parser.add_argument(
        "--poll-interval-sec",
        type=int,
        default=2,
        help="Polling interval while waiting for paper positions to clear. Defaults to 2.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview actions and generated strategy without submitting paper liquidation orders.",
    )
    parser.add_argument(
        "--auto-execute-paper",
        action="store_true",
        help=(
            "After writing the clone strategy, immediately run optimize_and_buy.py "
            "in paper mode against that strategy file."
        ),
    )
    parser.add_argument(
        "--min-order-notional",
        type=Decimal,
        default=Decimal("0.01"),
        help="Minimum order notional forwarded to optimize_and_buy.py. Defaults to 0.01.",
    )
    return parser.parse_args()


def get_client_for_mode(mode: str) -> TradingClient:
    load_environment_for_mode(mode)
    creds = resolve_credentials(mode)
    return TradingClient(
        api_key=creds.api_key,
        secret_key=creds.api_secret,
        oauth_token=creds.oauth_token,
        paper=creds.paper,
    )


def to_money(value: str | float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)


def get_paper_position_count(paper_client: TradingClient) -> int:
    return len(paper_client.get_all_positions())


def liquidate_paper_positions(
    paper_client: TradingClient,
    dry_run: bool,
    wait_timeout_sec: int,
    poll_interval_sec: int,
) -> bool:
    positions = paper_client.get_all_positions()
    if not positions:
        print("Paper account already has no positions to liquidate.")
        return True

    print(f"Paper liquidation target count: {len(positions)}")
    for pos in positions:
        print(f"  SELL ALL {pos.symbol} qty={pos.qty}")

    if dry_run:
        print("Dry run enabled. Skipping paper liquidation order submission.")
        return True

    paper_client.close_all_positions()
    print("Submitted close_all_positions for paper account.")

    started = time.time()
    while True:
        remaining = get_paper_position_count(paper_client)
        if remaining == 0:
            print("Paper positions fully liquidated.")
            return True

        elapsed = int(time.time() - started)
        if elapsed >= wait_timeout_sec:
            print(
                "Timed out waiting for paper liquidations to fully clear. "
                f"Remaining positions: {remaining}. Attempting per-symbol fallback sells."
            )

            # Attempt to cancel any lingering orders, then per-symbol market sells as a fallback
            try:
                paper_client.cancel_all_orders()
                print("Attempted to cancel any lingering open orders before fallback sells.")
            except Exception:
                print("Unable to cancel lingering orders before fallback; proceeding to per-symbol sells.")

            # Attempt per-symbol market sells as a fallback
            try:
                remaining_positions = paper_client.get_all_positions()
            except Exception:
                remaining_positions = []

            for pos in remaining_positions:
                sym = str(pos.symbol).strip().upper()
                mv = Decimal(str(pos.market_value)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
                print(f"Fallback SELL MARKET {sym} for notional ${mv}")
                try:
                    req = MarketOrderRequest(
                        symbol=sym,
                        notional=float(mv),
                        side=OrderSide.SELL,
                        time_in_force=TimeInForce.DAY,
                    )
                    paper_client.submit_order(req)
                    print(f"Submitted fallback sell for {sym} by notional ${mv}")
                except Exception as ex:
                    print(f"Fallback sell by notional failed for {sym}: {ex}. Trying qty-based sell.")
                    # Try qty-based sell as a fallback
                    try:
                        qty = float(pos.qty)
                        req2 = MarketOrderRequest(
                            symbol=sym,
                            qty=qty,
                            side=OrderSide.SELL,
                            time_in_force=TimeInForce.DAY,
                        )
                        paper_client.submit_order(req2)
                        print(f"Submitted fallback sell for {sym} by qty {qty}")
                    except Exception as ex2:
                        print(f"Fallback sell by qty failed for {sym}: {ex2}")

            # Wait a short period for fallback sells to clear
            extra_wait = min(60, wait_timeout_sec)
            print(f"Waiting {extra_wait}s for fallback sells to clear...")
            time.sleep(extra_wait)

            remaining_after = get_paper_position_count(paper_client)
            if remaining_after == 0:
                print("Paper positions cleared after fallback sells.")
                return True
            print(f"Positions still remain after fallback: {remaining_after}. Giving up.")
            return False

        # Diagnostic: list open orders if API supports it
        try:
            open_orders = paper_client.get_all_orders(status="open")
        except Exception:
            try:
                open_orders = paper_client.get_orders(status="open")
            except Exception:
                open_orders = []

        if open_orders:
            print(f"Open orders detected: {len(open_orders)}")
            for o in open_orders:
                oid = getattr(o, "id", getattr(o, "order_id", "<id?>"))
                sym = getattr(o, "symbol", getattr(o, "client_order_id", "<sym?>"))
                print(f"  order id={oid} symbol={sym}")
            # Try canceling all open orders to allow closes to fill
            try:
                paper_client.cancel_all_orders()
                print("Canceled open orders to unblock liquidations.")
            except Exception:
                print("Unable to cancel open orders via API; continuing to wait.")

        print(f"Waiting for paper liquidations to clear... remaining={remaining}")
        time.sleep(max(1, poll_interval_sec))


def get_live_long_position_values(live_client: TradingClient) -> dict[str, Decimal]:
    symbol_values: dict[str, Decimal] = {}
    for pos in live_client.get_all_positions():
        market_value = Decimal(str(pos.market_value))
        if market_value <= Decimal("0"):
            # Skip non-long exposure for this cloning flow.
            continue

        symbol = str(pos.symbol).strip().upper()
        current = symbol_values.get(symbol, Decimal("0"))
        symbol_values[symbol] = current + market_value

    return symbol_values


def build_strategy_for_exact_dollar_targets(
    live_symbol_values: dict[str, Decimal],
    paper_cash_after_liquidation: Decimal,
) -> dict:
    # optimize_and_buy.py computes each order as spendable_cash * (bucket_weight * asset_weight).
    # Putting all assets into core with core weight 1.0 allows asset_weight = target_dollars / paper_cash.
    core_assets: dict[str, float] = {}
    for symbol, target_value in sorted(live_symbol_values.items()):
        if target_value <= Decimal("0"):
            continue
        weight = (target_value / paper_cash_after_liquidation).quantize(
            Decimal("0.000000001"),
            rounding=ROUND_DOWN,
        )
        if weight > Decimal("0"):
            core_assets[symbol] = float(weight)

    return {
        "buckets": {
            "core": {
                "weight": 1.0,
                "assets": core_assets,
            },
            "growth": {
                "weight": 0.0,
                "assets": {},
            },
            "short_term": {
                "weight": 0.0,
                "assets": {},
            },
        }
    }


def write_strategy(path: Path, strategy: dict) -> None:
    # Ensure parent directory exists (configs/ by default)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(strategy, f, indent=2)
        f.write("\n")


def produce_side_by_side_report(live_client: TradingClient, paper_client: TradingClient, report_path: Path) -> None:
    live_positions = {p.symbol.strip().upper(): p for p in live_client.get_all_positions()}
    paper_positions = {p.symbol.strip().upper(): p for p in paper_client.get_all_positions()}
    symbols = sorted(set(list(live_positions.keys()) + list(paper_positions.keys())))

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["symbol", "live_qty", "live_value", "paper_qty", "paper_value"])
        for s in symbols:
            lp = live_positions.get(s)
            pp = paper_positions.get(s)
            lq = getattr(lp, "qty", 0) if lp else 0
            lv = getattr(lp, "market_value", 0) if lp else 0
            pq = getattr(pp, "qty", 0) if pp else 0
            pv = getattr(pp, "market_value", 0) if pp else 0
            writer.writerow([s, str(lq), str(lv), str(pq), str(pv)])

    print(f"Wrote side-by-side report to {report_path}")


def produce_text_clone_report(live_client: TradingClient, paper_client: TradingClient, reports_dir: Path) -> Path:
    # timestamp format requested by user: YYYYMMDD:HHMM
    now = datetime.now()
    ts_colon = now.strftime("%Y%m%d:%H%M")
    # Windows filenames cannot include ':', replace with '-'
    ts_safe = ts_colon.replace(':', '-')
    filename = f"clone report - {ts_safe}.txt"
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = reports_dir / filename

    live_positions = {p.symbol.strip().upper(): p for p in live_client.get_all_positions()}
    paper_positions = {p.symbol.strip().upper(): p for p in paper_client.get_all_positions()}
    symbols = sorted(set(list(live_positions.keys()) + list(paper_positions.keys())))

    total_live = Decimal('0')
    total_paper = Decimal('0')

    with out_path.open('w', encoding='utf-8') as f:
        f.write(f"Clone report generated: {ts_colon}\n")
        f.write("\n")
        f.write("symbol | live_qty | live_value | paper_qty | paper_value\n")
        f.write("-----------------------------------------------------\n")
        for s in symbols:
            lp = live_positions.get(s)
            pp = paper_positions.get(s)
            lq = getattr(lp, 'qty', 0) if lp else 0
            lv = Decimal(str(getattr(lp, 'market_value', 0) if lp else 0))
            pq = getattr(pp, 'qty', 0) if pp else 0
            pv = Decimal(str(getattr(pp, 'market_value', 0) if pp else 0))
            total_live += lv
            total_paper += pv
            f.write(f"{s} | {lq} | ${lv.quantize(Decimal('0.01'))} | {pq} | ${pv.quantize(Decimal('0.01'))}\n")

        f.write("\n")
        f.write(f"Total live value: ${total_live.quantize(Decimal('0.01'))}\n")
        f.write(f"Total paper value: ${total_paper.quantize(Decimal('0.01'))}\n")

    print(f"Wrote text clone report to {out_path} (timestamp {ts_colon})")
    return out_path


def execute_paper_clone_orders(
    strategy_path: Path,
    live_total: Decimal,
    min_order_notional: Decimal,
    dry_run: bool,
) -> int:
    cmd = [
        sys.executable,
        "src/fin_trade_alpaca/optimize_and_buy.py",
        "--mode",
        "paper",
        "--run-type",
        "adhoc",
        "--config",
        str(strategy_path),
        "--max-notional",
        str(live_total.quantize(Decimal("0.01"), rounding=ROUND_DOWN)),
        "--min-order-notional",
        str(min_order_notional.quantize(Decimal("0.01"), rounding=ROUND_DOWN)),
    ]
    if dry_run:
        cmd.append("--dry-run")

    print("Auto-executing paper clone command:", " ".join(cmd))
    result = subprocess.run(cmd, check=False)
    return result.returncode


def main() -> int:
    args = parse_args()

    try:
        paper_client = get_client_for_mode("paper")
        live_client = get_client_for_mode("live")
    except Exception as ex:
        print(f"Credential/client initialization failed: {ex}")
        return 2

    print("Step 1: Liquidating all paper positions.")
    liquidated = liquidate_paper_positions(
        paper_client=paper_client,
        dry_run=args.dry_run,
        wait_timeout_sec=max(1, args.wait_timeout_sec),
        poll_interval_sec=max(1, args.poll_interval_sec),
    )
    if not liquidated:
        return 2

    paper_account = paper_client.get_account()
    paper_cash = to_money(paper_account.cash)

    print("Step 2: Snapshotting live position market values.")
    live_symbol_values = get_live_long_position_values(live_client)
    live_total = to_money(sum(live_symbol_values.values(), Decimal("0")))

    print(f"Paper cash after liquidation: ${paper_cash}")
    print(f"Live long position total: ${live_total}")

    if not live_symbol_values:
        print("No live long positions found. Writing empty tradable strategy.")

    if live_total > paper_cash:
        print(
            "Paper cash is lower than live long market value total. "
            "Cannot create exact dollar-match strategy without additional cash."
        )
        print(f"Shortfall: ${(live_total - paper_cash).quantize(Decimal('0.01'), rounding=ROUND_DOWN)}")
        return 2

    if paper_cash <= Decimal("0") and live_total > Decimal("0"):
        print("Paper cash is 0 after liquidation; unable to clone live dollar targets.")
        return 2

    strategy = build_strategy_for_exact_dollar_targets(live_symbol_values, paper_cash or Decimal("1"))

    output_path = Path(args.output)
    write_strategy(output_path, strategy)

    core_assets = strategy["buckets"]["core"]["assets"]
    print(f"Wrote clone strategy with {len(core_assets)} symbols to {output_path}")

    for symbol, value in sorted(live_symbol_values.items()):
        print(f"  {symbol}: target_live_value=${to_money(value)}")

    print("Next step:")
    print(
        "  python src/fin_trade_alpaca/optimize_and_buy.py "
        f"--mode paper --run-type adhoc --config {output_path} --min-order-notional 0.01"
    )

    # Always produce a side-by-side report of live vs paper positions as part of the process
    report_path = output_path.parent / f"{output_path.stem}_report.csv"
    try:
        produce_side_by_side_report(live_client, paper_client, report_path)
    except Exception as ex:
        print(f"Failed to produce side-by-side report: {ex}")
    # Also write a human-readable text clone report into reports/
    try:
        reports_dir = Path('reports')
        text_path = produce_text_clone_report(live_client, paper_client, reports_dir)
    except Exception as ex:
        print(f"Failed to produce text clone report: {ex}")

    if args.auto_execute_paper:
        if live_total <= Decimal("0"):
            print("Auto-execute skipped: live long position total is 0.")
            return 0
        rc = execute_paper_clone_orders(
            strategy_path=output_path,
            live_total=live_total,
            min_order_notional=max(Decimal("0.01"), args.min_order_notional),
            dry_run=args.dry_run,
        )
        if rc != 0:
            print(f"Auto-execute command failed with exit code {rc}.")
            return rc
        print("Auto-execute completed successfully.")

    if args.dry_run:
        print("Dry run completed. No liquidation orders were submitted.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
