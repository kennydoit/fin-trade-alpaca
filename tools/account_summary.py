"""account_summary.py

Generate a detailed CSV report of all open positions for paper or live accounts.

Usage:
    python tools/account_summary.py paper
    python tools/account_summary.py live
"""
from pathlib import Path
import sys
import csv
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from runners.optimize_and_buy import resolve_credentials
from fin_trade_alpaca.env_loader import load_environment_for_mode
from alpaca.trading.client import TradingClient

# Use US Eastern Time (handles EST/EDT automatically)
EASTERN_TZ = ZoneInfo("America/New_York")


def main():
    if len(sys.argv) < 2 or sys.argv[1].lower() not in ['paper', 'live']:
        print('Usage: python tools/account_summary.py [paper|live]')
        return 1

    mode = sys.argv[1].lower()
    print(f'Fetching {mode} account positions...')

    # Load credentials and create client
    load_environment_for_mode(mode)
    creds = resolve_credentials(mode)
    client = TradingClient(
        api_key=creds.api_key,
        secret_key=creds.api_secret,
        oauth_token=creds.oauth_token,
        paper=creds.paper
    )

    # Fetch all positions
    try:
        positions = client.get_all_positions()
    except Exception as e:
        print(f'Error fetching positions: {e}')
        return 1

    if not positions:
        print(f'No open positions in {mode} account.')
        return 0

    # Prepare output directory and filename
    reports_dir = Path('reports') / 'account_summary'
    reports_dir.mkdir(parents=True, exist_ok=True)
    
    date_str = datetime.now(EASTERN_TZ).strftime('%Y%m%d')
    filename = f'{mode}_account_summary_{date_str}.csv'
    output_path = reports_dir / filename

    # Extract position data and write to CSV
    fieldnames = [
        'symbol',
        'qty',
        'side',
        'market_value',
        'avg_entry_price',
        'current_price',
        'unrealized_pl',
        'unrealized_plpc',
        'unrealized_intraday_pl',
        'unrealized_intraday_plpc',
        'cost_basis',
        'asset_id',
        'exchange',
        'asset_class',
        'asset_marginable',
    ]

    with output_path.open('w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        for p in positions:
            row = {}
            for field in fieldnames:
                value = getattr(p, field, None)
                # Convert to string for CSV
                if value is not None:
                    row[field] = str(value)
                else:
                    row[field] = ''
            writer.writerow(row)

    print(f'Wrote {len(positions)} positions to {output_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
