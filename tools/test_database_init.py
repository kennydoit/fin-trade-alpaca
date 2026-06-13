"""Quick test script to verify database initialization and basic queries."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database.schema import initialize_database, get_database_path
import sqlite3


def main():
    print("Testing database initialization...")
    
    # Initialize database
    db_path = initialize_database()
    print(f"✓ Database created at: {db_path}")
    
    # Verify tables exist
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='table' 
        ORDER BY name
    """)
    tables = [row[0] for row in cursor.fetchall()]
    
    print(f"\n✓ Tables created: {', '.join(tables)}")
    
    expected_tables = {'assets', 'positions', 'transactions', 'strategy_history'}
    if not expected_tables.issubset(set(tables)):
        print(f"✗ Missing tables: {expected_tables - set(tables)}")
        return 1
    
    # Verify indexes exist
    cursor.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='index' 
        ORDER BY name
    """)
    indexes = [row[0] for row in cursor.fetchall()]
    print(f"\n✓ Indexes created: {len(indexes)} total")
    
    # Check schema for positions table
    cursor.execute("PRAGMA table_info(positions)")
    columns = [(row[1], row[2]) for row in cursor.fetchall()]
    print(f"\n✓ Positions table has {len(columns)} columns")
    
    conn.close()
    
    print("\n" + "="*60)
    print("Database initialization test PASSED")
    print("="*60)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
