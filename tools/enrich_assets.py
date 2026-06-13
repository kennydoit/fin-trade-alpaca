"""Enrich asset metadata with company information from yfinance.

This tool fetches sector, industry, and company name for assets in the database
that are missing this information.

Usage:
    python tools/enrich_assets.py [--all] [--symbol SYMBOL]
    
    --all: Enrich all assets, even those already populated
    --symbol: Only enrich specific symbol(s) (can be repeated)
"""
import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Optional, Dict, Any

# Add parent directories to path
repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from database.schema import get_database_path


def fetch_company_info(symbol: str) -> Optional[Dict[str, Any]]:
    """Fetch company information from yfinance.
    
    Returns:
        Dict with keys: sector, industry, longName (or None if fetch fails)
    """
    try:
        import yfinance as yf
    except ImportError:
        print("Warning: yfinance not installed. Run: pip install yfinance")
        return None
    
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        
        return {
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "longName": info.get("longName") or info.get("shortName"),
        }
    except Exception as e:
        print(f"  Warning: Could not fetch info for {symbol}: {e}")
        return None


def enrich_asset(cursor: sqlite3.Cursor, symbol: str, force: bool = False) -> bool:
    """Enrich a single asset with yfinance data.
    
    Args:
        cursor: Database cursor
        symbol: Stock symbol to enrich
        force: If True, update even if data already exists
        
    Returns:
        True if asset was updated, False otherwise
    """
    # Check if enrichment is needed
    if not force:
        cursor.execute(
            "SELECT sector, industry, company_name FROM assets WHERE symbol = ?",
            (symbol,)
        )
        row = cursor.fetchone()
        if not row:
            print(f"  {symbol}: Not in database, skipping")
            return False
        
        sector, industry, company_name = row
        if sector and industry and company_name:
            print(f"  {symbol}: Already enriched, skipping (use --all to force)")
            return False
    
    # Fetch from yfinance
    print(f"  {symbol}: Fetching from yfinance...", end="", flush=True)
    info = fetch_company_info(symbol)
    
    if not info:
        print(" FAILED")
        return False
    
    # Update database
    cursor.execute("""
        UPDATE assets
        SET sector = ?,
            industry = ?,
            company_name = ?,
            last_updated = CURRENT_TIMESTAMP
        WHERE symbol = ?
    """, (
        info.get("sector"),
        info.get("industry"),
        info.get("longName"),
        symbol
    ))
    
    sector = info.get("sector") or "N/A"
    industry = info.get("industry") or "N/A"
    print(f" OK ({sector}, {industry})")
    return True


def enrich_all_assets(db_path: Path, force: bool = False, symbols: Optional[list] = None):
    """Enrich all (or specified) assets in the database.
    
    Args:
        db_path: Path to database
        force: If True, re-fetch even for already enriched assets
        symbols: If provided, only enrich these symbols
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        if symbols:
            # Enrich specific symbols
            asset_symbols = symbols
            print(f"Enriching {len(asset_symbols)} specified symbols...")
        else:
            # Get all symbols from database
            if force:
                cursor.execute("SELECT symbol FROM assets ORDER BY symbol")
            else:
                cursor.execute("""
                    SELECT symbol FROM assets
                    WHERE sector IS NULL OR industry IS NULL OR company_name IS NULL
                    ORDER BY symbol
                """)
            asset_symbols = [row[0] for row in cursor.fetchall()]
            
            if not asset_symbols:
                print("All assets already enriched! Use --all to force re-fetch.")
                return
            
            print(f"Enriching {len(asset_symbols)} assets...")
        
        updated_count = 0
        for symbol in asset_symbols:
            if enrich_asset(cursor, symbol, force):
                updated_count += 1
        
        conn.commit()
        print(f"\n✅ Enriched {updated_count} / {len(asset_symbols)} assets")
        
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Enrich asset metadata with yfinance company information"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Enrich all assets, even those already populated"
    )
    parser.add_argument(
        "--symbol",
        action="append",
        help="Only enrich specific symbol(s) (can be repeated)"
    )
    
    args = parser.parse_args()
    
    db_path = get_database_path()
    if not db_path.exists():
        print(f"❌ Database not found: {db_path}")
        print("Run: python tools\\sync_portfolio_db.py paper")
        return 1
    
    try:
        enrich_all_assets(db_path, force=args.all, symbols=args.symbol)
        return 0
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
