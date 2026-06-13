"""Migration script to add strategy attribution columns to existing databases.

Run this once to upgrade an existing portfolio.db with new strategy tracking fields.
Safe to run multiple times - will skip columns that already exist.
"""
import sqlite3
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database.schema import get_database_path


def column_exists(cursor: sqlite3.Cursor, table: str, column: str) -> bool:
    """Check if a column exists in a table."""
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in cursor.fetchall()]
    return column in columns


def migrate_positions_table(db_path: Path) -> None:
    """Add strategy attribution columns to positions table."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Columns to add
    new_columns = [
        ("strategy_source", "TEXT"),
        ("model_type", "TEXT"),
        ("predicted_return", "REAL"),
        ("screener_rank", "INTEGER"),
        ("entry_notes", "TEXT"),
    ]
    
    print(f"Migrating database: {db_path}")
    print("Adding strategy attribution columns to positions table...")
    
    for col_name, col_type in new_columns:
        if column_exists(cursor, "positions", col_name):
            print(f"  ✓ Column '{col_name}' already exists, skipping")
        else:
            try:
                cursor.execute(f"ALTER TABLE positions ADD COLUMN {col_name} {col_type}")
                print(f"  + Added column '{col_name}' ({col_type})")
            except sqlite3.OperationalError as e:
                print(f"  ✗ Failed to add column '{col_name}': {e}")
    
    conn.commit()
    conn.close()
    print("\nMigration complete!")


def main():
    """Run the migration."""
    db_path = get_database_path()
    
    if not db_path.exists():
        print(f"Database not found at {db_path}")
        print("Run sync_portfolio_db.py first to create the database.")
        return
    
    migrate_positions_table(db_path)
    
    print(f"\nDatabase ready with strategy attribution tracking at:")
    print(f"  {db_path}")


if __name__ == "__main__":
    main()
