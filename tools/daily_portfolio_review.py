"""
Daily Portfolio Review - Automated position exit logic.

Evaluates current positions against latest predictions and price movements,
then submits sell orders for positions that meet exit criteria.

Exit Triggers (configurable):
1. Prediction-based: Position dropped out of top N predictions
2. Price-based: Hit stop loss or take profit thresholds
3. Hybrid: Combination of both

Aggressiveness Levels:
- conservative: Exit if dropped out of top 20 OR hit -5% stop
- moderate: Exit if not in top 10 OR hit -3% stop
- aggressive: Exit if not in top 5 OR hit -2% stop
- custom: User-defined thresholds
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import pandas as pd
from decimal import Decimal
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest
from runners.optimize_and_buy import resolve_credentials
from fin_trade_alpaca.env_loader import load_environment_for_mode


# Aggressiveness presets
AGGRESSIVENESS_PRESETS = {
    "conservative": {
        "min_prediction_rank": 20,
        "stop_loss_pct": -5.0,
        "take_profit_pct": 10.0,
        "trailing_stop_pct": None,
    },
    "moderate": {
        "min_prediction_rank": 10,
        "stop_loss_pct": -3.0,
        "take_profit_pct": 7.0,
        "trailing_stop_pct": None,
    },
    "aggressive": {
        "min_prediction_rank": 5,
        "stop_loss_pct": -2.0,
        "take_profit_pct": 5.0,
        "trailing_stop_pct": None,
    },
}


def find_latest_predictions(reports_dir: Path):
    """Find the most recent predictions CSV."""
    if not reports_dir.exists():
        return None
    
    pred_files = sorted(reports_dir.glob("predictions_*.csv"))
    return pred_files[-1] if pred_files else None


def load_predictions(csv_path: Path) -> pd.DataFrame:
    """Load predictions CSV and return sorted by rank."""
    df = pd.read_csv(csv_path)
    if "symbol" not in df.columns:
        raise ValueError("Predictions CSV must have 'symbol' column")
    
    # Ensure symbol is uppercase
    df["symbol"] = df["symbol"].str.upper()
    
    # Add rank if not present (assume already sorted by pred_ret descending)
    if "rank" not in df.columns:
        df["rank"] = range(1, len(df) + 1)
    
    return df


def evaluate_position_exit(
    position,
    current_price: float,
    prediction_rank: int | None,
    config: dict,
) -> tuple[bool, str]:
    """
    Evaluate if a position should be exited.
    
    Returns:
        (should_exit: bool, reason: str)
    """
    symbol = position.symbol
    avg_entry = float(position.avg_entry_price)
    pnl_pct = ((current_price - avg_entry) / avg_entry) * 100
    
    # Check prediction-based exit
    min_rank = config.get("min_prediction_rank")
    if min_rank and prediction_rank is not None:
        if prediction_rank > min_rank:
            return True, f"Dropped to rank #{prediction_rank} (min: {min_rank})"
    
    # Check price-based exits
    stop_loss = config.get("stop_loss_pct")
    if stop_loss and pnl_pct <= stop_loss:
        return True, f"Hit stop loss: {pnl_pct:.2f}% (threshold: {stop_loss}%)"
    
    take_profit = config.get("take_profit_pct")
    if take_profit and pnl_pct >= take_profit:
        return True, f"Hit take profit: {pnl_pct:.2f}% (threshold: {take_profit}%)"
    
    # Check if not in predictions at all (if predictions available)
    if min_rank and prediction_rank is None:
        return True, "Not in current predictions"
    
    return False, ""


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Daily portfolio review - evaluate and exit positions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Aggressiveness Presets:
  conservative: Exit if rank > 20 OR P&L < -5%
  moderate:     Exit if rank > 10 OR P&L < -3%
  aggressive:   Exit if rank > 5 OR P&L < -2%
  custom:       Use --min-rank, --stop-pct, --take-pct

Examples:
  # Conservative review (paper account)
  python tools/daily_portfolio_review.py --mode paper --aggressiveness conservative

  # Aggressive review with dry-run
  python tools/daily_portfolio_review.py --mode paper --aggressiveness aggressive --dry-run

  # Custom thresholds
  python tools/daily_portfolio_review.py --mode paper --aggressiveness custom --min-rank 15 --stop-pct -4.0 --take-pct 8.0
        """
    )
    parser.add_argument("--mode", choices=["paper", "live"], default="paper", help="Trading mode")
    parser.add_argument(
        "--aggressiveness",
        choices=["conservative", "moderate", "aggressive", "custom"],
        default="moderate",
        help="Exit aggressiveness preset"
    )
    parser.add_argument("--min-rank", type=int, help="Min prediction rank to hold (custom mode)")
    parser.add_argument("--stop-pct", type=float, help="Stop loss percentage (custom mode)")
    parser.add_argument("--take-pct", type=float, help="Take profit percentage (custom mode)")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, don't submit orders")
    parser.add_argument("--predictions", help="Path to predictions CSV (uses latest if omitted)")
    
    args = parser.parse_args()
    
    # Build configuration
    if args.aggressiveness == "custom":
        if args.min_rank is None and args.stop_pct is None and args.take_pct is None:
            parser.error("Custom mode requires at least one of: --min-rank, --stop-pct, --take-pct")
        config = {
            "min_prediction_rank": args.min_rank,
            "stop_loss_pct": args.stop_pct,
            "take_profit_pct": args.take_pct,
        }
    else:
        config = AGGRESSIVENESS_PRESETS[args.aggressiveness].copy()
    
    # Header
    print("=" * 80)
    print(f"DAILY PORTFOLIO REVIEW ({args.mode.upper()} MODE)")
    print("=" * 80)
    print(f"Aggressiveness: {args.aggressiveness}")
    if config.get("min_prediction_rank"):
        print(f"  Min prediction rank: {config['min_prediction_rank']}")
    if config.get("stop_loss_pct"):
        print(f"  Stop loss: {config['stop_loss_pct']}%")
    if config.get("take_profit_pct"):
        print(f"  Take profit: {config['take_profit_pct']}%")
    if args.dry_run:
        print("\n⚠️  DRY RUN MODE - No orders will be submitted")
    print()
    
    # Load credentials
    load_environment_for_mode(args.mode)
    creds = resolve_credentials(args.mode)
    
    # Create clients
    trading_client = TradingClient(
        api_key=creds.api_key,
        secret_key=creds.api_secret,
        oauth_token=creds.oauth_token,
        paper=creds.paper
    )
    
    data_client = StockHistoricalDataClient(
        api_key=creds.api_key,
        secret_key=creds.api_secret
    )
    
    # Load predictions
    predictions_df = None
    if config.get("min_prediction_rank"):
        repo_root = Path(__file__).resolve().parents[1]
        if args.predictions:
            pred_path = Path(args.predictions)
        else:
            reports_dir = repo_root / "reports" / "screener_results"
            pred_path = find_latest_predictions(reports_dir)
        
        if pred_path and pred_path.exists():
            print(f"Loading predictions from: {pred_path.name}")
            predictions_df = load_predictions(pred_path)
            print(f"  Loaded {len(predictions_df)} predictions\n")
        else:
            print("⚠️  No predictions file found - will only use price-based exits\n")
    
    # Get all positions
    print("Fetching current positions...")
    positions = trading_client.get_all_positions()
    
    if not positions:
        print("✓ No positions to review\n")
        return
    
    print(f"Found {len(positions)} position(s)\n")
    
    # Evaluate each position
    positions_to_exit = []
    positions_to_hold = []
    
    for position in positions:
        symbol = position.symbol.upper()
        qty = float(position.qty)
        avg_entry = float(position.avg_entry_price)
        market_value = float(position.market_value)
        
        # Get current price
        try:
            request = StockLatestQuoteRequest(symbol_or_symbols=symbol)
            quote = data_client.get_stock_latest_quote(request)
            current_price = float(quote[symbol].bid_price)
        except Exception as e:
            print(f"{symbol}: ⚠️  Could not fetch current price: {e}")
            continue
        
        # Calculate P&L
        pnl = market_value - (avg_entry * qty)
        pnl_pct = (pnl / (avg_entry * qty)) * 100
        
        # Get prediction rank
        prediction_rank = None
        if predictions_df is not None:
            match = predictions_df[predictions_df["symbol"] == symbol]
            if not match.empty:
                prediction_rank = int(match.iloc[0]["rank"])
        
        # Evaluate exit
        should_exit, reason = evaluate_position_exit(
            position, current_price, prediction_rank, config
        )
        
        # Display
        rank_str = f"#{prediction_rank}" if prediction_rank else "N/A"
        pnl_color = "+" if pnl >= 0 else ""
        
        print(f"{symbol}:")
        print(f"  Qty: {qty:.6f} @ ${avg_entry:.2f} → ${current_price:.2f}")
        print(f"  P&L: {pnl_color}{pnl:.2f} ({pnl_color}{pnl_pct:.2f}%)")
        print(f"  Prediction rank: {rank_str}")
        
        if should_exit:
            print(f"  → EXIT: {reason}")
            positions_to_exit.append({
                "symbol": symbol,
                "qty": qty,
                "current_price": current_price,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "reason": reason,
            })
        else:
            print(f"  → HOLD")
            positions_to_hold.append(symbol)
        print()
    
    # Summary
    print("=" * 80)
    print(f"REVIEW SUMMARY")
    print("=" * 80)
    print(f"Total positions: {len(positions)}")
    print(f"Hold: {len(positions_to_hold)}")
    print(f"Exit: {len(positions_to_exit)}")
    
    if not positions_to_exit:
        print("\n✓ No exits recommended\n")
        return
    
    # Display exit plan
    print(f"\nEXIT PLAN ({len(positions_to_exit)} positions):")
    total_pnl = sum(p["pnl"] for p in positions_to_exit)
    for p in positions_to_exit:
        pnl_sign = "+" if p["pnl"] >= 0 else ""
        print(f"  SELL {p['qty']:.6f} {p['symbol']} @ ${p['current_price']:.2f}")
        print(f"       P&L: {pnl_sign}${p['pnl']:.2f} ({pnl_sign}{p['pnl_pct']:.2f}%) - {p['reason']}")
    
    print(f"\nTotal P&L from exits: ${total_pnl:+.2f}\n")
    
    if args.dry_run:
        print("⚠️  DRY RUN - No orders submitted\n")
        return
    
    # Confirm before submitting
    if args.mode == "live":
        response = input("⚠️  LIVE MODE - Submit sell orders? (type 'YES' to confirm): ")
        if response != "YES":
            print("Cancelled.\n")
            return
    else:
        response = input("Submit sell orders to paper account? (yes/no): ")
        if response.lower() != "yes":
            print("Cancelled.\n")
            return
    
    # Submit sell orders
    print("\nSubmitting sell orders...")
    success_count = 0
    
    for p in positions_to_exit:
        try:
            order_request = MarketOrderRequest(
                symbol=p["symbol"],
                qty=p["qty"],
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
            )
            order = trading_client.submit_order(order_request)
            print(f"✓ {p['symbol']}: Sell order submitted (ID: {order.id})")
            success_count += 1
        except Exception as e:
            print(f"✗ {p['symbol']}: Failed to submit order - {e}")
    
    print(f"\n{'=' * 80}")
    print(f"Submitted {success_count}/{len(positions_to_exit)} sell orders successfully")
    print(f"{'=' * 80}\n")


if __name__ == "__main__":
    main()
