from __future__ import annotations

from fin_trade_alpaca.env_loader import load_environment_for_mode
from fin_trade_alpaca.optimize_and_buy import resolve_credentials
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest


def main() -> int:
    load_environment_for_mode('paper')
    creds = resolve_credentials('paper')
    client = TradingClient(
        api_key=creds.api_key,
        secret_key=creds.api_secret,
        oauth_token=creds.oauth_token,
        paper=creds.paper,
    )

    positions = client.get_all_positions()
    if not positions:
        print('No paper positions to liquidate.')
        return 0

    print(f'Found {len(positions)} paper position(s) to liquidate:')
    for p in positions:
        sym = str(getattr(p, 'symbol', '')).strip().upper()
        qty = float(getattr(p, 'qty', 0) or 0)
        print(f'  - {sym}: qty={qty}')

        try:
            req = MarketOrderRequest(
                symbol=sym,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
            )
            order = client.submit_order(req)
            print(f'    submitted sell order id={getattr(order, "id", "<unknown>")}')
        except Exception as exc:
            print(f'    failed to submit sell for {sym}: {exc!r}')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
