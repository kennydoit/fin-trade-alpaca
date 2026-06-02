from pathlib import Path
import sys
from decimal import Decimal

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from fin_trade_alpaca.optimize_and_buy import load_environment_for_mode, resolve_credentials
from alpaca.trading.client import TradingClient

def main():
    load_environment_for_mode('live')
    live_creds = resolve_credentials('live')
    live_client = TradingClient(api_key=live_creds.api_key, secret_key=live_creds.api_secret, oauth_token=live_creds.oauth_token, paper=live_creds.paper)

    load_environment_for_mode('paper')
    paper_creds = resolve_credentials('paper')
    paper_client = TradingClient(api_key=paper_creds.api_key, secret_key=paper_creds.api_secret, oauth_token=paper_creds.oauth_token, paper=paper_creds.paper)

    live_positions = {p.symbol.strip().upper(): p for p in live_client.get_all_positions()}
    paper_positions = {p.symbol.strip().upper(): p for p in paper_client.get_all_positions()}
    symbols = sorted(set(list(live_positions.keys()) + list(paper_positions.keys())))

    now = __import__('datetime').datetime.now()
    ts_colon = now.strftime('%Y%m%d:%H%M')
    ts_safe = ts_colon.replace(':', '-')
    filename = f'clone report - {ts_safe}.txt'
    reports_dir = Path('reports')
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = reports_dir / filename

    total_live = Decimal('0')
    total_paper = Decimal('0')

    with out_path.open('w', encoding='utf-8') as f:
        f.write(f"Clone report generated: {ts_colon}\n")
        f.write('\n')
        f.write('symbol | live_qty | live_value | paper_qty | paper_value\n')
        f.write('-----------------------------------------------------\n')
        for s in symbols:
            lp = live_positions.get(s)
            pp = paper_positions.get(s)
            lq = getattr(lp, 'qty', 0) if lp else 0
            lv = Decimal(str(getattr(lp, 'market_value', 0) if lp else 0))
            pq = getattr(pp, 'qty', 0) if pp else 0
            pv = Decimal(str(getattr(pp, 'market_value', 0) if pp else 0))
            total_live += lv
            total_paper += pv
            f.write(f"{s} | {lq} | ${lv.quantize(Decimal('0.01'))} | {pq} | ${pv.quantize(Decimal('0.01'))}\n")

        f.write('\n')
        f.write(f"Total live value: ${total_live.quantize(Decimal('0.01'))}\n")
        f.write(f"Total paper value: ${total_paper.quantize(Decimal('0.01'))}\n")

    print(f'Wrote side-by-side report to {out_path}')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
