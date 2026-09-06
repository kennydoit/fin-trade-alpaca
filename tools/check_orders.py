from alpaca.trading.client import TradingClient
import os
from dotenv import load_dotenv

load_dotenv('.env.paper')
client = TradingClient(
    os.environ['ALPACA_PAPER_API_KEY'], 
    os.environ['ALPACA_PAPER_API_SECRET'], 
    paper=True
)

orders = client.get_orders()
print(f'\nTotal orders: {len(orders)}\n')
print('Recent orders:')
for o in orders[:10]:
    filled_price = o.filled_avg_price if o.filled_avg_price else 'pending'
    print(f'{o.symbol}: {o.side} {o.qty} @ ${filled_price} - Status: {o.status}')
