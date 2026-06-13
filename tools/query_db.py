"""Quick database query tool for exploring the portfolio database.

Usage:
    python tools/query_db.py
    
Then enter SQL queries interactively, or pass SQL as argument:
    python tools/query_db.py "SELECT * FROM positions WHERE account='paper'"
"""
import sqlite3
import sys
from pathlib import Path
from tabulate import tabulate

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from database.schema import get_database_path


def run_query(db_path: Path, sql: str):
    """Execute SQL query and display results in a table."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        cursor.execute(sql)
        
        # Check if it's a SELECT query
        if sql.strip().upper().startswith('SELECT'):
            rows = cursor.fetchall()
            if rows:
                # Get column names
                columns = [desc[0] for desc in cursor.description]
                print(tabulate(rows, headers=columns, tablefmt='grid'))
                print(f"\n{len(rows)} rows returned")
            else:
                print("No rows returned")
        else:
            conn.commit()
            print(f"Query executed successfully. Rows affected: {cursor.rowcount}")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        conn.close()


def interactive_mode(db_path: Path):
    """Interactive SQL query mode."""
    print("Portfolio Database Query Tool")
    print("=" * 60)
    print(f"Database: {db_path}")
    print("\nCommands:")
    print("  .tables    - List all tables")
    print("  .schema    - Show schema for all tables")
    print("  .exit      - Exit")
    print("\nOr enter any SQL query")
    print("=" * 60)
    print()
    
    while True:
        try:
            query = input("SQL> ").strip()
            
            if not query:
                continue
            
            if query.lower() == '.exit':
                break
            elif query.lower() == '.tables':
                run_query(db_path, "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            elif query.lower() == '.schema':
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
                tables = [row[0] for row in cursor.fetchall()]
                for table in tables:
                    print(f"\n{table}:")
                    cursor.execute(f"PRAGMA table_info({table})")
                    for col in cursor.fetchall():
                        print(f"  {col[1]:20s} {col[2]}")
                conn.close()
            else:
                run_query(db_path, query)
            
            print()
        except KeyboardInterrupt:
            print("\nExiting...")
            break
        except EOFError:
            break


def main():
    db_path = get_database_path()
    
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        print("Run: python tools/sync_portfolio_db.py paper")
        return 1
    
    # Check if SQL query provided as argument
    if len(sys.argv) > 1:
        sql = ' '.join(sys.argv[1:])
        run_query(db_path, sql)
    else:
        interactive_mode(db_path)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
