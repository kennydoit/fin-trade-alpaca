"""Portfolio Management Console - Simple menu interface for common operations.

Run this to access a menu of frequently used portfolio management tasks.

Usage:
    python console/console.py
    
Or from console directory:
    cd console
    python console.py
"""
import os
import re
import subprocess
import sys
from pathlib import Path
import pandas as pd


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
    print("  3. Run Growth Screener (Equity)")
    print("  4. Run Prediction Screener")
    print("  5. View Portfolio (SQLite Viewer)")
    print("  6. Query Database (Interactive SQL)")
    print("  0. Exit")
    print()


def run_command(cmd, description, capture_output=False):
    """Run a command and return success status.
    
    Args:
        cmd: Command list to execute
        description: Human-readable description
        capture_output: If True, return (success, stdout, stderr) instead of just success
    
    Returns:
        bool (if capture_output=False) or tuple of (bool, str, str)
    """
    print(f"\n{'='*60}")
    print(f"Running: {description}")
    print(f"{'='*60}\n")
    
    repo_root = get_repo_root()
    
    # Set PYTHONPATH for proper imports
    env = os.environ.copy()
    env['PYTHONPATH'] = str(repo_root / 'src')
    
    try:
        if capture_output:
            result = subprocess.run(
                cmd, 
                cwd=repo_root, 
                check=True, 
                capture_output=True, 
                text=True,
                env=env
            )
            print(result.stdout)
            if result.stderr:
                print(result.stderr, file=sys.stderr)
            print(f"\n[SUCCESS] {description} completed successfully")
            return True, result.stdout, result.stderr
        else:
            result = subprocess.run(cmd, cwd=repo_root, check=True, env=env)
            print(f"\n[SUCCESS] {description} completed successfully")
            return True
    except subprocess.CalledProcessError as e:
        print(f"\n[FAILED] {description} failed with exit code {e.returncode}")
        if capture_output:
            if hasattr(e, 'stdout') and e.stdout:
                print(e.stdout)
            if hasattr(e, 'stderr') and e.stderr:
                print(e.stderr, file=sys.stderr)
            return False, getattr(e, 'stdout', ''), getattr(e, 'stderr', '')
        return False
    except FileNotFoundError:
        print(f"\n[ERROR] Command not found: {cmd[0]}")
        if capture_output:
            return False, '', f"Command not found: {cmd[0]}"
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


