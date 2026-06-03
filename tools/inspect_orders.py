from pathlib import Path
import sys
import json
from datetime import datetime
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from fin_trade_alpaca.optimize_and_buy import resolve_credentials
from fin_trade_alpaca.env_loader import load_environment_for_mode
from alpaca.trading.client import TradingClient

# Order IDs to inspect (from recent run)
ORDER_IDS = [
    "50e0693f-a5cf-4a26-a6fd-4e1ebc40b572",
    "8c9a508c-3ddf-42a7-a97e-556f1430ba25",
    "9ba33985-9ba2-41db-965d-1eb3fabfaf27",
]

# normalize to UUID objects since SDK may use UUID typed ids
ORDER_UUIDS = [uuid.UUID(s) for s in ORDER_IDS]


def fmt(o, attr):
    v = getattr(o, attr, None)
    if v is None:
        return None
    try:
        return str(v)
    except Exception:
        return repr(v)


def main():
    load_environment_for_mode('live')
    creds = resolve_credentials('live')
    client = TradingClient(api_key=creds.api_key, secret_key=creds.api_secret, oauth_token=creds.oauth_token, paper=creds.paper)

    results = {}
    try:
        all_orders = client.get_orders()
    except Exception:
        try:
            all_orders = client.get_all_orders()
        except Exception as exc:
            print('Unable to list orders:', exc)
            all_orders = []

    orders_by_id = {getattr(o, 'id', None): o for o in all_orders}

    for oid, ou in zip(ORDER_IDS, ORDER_UUIDS):
        o = orders_by_id.get(ou)
        if o is None:
            results[oid] = {"error": "order not found"}
            continue

        info = {
            "id": fmt(o, 'id'),
            "symbol": fmt(o, 'symbol'),
            "side": fmt(o, 'side'),
            "type": fmt(o, 'type'),
            "status": fmt(o, 'status'),
            "submitted_at": fmt(o, 'submitted_at'),
            "filled_at": fmt(o, 'filled_at'),
            "filled_qty": fmt(o, 'filled_qty'),
            "quantity": fmt(o, 'qty') if hasattr(o, 'qty') else fmt(o, 'quantity'),
            "notional": fmt(o, 'notional'),
            "filled_avg_price": fmt(o, 'filled_avg_price'),
            "last_fill_price": fmt(o, 'last_fill_price'),
            "client_order_id": fmt(o, 'client_order_id'),
            "raw": None,
        }
        # include raw __dict__ when available for debugging
        try:
            info['raw'] = {k: str(v) for k, v in o.__dict__.items()}
        except Exception:
            info['raw'] = None

        results[oid] = info

    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
