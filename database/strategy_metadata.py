"""Helper functions for setting strategy attribution metadata on positions.

Use these functions from your trading scripts to record why positions were opened.
"""
import sqlite3
from pathlib import Path
from typing import Optional
from database.schema import get_database_path


def set_position_strategy_metadata(
    symbol: str,
    account: str,
    strategy_source: Optional[str] = None,
    model_type: Optional[str] = None,
    predicted_return: Optional[float] = None,
    screener_rank: Optional[int] = None,
    entry_notes: Optional[str] = None,
    db_path: Optional[Path] = None
) -> bool:
    """Set strategy metadata for a position.
    
    Args:
        symbol: Stock symbol (e.g., 'AAPL')
        account: 'paper' or 'live'
        strategy_source: 'predictive_model', 'screener', 'manual', 'rebalance'
        model_type: 'xgboost', 'random_forest', etc. (if predictive_model)
        predicted_return: Expected return (e.g., 0.08 for 8%)
        screener_rank: Rank in screener results (1, 2, 3, etc.)
        entry_notes: Any additional notes about this position
        db_path: Override default database path
    
    Returns:
        True if update succeeded, False if position not found
    
    Example:
        # After opening a position from predictive model:
        set_position_strategy_metadata(
            symbol='AAPL',
            account='paper',
            strategy_source='predictive_model',
            model_type='xgboost',
            predicted_return=0.085,
            screener_rank=1,
            entry_notes='High momentum + earnings beat'
        )
    """
    if db_path is None:
        db_path = get_database_path()
    
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Check if position exists
    cursor.execute(
        "SELECT id FROM positions WHERE symbol = ? AND account = ?",
        (symbol.upper(), account)
    )
    
    if not cursor.fetchone():
        conn.close()
        return False
    
    # Update metadata fields (only non-None values)
    updates = []
    params = []
    
    if strategy_source is not None:
        updates.append("strategy_source = ?")
        params.append(strategy_source)
    
    if model_type is not None:
        updates.append("model_type = ?")
        params.append(model_type)
    
    if predicted_return is not None:
        updates.append("predicted_return = ?")
        params.append(predicted_return)
    
    if screener_rank is not None:
        updates.append("screener_rank = ?")
        params.append(screener_rank)
    
    if entry_notes is not None:
        updates.append("entry_notes = ?")
        params.append(entry_notes)
    
    if not updates:
        conn.close()
        return True  # Nothing to update
    
    # Build and execute update
    params.extend([symbol.upper(), account])
    sql = f"""
        UPDATE positions 
        SET {', '.join(updates)}, last_updated = CURRENT_TIMESTAMP
        WHERE symbol = ? AND account = ?
    """
    
    cursor.execute(sql, params)
    conn.commit()
    conn.close()
    
    return True


def bulk_set_strategy_metadata(
    positions: list[dict],
    db_path: Optional[Path] = None
) -> int:
    """Set strategy metadata for multiple positions in one transaction.
    
    Args:
        positions: List of dicts with keys: symbol, account, and metadata fields
        db_path: Override default database path
    
    Returns:
        Number of positions updated
    
    Example:
        bulk_set_strategy_metadata([
            {
                'symbol': 'AAPL',
                'account': 'paper',
                'strategy_source': 'predictive_model',
                'model_type': 'xgboost',
                'predicted_return': 0.085,
                'screener_rank': 1
            },
            {
                'symbol': 'MSFT',
                'account': 'paper',
                'strategy_source': 'predictive_model',
                'model_type': 'xgboost',
                'predicted_return': 0.073,
                'screener_rank': 2
            }
        ])
    """
    if db_path is None:
        db_path = get_database_path()
    
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    updated_count = 0
    
    for pos in positions:
        symbol = pos.get('symbol', '').upper()
        account = pos.get('account', '')
        
        if not symbol or not account:
            continue
        
        # Check if position exists
        cursor.execute(
            "SELECT id FROM positions WHERE symbol = ? AND account = ?",
            (symbol, account)
        )
        
        if not cursor.fetchone():
            continue
        
        # Build update
        updates = []
        params = []
        
        for field in ['strategy_source', 'model_type', 'predicted_return', 
                     'screener_rank', 'entry_notes']:
            if field in pos and pos[field] is not None:
                updates.append(f"{field} = ?")
                params.append(pos[field])
        
        if updates:
            params.extend([symbol, account])
            sql = f"""
                UPDATE positions 
                SET {', '.join(updates)}, last_updated = CURRENT_TIMESTAMP
                WHERE symbol = ? AND account = ?
            """
            cursor.execute(sql, params)
            updated_count += 1
    
    conn.commit()
    conn.close()
    
    return updated_count


def get_position_metadata(symbol: str, account: str, 
                         db_path: Optional[Path] = None) -> Optional[dict]:
    """Retrieve strategy metadata for a position.
    
    Returns:
        Dict with metadata fields or None if position not found
    """
    if db_path is None:
        db_path = get_database_path()
    
    if not db_path.exists():
        return None
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT strategy_source, model_type, predicted_return, 
               screener_rank, entry_notes
        FROM positions 
        WHERE symbol = ? AND account = ?
    """, (symbol.upper(), account))
    
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return None
    
    return {
        'strategy_source': row[0],
        'model_type': row[1],
        'predicted_return': row[2],
        'screener_rank': row[3],
        'entry_notes': row[4]
    }
