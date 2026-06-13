"""Database schema definitions and initialization for portfolio tracking.

This module defines the SQLite schema for tracking:
- Assets: symbol metadata (sector, industry, exchange, etc.)
- Positions: current portfolio state per account (paper/live)
- Transactions: complete order history with fills
"""
import sqlite3
from pathlib import Path
from typing import Optional


def get_database_path(repo_root: Optional[Path] = None) -> Path:
    """Return the path to the portfolio database file."""
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[1]
    db_dir = repo_root / "reports" / "portfolio_db"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / "portfolio.db"


def create_schema(db_path: Path) -> None:
    """Create all tables and indexes if they don't exist."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Assets table: reusable metadata
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS assets (
            symbol TEXT PRIMARY KEY,
            asset_id TEXT,
            exchange TEXT,
            asset_class TEXT,
            asset_marginable INTEGER,
            sector TEXT,
            industry TEXT,
            company_name TEXT,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Positions table: current state snapshot
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            account TEXT NOT NULL,
            strategy TEXT,
            qty REAL NOT NULL,
            side TEXT,
            avg_entry_price REAL,
            current_price REAL,
            market_value REAL,
            cost_basis REAL,
            unrealized_pl REAL,
            unrealized_plpc REAL,
            first_acquired_at TIMESTAMP,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            strategy_source TEXT,
            model_type TEXT,
            predicted_return REAL,
            screener_rank INTEGER,
            entry_notes TEXT,
            FOREIGN KEY (symbol) REFERENCES assets(symbol),
            UNIQUE(symbol, account)
        )
    """)

    # Transactions table: historical ledger
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            account TEXT NOT NULL,
            order_id TEXT UNIQUE,
            side TEXT NOT NULL,
            qty REAL,
            notional REAL,
            filled_price REAL,
            submitted_at TIMESTAMP,
            filled_at TIMESTAMP,
            status TEXT,
            order_type TEXT,
            strategy TEXT,
            stop_loss REAL,
            take_profit REAL,
            time_in_force TEXT,
            FOREIGN KEY (symbol) REFERENCES assets(symbol)
        )
    """)

    # Create indexes for performance
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_positions_account_strategy 
        ON positions(account, strategy)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_transactions_symbol_account 
        ON transactions(symbol, account)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_transactions_filled_at 
        ON transactions(filled_at)
    """)

    conn.commit()
    conn.close()
    print(f"Database schema initialized at {db_path}")


def initialize_database(repo_root: Optional[Path] = None) -> Path:
    """Initialize the database with schema if it doesn't exist."""
    db_path = get_database_path(repo_root)
    create_schema(db_path)
    return db_path


if __name__ == "__main__":
    # Initialize database when run directly
    db_path = initialize_database()
    print(f"Portfolio database ready at: {db_path}")
