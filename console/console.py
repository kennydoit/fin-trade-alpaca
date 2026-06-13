"""Portfolio Management Console - Simple menu interface for common operations.

Run this to access a menu of frequently used portfolio management tasks.

Usage:
    python console/console.py
    
Or from console directory:
    cd console
    python console.py
"""
import subprocess
import sys
from pathlib import Path


def get_repo_root():
    """Get repository root directory."""
    return Path(__file__).resolve().parents[1]


def print_header():
    """Print console header."""
    print("\n" + "=" * 60)
    print("    PORTFOLIO MANAGEMENT CONSOLE")
    print("=" * 60)


def print_menu():
    """Print main menu options."""
    print("\nAvailable Operations:")
    print("  1. Sync Portfolio Database (Alpaca -> DB)")
    print("  2. Update Asset Classifications (Strategy Files -> DB)")
    print("  3. Run Prediction Screener")
    print("  4. Run Growth Screener")
    print("  5. View Portfolio (SQLite Viewer)")
    print("  6. Query Database (Interactive SQL)")
    print("  0. Exit")
    print()


def run_command(cmd, description):
    """Run a command and return success status."""
    print(f"\n{'='*60}")
    print(f"Running: {description}")
    print(f"{'='*60}\n")
    
    repo_root = get_repo_root()
    
    try:
        result = subprocess.run(cmd, cwd=repo_root, check=True)
        print(f"\n[SUCCESS] {description} completed successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"\n[FAILED] {description} failed with exit code {e.returncode}")
        return False
    except FileNotFoundError:
        print(f"\n[ERROR] Command not found: {cmd[0]}")
        return False


def sync_portfolio_db():
    """Sync portfolio database from Alpaca."""
    print("\nSync Options:")
    print("  1. Paper account")
    print("  2. Live account")
    print("  3. Both accounts")
    
    choice = input("\nSelect account (1-3): ").strip()
    
    account_map = {"1": "paper", "2": "live", "3": "both"}
    account = account_map.get(choice)
    
    if not account:
        print("[ERROR] Invalid choice")
        return
    
    backfill = input("Backfill transactions? (y/n, default=n): ").strip().lower()
    
    cmd = [sys.executable, "tools/sync_portfolio_db.py", account]
    if backfill != "y":
        cmd.append("--no-backfill")
    
    run_command(cmd, f"Sync {account} account")


def update_asset_class():
    """Update asset classifications from strategy files."""
    print("\nThis will scan all strategy*.json files in configs/")
    print("and update position strategies in the database.")
    
    confirm = input("\nProceed? (y/n): ").strip().lower()
    if confirm != "y":
        print("[CANCELLED]")
        return
    
    run_command(
        [sys.executable, "tools/update_asset_class.py"],
        "Update asset classifications"
    )


def run_prediction_screener():
    """Run prediction screener."""
    print("\nPrediction Screener Options:")
    print("  This will generate predictions with strategy attribution metadata")
    
    use_simple = input("\nUse simple mode (latest screener, limit 100)? (y/n): ").strip().lower()
    
    if use_simple == "y":
        cmd = [sys.executable, "src/runners/predict_screener.py", "--limit", "100"]
    else:
        limit = input("Enter limit (default 200): ").strip() or "200"
        sector = input("Filter by sector (leave empty for all): ").strip()
        
        cmd = [sys.executable, "src/runners/predict_screener.py", "--limit", limit]
        if sector:
            cmd.extend(["--sector", sector])
    
    run_command(cmd, "Prediction screener")


def run_growth_screener():
    """Run growth screener."""
    print("\nGrowth Screener - generates ranked candidates CSV")
    print("Output: reports/screener_results/")
    
    confirm = input("\nProceed? (y/n): ").strip().lower()
    if confirm != "y":
        print("[CANCELLED]")
        return
    
    run_command(
        [sys.executable, "src/runners/yfinance_growth_screener.py"],
        "Growth screener"
    )


def view_portfolio():
    """Open database in SQLite viewer."""
    repo_root = get_repo_root()
    db_path = repo_root / "reports" / "portfolio_db" / "portfolio.db"
    
    if not db_path.exists():
        print(f"\n[ERROR] Database not found: {db_path}")
        print("Run 'Sync Portfolio Database' first (Option 1)")
        return
    
    print(f"\nDatabase location: {db_path}")
    print("\nTo view in VS Code:")
    print("  1. Install 'SQLite Viewer' extension (qwtel.sqlite-viewer)")
    print("  2. Open Explorer (Ctrl+Shift+E)")
    print("  3. Navigate to: reports/portfolio_db/portfolio.db")
    print("  4. Click the file to open in table view")
    print("\nAlternatively, use DBeaver or any SQLite client.")


def query_database():
    """Run interactive SQL query tool."""
    repo_root = get_repo_root()
    db_path = repo_root / "reports" / "portfolio_db" / "portfolio.db"
    
    if not db_path.exists():
        print(f"\n[ERROR] Database not found: {db_path}")
        print("Run 'Sync Portfolio Database' first (Option 1)")
        return
    
    print("\nLaunching interactive SQL query tool...")
    print("Commands: .tables, .schema, .exit")
    print()
    
    run_command(
        [sys.executable, "tools/query_db.py"],
        "Interactive SQL query"
    )


def main():
    """Main console loop."""
    print_header()
    
    while True:
        print_menu()
        choice = input("Select option (0-6): ").strip()
        
        if choice == "0":
            print("\nExiting console. Goodbye!")
            break
        elif choice == "1":
            sync_portfolio_db()
        elif choice == "2":
            update_asset_class()
        elif choice == "3":
            run_prediction_screener()
        elif choice == "4":
            run_growth_screener()
        elif choice == "5":
            view_portfolio()
        elif choice == "6":
            query_database()
        else:
            print(f"\n[ERROR] Invalid option: {choice}")
        
        input("\nPress Enter to continue...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Exiting...")
        sys.exit(0)
