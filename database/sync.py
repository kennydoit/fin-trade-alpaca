"""Sync portfolio data from Alpaca and strategy.json into the database.

This module handles:
- Fetching current positions from Alpaca
- Classifying positions by strategy (core/growth/short_term)
- Backfilling transaction history from Alpaca orders
- Upserting asset metadata and position records
- Recording strategy changes in audit trail
- Enriching asset metadata with company info from yfinance
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Set, List, Any, Tuple
from zoneinfo import ZoneInfo

from alpaca.trading.client import TradingClient

# Use US Eastern Time (handles EST/EDT automatically)
EASTERN_TZ = ZoneInfo("America/New_York")


def load_strategy_config(config_path: Path) -> dict:
    """Load and parse strategy.json configuration."""
    if not config_path.exists():
        raise FileNotFoundError(f"Strategy config not found: {config_path}")
    
    with config_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_strategy_symbols(config: dict) -> Dict[str, str]:
    """Extract symbol→strategy mapping from strategy config.
    
    Returns:
        Dict mapping symbol to strategy name ('core', 'growth', 'short_term')
    """
    symbol_to_strategy = {}
    
    buckets = config.get("buckets", {})
    for bucket_name in ["core", "growth", "short_term"]:
        bucket = buckets.get(bucket_name, {})
        assets = bucket.get("assets", {})
        
        # Handle both dict (symbol→weight) and list formats
        if isinstance(assets, dict):
            for symbol in assets.keys():
                symbol_upper = symbol.strip().upper()
                if symbol_upper in symbol_to_strategy:
                    raise ValueError(
                        f"Symbol {symbol_upper} appears in multiple strategies: "
                        f"{symbol_to_strategy[symbol_upper]} and {bucket_name}"
                    )
                symbol_to_strategy[symbol_upper] = bucket_name
        elif isinstance(assets, list):
            for symbol in assets:
                symbol_upper = str(symbol).strip().upper()
                if symbol_upper in symbol_to_strategy:
                    raise ValueError(
                        f"Symbol {symbol_upper} appears in multiple strategies"
                    )
                symbol_to_strategy[symbol_upper] = bucket_name
    
    return symbol_to_strategy


def fetch_company_info(symbol: str) -> Optional[Tuple[str, str, str]]:
    """Fetch company information from yfinance.
    
    Returns:
        Tuple of (sector, industry, company_name) or None if fetch fails
    """
    try:
        import yfinance as yf
    except ImportError:
        return None
    
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        
        sector = info.get("sector")
        industry = info.get("industry")
        company_name = info.get("longName") or info.get("shortName")
        
        return (sector, industry, company_name)
    except Exception:
        return None


def upsert_asset(cursor: sqlite3.Cursor, position: Any, enrich: bool = True) -> None:
    """Insert or update asset metadata from a position object.
    
    Args:
        cursor: Database cursor
        position: Alpaca position object
        enrich: If True, fetch company info from yfinance for new/incomplete assets
    """
    symbol = str(position.symbol).strip().upper()
    asset_id = str(getattr(position, "asset_id", "")) if getattr(position, "asset_id", None) else None
    exchange = str(getattr(position, "exchange", "")).replace("AssetExchange.", "")
    asset_class = str(getattr(position, "asset_class", "")).replace("AssetClass.", "")
    asset_marginable = 1 if getattr(position, "asset_marginable", False) else 0
    
    # Check if asset needs enrichment
    needs_enrichment = False
    if enrich:
        cursor.execute(
            "SELECT sector, industry, company_name FROM assets WHERE symbol = ?",
            (symbol,)
        )
        existing = cursor.fetchone()
        if not existing or not all(existing):
            needs_enrichment = True
    
    # Fetch company info if needed
    sector, industry, company_name = None, None, None
    if needs_enrichment:
        company_info = fetch_company_info(symbol)
        if company_info:
            sector, industry, company_name = company_info
    
    # Insert or update asset with basic data and enrichment if available
    if needs_enrichment and company_info:
        cursor.execute("""
            INSERT INTO assets (
                symbol, asset_id, exchange, asset_class, asset_marginable,
                sector, industry, company_name, last_updated
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(symbol) DO UPDATE SET
                asset_id = excluded.asset_id,
                exchange = excluded.exchange,
                asset_class = excluded.asset_class,
                asset_marginable = excluded.asset_marginable,
                sector = COALESCE(excluded.sector, sector),
                industry = COALESCE(excluded.industry, industry),
                company_name = COALESCE(excluded.company_name, company_name),
                last_updated = CURRENT_TIMESTAMP
        """, (symbol, asset_id, exchange, asset_class, asset_marginable,
              sector, industry, company_name))
    else:
        cursor.execute("""
            INSERT INTO assets (symbol, asset_id, exchange, asset_class, asset_marginable, last_updated)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(symbol) DO UPDATE SET
                asset_id = excluded.asset_id,
                exchange = excluded.exchange,
                asset_class = excluded.asset_class,
                asset_marginable = excluded.asset_marginable,
                last_updated = CURRENT_TIMESTAMP
        """, (symbol, asset_id, exchange, asset_class, asset_marginable))


def upsert_position(cursor: sqlite3.Cursor, position: Any, account: str, 
                    strategy: str, first_acquired_at: Optional[str] = None) -> None:
    """Insert or update a position record."""
    symbol = str(position.symbol).strip().upper()
    qty = float(position.qty)
    side = str(getattr(position, "side", "LONG")).replace("PositionSide.", "")
    avg_entry_price = float(position.avg_entry_price) if position.avg_entry_price else None
    current_price = float(position.current_price) if position.current_price else None
    market_value = float(position.market_value) if position.market_value else None
    cost_basis = float(position.cost_basis) if position.cost_basis else None
    unrealized_pl = float(position.unrealized_pl) if position.unrealized_pl else None
    unrealized_plpc = float(position.unrealized_plpc) if position.unrealized_plpc else None
    
    # Check if position exists and get current strategy
    cursor.execute("""
        SELECT strategy, first_acquired_at FROM positions 
        WHERE symbol = ? AND account = ?
    """, (symbol, account))
    existing = cursor.fetchone()
    
    old_strategy = existing[0] if existing else None
    existing_acquired_at = existing[1] if existing else None
    
    # Use existing acquired date if present, otherwise use provided or current timestamp (EST)
    acquired_at = existing_acquired_at or first_acquired_at or datetime.now(EASTERN_TZ).isoformat()
    
    # Upsert position
    cursor.execute("""
        INSERT INTO positions (
            symbol, account, strategy, qty, side, avg_entry_price, current_price,
            market_value, cost_basis, unrealized_pl, unrealized_plpc,
            first_acquired_at, last_updated
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(symbol, account) DO UPDATE SET
            strategy = excluded.strategy,
            qty = excluded.qty,
            side = excluded.side,
            avg_entry_price = excluded.avg_entry_price,
            current_price = excluded.current_price,
            market_value = excluded.market_value,
            cost_basis = excluded.cost_basis,
            unrealized_pl = excluded.unrealized_pl,
            unrealized_plpc = excluded.unrealized_plpc,
            last_updated = CURRENT_TIMESTAMP
    """, (symbol, account, strategy, qty, side, avg_entry_price, current_price,
          market_value, cost_basis, unrealized_pl, unrealized_plpc, acquired_at))
    
    # Strategy assignments are treated as permanent bucket classifications.
    # Do not write synthetic history rows during sync.


def backfill_transactions(cursor: sqlite3.Cursor, client: TradingClient, 
                         account: str, symbol_to_strategy: Dict[str, str]) -> int:
    """Backfill transactions from Alpaca order history.
    
    Returns:
        Number of new transactions inserted
    """
    print(f"Fetching order history for {account} account...")
    
    orders = []
    try:
        orders = client.get_orders()
    except Exception as e:
        print(f"  get_orders() failed: {e}")
        try:
            orders = client.get_all_orders()
            print(f"  Using get_all_orders() - found {len(orders)} orders")
        except Exception as e2:
            print(f"  get_all_orders() also failed: {e2}")
            return 0
    
    if not orders:
        print("  No orders found")
        return 0
    
    # Filter to filled orders
    filled_orders = [o for o in orders if hasattr(o, 'status') and str(o.status).lower() == 'filled']
    print(f"  Found {len(filled_orders)} filled orders out of {len(orders)} total")
    print(f"  Found {len(filled_orders)} filled orders out of {len(orders)} total")
    
    new_count = 0
    for order in filled_orders:
        order_id = str(order.id) if order.id else None
        
        # Skip if already in database
        if order_id:
            cursor.execute("SELECT 1 FROM transactions WHERE order_id = ?", (order_id,))
            if cursor.fetchone():
                continue
        
        symbol = str(order.symbol).strip().upper()
        side = str(order.side).replace("OrderSide.", "")
        qty = float(order.qty) if hasattr(order, 'qty') and order.qty else None
        notional = float(order.notional) if hasattr(order, 'notional') and order.notional else None
        filled_price = float(order.filled_avg_price) if hasattr(order, 'filled_avg_price') and order.filled_avg_price else None
        submitted_at = str(order.submitted_at) if hasattr(order, 'submitted_at') else None
        filled_at = str(order.filled_at) if hasattr(order, 'filled_at') else None
        status = str(order.status) if hasattr(order, 'status') else None
        order_type = str(order.type).replace("OrderType.", "") if hasattr(order, 'type') else None
        time_in_force = str(order.time_in_force).replace("TimeInForce.", "") if hasattr(order, 'time_in_force') else None
        
        # Determine strategy at time of order (use current mapping as best guess)
        strategy = symbol_to_strategy.get(symbol, "short_term")
        
        # Extract stop loss and take profit if present
        stop_loss = None
        take_profit = None
        if hasattr(order, 'stop_loss') and order.stop_loss:
            stop_loss = float(order.stop_loss.stop_price) if hasattr(order.stop_loss, 'stop_price') else None
        if hasattr(order, 'take_profit') and order.take_profit:
            take_profit = float(order.take_profit.limit_price) if hasattr(order.take_profit, 'limit_price') else None
        
        cursor.execute("""
            INSERT INTO transactions (
                symbol, account, order_id, side, qty, notional, filled_price,
                submitted_at, filled_at, status, order_type, strategy,
                stop_loss, take_profit, time_in_force
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (symbol, account, order_id, side, qty, notional, filled_price,
              submitted_at, filled_at, status, order_type, strategy,
              stop_loss, take_profit, time_in_force))
        
        new_count += 1
    
    return new_count


def get_first_acquisition_date(cursor: sqlite3.Cursor, symbol: str, account: str) -> Optional[str]:
    """Get the earliest filled_at date for BUY orders of this symbol."""
    cursor.execute("""
        SELECT MIN(filled_at) FROM transactions
        WHERE symbol = ? AND account = ? AND side = 'BUY' AND filled_at IS NOT NULL
    """, (symbol, account))
    result = cursor.fetchone()
    return result[0] if result and result[0] else None


def sync_account(db_path: Path, client: TradingClient, account: str, 
                 strategy_config: dict, backfill: bool = True) -> Dict[str, int]:
    """Sync positions and transactions for one account.
    
    Returns:
        Dict with counts: positions_synced, transactions_added, assets_enriched
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Get strategy classifications from config
    symbol_to_strategy = get_strategy_symbols(strategy_config)
    
    # Fetch current positions from Alpaca
    print(f"Fetching positions from {account} account...")
    positions = client.get_all_positions()
    print(f"Found {len(positions)} positions")
    
    # Backfill transactions first (if enabled) so we can get acquisition dates
    transactions_added = 0
    if backfill:
        transactions_added = backfill_transactions(cursor, client, account, symbol_to_strategy)
        print(f"Added {transactions_added} new transactions")
        conn.commit()
    
    # Sync each position
    print("Syncing positions and enriching asset data...")
    positions_synced = 0
    assets_enriched = 0
    
    for position in positions:
        symbol = str(position.symbol).strip().upper()
        
        # Classify by strategy
        strategy = symbol_to_strategy.get(symbol, "short_term")
        
        # Get first acquisition date from transactions
        first_acquired_at = get_first_acquisition_date(cursor, symbol, account)
        
        # Check if asset needs enrichment before upserting
        cursor.execute(
            "SELECT sector, industry, company_name FROM assets WHERE symbol = ?",
            (symbol,)
        )
        existing = cursor.fetchone()
        needs_enrichment = not existing or not all(existing)
        
        # Upsert asset metadata (with enrichment if needed)
        upsert_asset(cursor, position, enrich=True)
        
        # Check if enrichment was performed
        if needs_enrichment:
            cursor.execute(
                "SELECT sector, industry, company_name FROM assets WHERE symbol = ?",
                (symbol,)
            )
            after = cursor.fetchone()
            if after and all(after):
                assets_enriched += 1
                sector = after[0] or "N/A"
                industry = after[1] or "N/A"
                print(f"  {symbol}: Enriched with {sector} / {industry}")
        
        # Upsert position
        upsert_position(cursor, position, account, strategy, first_acquired_at)
        
        positions_synced += 1
    
    conn.commit()
    conn.close()
    
    return {
        "positions_synced": positions_synced,
        "transactions_added": transactions_added,
        "assets_enriched": assets_enriched
    }


if __name__ == "__main__":
    # Quick test
    from database.schema import initialize_database
    db_path = initialize_database()
    print(f"Database ready: {db_path}")
