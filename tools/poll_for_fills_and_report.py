from pathlib import Path
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from fin_trade_alpaca.optimize_and_buy import load_environment_for_mode, resolve_credentials
from alpaca.trading.client import TradingClient


def get_open_live_order_ids(client):
    try:
        orders = client.get_orders()
    except Exception:
        try:
            orders = client.get_all_orders()
        except Exception:
            return []
    open_ids = []
    for o in orders:
        status = getattr(o, 'status', None)
        name = str(status).lower() if status is not None else ''
        if name not in ('filled', 'canceled', 'rejected', 'expired'):
            open_ids.append(getattr(o, 'id', None))
    return [i for i in open_ids if i]


def poll_for_fills(client, timeout_sec=120, poll_interval=3):
    start = time.time()
    end = start + timeout_sec
    open_ids = get_open_live_order_ids(client)
    if not open_ids:
        print('No open live orders to poll.')
        return True

    print(f'Polling for fills for {len(open_ids)} orders, timeout={timeout_sec}s')
    while time.time() < end:
        remaining = []
        for oid in open_ids:
            try:
                o = client.get_order(oid)
            except Exception:
                try:
                    # some SDKs use get_order_by_client_id/name; fall back to listing
                    all_orders = client.get_orders()
                    o = next((x for x in all_orders if getattr(x,'id',None)==oid), None)
                except Exception:
                    o = None
            if o is None:
                # consider it cleared
                continue
            status = getattr(o, 'status', None)
            name = status.name.lower() if hasattr(status, 'name') else str(status).lower()
            print(f'  order {oid} status={name}')
            if name in ('filled', 'canceled', 'rejected', 'expired'):
                continue
            remaining.append(oid)
        if not remaining:
            print('All orders are completed (filled/canceled/rejected).')
            return True
        open_ids = remaining
        time.sleep(poll_interval)

    print('Timeout reached; some orders remain open:', open_ids)
    return False


def main():
    load_environment_for_mode('live')
    creds = resolve_credentials('live')
    client = TradingClient(api_key=creds.api_key, secret_key=creds.api_secret, oauth_token=creds.oauth_token, paper=creds.paper)

    ok = poll_for_fills(client, timeout_sec=180, poll_interval=5)
    # run side-by-side report regardless
    import runpy
    print('Running side-by-side report...')
    runpy.run_path('tools/side_by_side_report.py', run_name='__main__')
    return 0 if ok else 2


if __name__ == '__main__':
    raise SystemExit(main())