def display_prediction_report(stdout, repo_root):
    """Parse prediction screener output and display model fit and top 10 assets.
    
    Args:
        stdout: Captured stdout from prediction screener
        repo_root: Repository root path
    """
    print("\n" + "="*60)
    print("    PREDICTION SCREENER REPORT")
    print("="*60)
    
    # Extract model metrics from output
    metrics = {}
    for line in stdout.split('\n'):
        # Look for evaluation metrics line
        # Example: "Eval (return_days=5): Spearman IC=0.1234, R2=0.5678, MAE=0.012345"
        if 'Eval (return_days=' in line and 'Spearman IC=' in line:
            try:
                return_days_match = re.search(r'return_days=(\d+)', line)
                ic_match = re.search(r'Spearman IC=([-\d.]+)', line)
                r2_match = re.search(r'R2=([-\d.]+)', line)
                mae_match = re.search(r'MAE=([-\d.]+)', line)
                
                if return_days_match:
                    metrics['return_days'] = int(return_days_match.group(1))
                if ic_match:
                    metrics['spearman_ic'] = float(ic_match.group(1))
                if r2_match:
                    metrics['r2_score'] = float(r2_match.group(1))
                if mae_match:
                    metrics['mae'] = float(mae_match.group(1))
            except Exception:
                pass
        
        # Extract model type
        if 'Training' in line and 'with' in line and 'features' in line:
            if 'lightgbm' in line.lower():
                metrics['model_type'] = 'LightGBM'
            elif 'random_forest' in line.lower():
                metrics['model_type'] = 'Random Forest'
            elif 'ridge' in line.lower():
                metrics['model_type'] = 'Ridge'
            elif 'elasticnet' in line.lower():
                metrics['model_type'] = 'ElasticNet'
    
    # Display model fit metrics
    if metrics:
        print("\n📊 MODEL FIT METRICS:")
        print("-" * 60)
        if 'model_type' in metrics:
            print(f"  Model Type:        {metrics['model_type']}")
        if 'return_days' in metrics:
            print(f"  Return Horizon:    {metrics['return_days']} days")
        if 'spearman_ic' in metrics:
            ic_rating = "Excellent" if metrics['spearman_ic'] > 0.05 else "Good" if metrics['spearman_ic'] > 0.02 else "Fair"
            print(f"  Spearman IC:       {metrics['spearman_ic']:.4f} ({ic_rating})")
        if 'r2_score' in metrics:
            print(f"  R² Score:          {metrics['r2_score']:.4f}")
        if 'mae' in metrics:
            print(f"  Mean Abs Error:    {metrics['mae']:.6f}")
    else:
        print("\n⚠️  Could not extract model metrics from output")
    
    # Find and read the latest predictions CSV
    predictions_file = None
    for line in stdout.split('\n'):
        if 'Wrote predictions to' in line:
            match = re.search(r'Wrote predictions to (.+)', line)
            if match:
                predictions_file = Path(match.group(1).strip())
                break
    
    if not predictions_file:
        # Try to find latest predictions file
        reports = repo_root / 'reports' / 'screener_results'
        if reports.exists():
            pred_files = sorted(reports.glob('predictions_*.csv'))
            if pred_files:
                predictions_file = pred_files[-1]
    
    # Display top 10 assets
    if predictions_file and predictions_file.exists():
        try:
            df = pd.read_csv(predictions_file)
            print("\n🏆 TOP 10 PREDICTED ASSETS:")
            print("-" * 60)
            
            # Select relevant columns for display
            display_cols = ['symbol', 'pred_ret', 'sector']
            if 'screener_rank' in df.columns:
                display_cols.append('screener_rank')
            if 'close' in df.columns:
                display_cols.append('close')
            
            available_cols = [c for c in display_cols if c in df.columns]
            top10 = df.head(10)[available_cols].copy()
            
            # Format prediction_rank
            top10.insert(0, 'rank', range(1, len(top10) + 1))
            
            # Format pred_ret as percentage
            if 'pred_ret' in top10.columns:
                top10['pred_ret'] = top10['pred_ret'].apply(lambda x: f"{x*100:+.2f}%")
            
            # Format close price
            if 'close' in top10.columns:
                top10['close'] = top10['close'].apply(lambda x: f"${x:.2f}")
            
            # Rename columns for display
            rename_map = {
                'rank': 'Rank',
                'symbol': 'Symbol',
                'pred_ret': 'Predicted Return',
                'sector': 'Sector',
                'screener_rank': 'Screener Rank',
                'close': 'Price'
            }
            top10 = top10.rename(columns={k: v for k, v in rename_map.items() if k in top10.columns})
            
            print(top10.to_string(index=False))
            print(f"\n📁 Full results: {predictions_file}")
            print(f"   Total symbols analyzed: {len(df)}")
        except Exception as e:
            print(f"\n⚠️  Could not read predictions file: {e}")
    else:
        print("\n⚠️  Predictions file not found")
    
    print("\n" + "="*60)


def run_prediction_screener():
    """Run prediction screener."""
    print("\nPrediction Screener Options:")
    print("  This will generate predictions with strategy attribution metadata")
    print("  Note: If initial lookback fails, will automatically retry with shorter periods")
    
    use_simple = input("\nUse simple mode (latest screener, limit 100)? (y/n): ").strip().lower()
    
    if use_simple == "y":
        cmd = [sys.executable, "src/runners/predict_screener.py", "--limit", "100", "--lookback", "180"]
    else:
        limit = input("Enter limit (default 200): ").strip() or "200"
        sector = input("Filter by sector (leave empty for all): ").strip()
        lookback = input("Lookback days (default 180, max 365): ").strip() or "180"
        
        cmd = [sys.executable, "src/runners/predict_screener.py", "--limit", limit, "--lookback", lookback]
        if sector:
            cmd.extend(["--sector", sector])
    
    success, stdout, stderr = run_command(cmd, "Prediction screener", capture_output=True)
    
    if success:
        repo_root = get_repo_root()
        display_prediction_report(stdout, repo_root)


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
            run_growth_screener()
        elif choice == "4":
            run_prediction_screener()
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
