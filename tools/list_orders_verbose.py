from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from fin_trade_alpaca.optimize_and_buy import resolve_credentials
from fin_trade_alpaca.env_loader import load_environment_for_mode
from alpaca.trading.client import TradingClient


def main():
    load_environment_for_mode('live')
    creds = resolve_credentials('live')
    client = TradingClient(api_key=creds.api_key, secret_key=creds.api_secret, oauth_token=creds.oauth_token, paper=creds.paper)

    orders = []
    try:
        orders = client.get_orders()
    except Exception:
        try:
            orders = client.get_all_orders()
        except Exception:
            print('Unable to fetch orders via SDK.')
            return

    print(f'Found {len(orders)} orders (showing up to 50):')
    for o in orders[:50]:
        oid = getattr(o, 'id', None)
        oid_type = type(oid).__name__ if oid is not None else 'None'
        status = getattr(o, 'status', None)
        symbol = getattr(o, 'symbol', None)
        side = getattr(o, 'side', None)
        submitted = getattr(o, 'submitted_at', None)
        filled_qty = getattr(o, 'filled_qty', None)
        notional = getattr(o, 'notional', None)
        print(f'  id={oid} (type={oid_type}) symbol={symbol} side={side} status={status} submitted={submitted} filled_qty={filled_qty} notional={notional}')

if __name__ == '__main__':
    main()
