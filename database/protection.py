"""Portfolio protection logic for preventing liquidation of core and growth positions.

This module provides APIs for checking whether positions can be liquidated or
whether symbols are available for short-term trading.
"""
import sqlite3
from pathlib import Path
from typing import List, Dict, Set, Optional

from database.schema import get_database_path


PROTECTED_STRATEGIES = {"core", "growth"}
TRADEABLE_STRATEGIES = {"short_term"}


def is_protected(symbol: str, account: str, db_path: Optional[Path] = None) -> bool:
    """Check if a position is protected from liquidation.
    
    Args:
        symbol: Stock symbol (will be uppercased)
        account: 'paper' or 'live'
        db_path: Optional path to database (uses default if None)
    
    Returns:
        True if position is in 'core' or 'growth' strategy, False otherwise
    """
    if db_path is None:
        db_path = get_database_path()
    
    if not db_path.exists():
        # Database doesn't exist yet - no protection by default
        return False
    
    symbol_upper = symbol.strip().upper()
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT strategy FROM positions
        WHERE symbol = ? AND account = ?
    """, (symbol_upper, account))
    
    result = cursor.fetchone()
    conn.close()
    
    if result:
        strategy = result[0]
        return strategy in PROTECTED_STRATEGIES
    
    return False


def get_protected_symbols(account: str, db_path: Optional[Path] = None) -> Set[str]:
    """Get all protected symbols for an account.
    
    Args:
        account: 'paper' or 'live'
        db_path: Optional path to database (uses default if None)
    
    Returns:
        Set of symbol strings that are protected
    """
    if db_path is None:
        db_path = get_database_path()
    
    if not db_path.exists():
        return set()
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT symbol FROM positions
        WHERE account = ? AND strategy IN ('core', 'growth')
    """, (account,))
    
    symbols = {row[0] for row in cursor.fetchall()}
    conn.close()
    
    return symbols


def filter_protected_symbols(symbols: List[str], account: str, 
                             db_path: Optional[Path] = None) -> List[str]:
    """Filter out protected symbols from a list.
    
    Args:
        symbols: List of symbols to filter
        account: 'paper' or 'live'
        db_path: Optional path to database (uses default if None)
    
    Returns:
        List of symbols that are NOT protected (safe to trade)
    """
    protected = get_protected_symbols(account, db_path)
    return [s for s in symbols if s.strip().upper() not in protected]


def get_positions_by_strategy(account: str, db_path: Optional[Path] = None) -> Dict[str, List[str]]:
    """Get positions grouped by strategy.
    
    Args:
        account: 'paper' or 'live'
        db_path: Optional path to database (uses default if None)
    
    Returns:
        Dict mapping strategy name to list of symbols
    """
    if db_path is None:
        db_path = get_database_path()
    
    if not db_path.exists():
        return {}
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT strategy, symbol FROM positions
        WHERE account = ?
        ORDER BY strategy, symbol
    """, (account,))
    
    result = {}
    for strategy, symbol in cursor.fetchall():
        if strategy not in result:
            result[strategy] = []
        result[strategy].append(symbol)
    
    conn.close()
    return result


def check_symbols_available_for_short_term(symbols: List[str], account: str,
                                           db_path: Optional[Path] = None) -> Dict[str, bool]:
    """Check which symbols are available for short-term trading.
    
    Symbols in core or growth strategies are NOT available.
    
    Args:
        symbols: List of symbols to check
        account: 'paper' or 'live'
        db_path: Optional path to database (uses default if None)
    
    Returns:
        Dict mapping symbol to boolean (True if available, False if protected)
    """
    protected = get_protected_symbols(account, db_path)
    return {s: s.strip().upper() not in protected for s in symbols}


if __name__ == "__main__":
    # Quick test
    print("Protection module loaded")
    print(f"Protected strategies: {PROTECTED_STRATEGIES}")
    print(f"Tradeable strategies: {TRADEABLE_STRATEGIES}")
