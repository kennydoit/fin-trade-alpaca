"""CLI tool to sync portfolio database from Alpaca and strategy.json.

Usage:
    python tools/sync_portfolio_db.py paper
    python tools/sync_portfolio_db.py live
    python tools/sync_portfolio_db.py both
    python tools/sync_portfolio_db.py paper --no-backfill
"""
from pathlib import Path
import sys
import argparse

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / 'src'))

from database.schema import initialize_database, get_database_path
from database.sync import sync_account, load_strategy_config
from runners.optimize_and_buy import resolve_credentials
from fin_trade_alpaca.env_loader import load_environment_for_mode
from alpaca.trading.client import TradingClient


def sync_one_account(account: str, backfill: bool, repo_root: Path) -> None:
    """Sync portfolio database for one account."""
    print(f"\n{'='*60}")
    print(f"Syncing {account.upper()} account")
    print('='*60)
    
    # Load credentials and create client
    load_environment_for_mode(account)
    creds = resolve_credentials(account)
    client = TradingClient(
        api_key=creds.api_key,
        secret_key=creds.api_secret,
        oauth_token=creds.oauth_token,
        paper=creds.paper
    )
    
    # Load strategy config
    config_path = repo_root / "configs" / "strategy.json"
    strategy_config = load_strategy_config(config_path)
    
    # Get or create database
    db_path = get_database_path(repo_root)
    if not db_path.exists():
        print("Database doesn't exist. Initializing...")
        initialize_database(repo_root)
    
    # Sync
    results = sync_account(db_path, client, account, strategy_config, backfill)
    
    print(f"\n✓ Sync complete for {account}:")
    print(f"  - Positions synced: {results['positions_synced']}")
    print(f"  - Assets enriched: {results['assets_enriched']}")
    print(f"  - Transactions added: {results['transactions_added']}")


def main():
    parser = argparse.ArgumentParser(
        description="Sync portfolio database from Alpaca and strategy.json"
    )
    parser.add_argument(
        "account",
        choices=["paper", "live", "both"],
        help="Which account(s) to sync"
    )
    parser.add_argument(
        "--no-backfill",
        action="store_true",
        help="Skip backfilling transaction history (positions only)"
    )
    parser.add_argument(
        "--config",
        default="configs/strategy.json",
        help="Path to strategy config (default: configs/strategy.json)"
    )
    
    args = parser.parse_args()
    
    repo_root = Path(__file__).resolve().parents[1]
    backfill = not args.no_backfill
    
    try:
        if args.account == "both":
            sync_one_account("paper", backfill, repo_root)
            sync_one_account("live", backfill, repo_root)
        else:
            sync_one_account(args.account, backfill, repo_root)
        
        db_path = get_database_path(repo_root)
        print(f"\n{'='*60}")
        print(f"Database location: {db_path}")
        print('='*60)
        
    except Exception as e:
        print(f"\nError during sync: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
