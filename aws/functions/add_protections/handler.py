"""
Lambda handler for adding position protections.
Adds stop-loss and take-profit orders to filled positions.
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path
import boto3

# Add source code to path
sys.path.insert(0, '/opt/python')  # Lambda layer
sys.path.insert(0, str(Path(__file__).parent / 'src'))


def get_alpaca_credentials(secrets_client, secret_name):
    """Retrieve Alpaca API credentials from Secrets Manager."""
    response = secrets_client.get_secret_value(SecretId=secret_name)
    secret = json.loads(response['SecretString'])
    return secret['key'], secret['secret']


def lambda_handler(event, context):
    """
    Lambda handler for adding position protections.
    
    Event structure:
    {
        "mode": "paper",  # or "live"
        "symbols": ["AAPL", "MSFT"],  # optional: specific symbols only
        "stop_pct": -2.0,  # optional: override stop loss %
        "take_pct": 5.0,   # optional: override take profit %
        "dry_run": false
    }
    """
    # Initialize AWS clients
    secrets_client = boto3.client('secretsmanager')
    sns_client = boto3.client('sns')
    
    bucket_name = os.environ['S3_BUCKET']
    alert_topic = os.environ['SNS_ALERT_TOPIC']
    status_topic = os.environ['SNS_STATUS_TOPIC']
    
    try:
        # Extract parameters from event
        mode = event.get('mode', 'paper')
        symbols = event.get('symbols', [])
        stop_pct = event.get('stop_pct', -2.0)
        take_pct = event.get('take_pct', 5.0)
        dry_run = event.get('dry_run', False)
        
        print(f"Adding protections: mode={mode}, symbols={symbols}, "
              f"stop_pct={stop_pct}, take_pct={take_pct}, dry_run={dry_run}")
        
        # Get Alpaca credentials
        secret_name = (os.environ['ALPACA_LIVE_SECRET_NAME'] if mode == 'live' 
                      else os.environ['ALPACA_PAPER_SECRET_NAME'])
        api_key, api_secret = get_alpaca_credentials(secrets_client, secret_name)
        
        # Import after setting credentials
        from alpaca.trading.client import TradingClient
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestQuoteRequest
        from alpaca.trading.requests import StopLossRequest, TakeProfitRequest, LimitOrderRequest, StopOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        from decimal import Decimal
        
        # Initialize Alpaca clients
        trading_client = TradingClient(api_key, api_secret, paper=(mode == 'paper'))
        data_client = StockHistoricalDataClient(api_key, api_secret)
        
        # Get current positions
        positions = trading_client.get_all_positions()
        
        if symbols:
            positions = [p for p in positions if p.symbol in symbols]
        
        if not positions:
            print(f"No positions found{' for symbols: ' + str(symbols) if symbols else ''}")
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'No positions to protect',
                    'mode': mode
                })
            }
        
        print(f"Processing {len(positions)} positions for protections")
        
        protections_added = []
        errors = []
        
        for position in positions:
            symbol = position.symbol
            qty = float(position.qty)
            
            try:
                # Get current bid price
                quote_request = StockLatestQuoteRequest(symbol_or_symbols=symbol)
                quotes = data_client.get_stock_latest_quote(quote_request)
                current_price = float(quotes[symbol].bid_price)
                
                # Calculate stop and take profit prices
                stop_price = round(current_price * (1 + stop_pct / 100), 2)
                take_price = round(current_price * (1 + take_pct / 100), 2)
                
                print(f"{symbol}: current=${current_price:.2f}, stop=${stop_price:.2f}, take=${take_price:.2f}")
                
                if not dry_run:
                    # Submit stop loss order
                    from alpaca.trading.requests import MarketOrderRequest
                    stop_order = trading_client.submit_order(
                        MarketOrderRequest(
                            symbol=symbol,
                            qty=qty,
                            side=OrderSide.SELL,
                            type='stop',
                            time_in_force=TimeInForce.GTC,
                            stop_price=stop_price
                        )
                    )
                    
                    # Submit take profit order
                    take_order = trading_client.submit_order(
                        MarketOrderRequest(
                            symbol=symbol,
                            qty=qty,
                            side=OrderSide.SELL,
                            type='limit',
                            time_in_force=TimeInForce.GTC,
                            limit_price=take_price
                        )
                    )
                    
                    protections_added.append({
                        'symbol': symbol,
                        'qty': qty,
                        'current_price': current_price,
                        'stop_price': stop_price,
                        'take_price': take_price,
                        'stop_order_id': stop_order.id,
                        'take_order_id': take_order.id
                    })
                else:
                    protections_added.append({
                        'symbol': symbol,
                        'qty': qty,
                        'current_price': current_price,
                        'stop_price': stop_price,
                        'take_price': take_price,
                        'dry_run': True
                    })
                
            except Exception as e:
                error_msg = f"{symbol}: {str(e)}"
                print(f"Error adding protections for {symbol}: {e}")
                errors.append(error_msg)
        
        # Send notification
        message = f"""Position Protections {'(DRY RUN)' if dry_run else 'Added'}

Mode: {mode.upper()}
Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Positions Protected: {len(protections_added)}
Errors: {len(errors)}

Stop Loss: {stop_pct}%
Take Profit: {take_pct}%

Protected Positions:
"""
        
        for p in protections_added:
            message += f"\n{p['symbol']}: ${p['current_price']:.2f} → Stop: ${p['stop_price']:.2f}, Take: ${p['take_price']:.2f}"
        
        if errors:
            message += f"\n\n❌ Errors:\n" + "\n".join(errors)
        
        sns_client.publish(
            TopicArn=status_topic if dry_run else alert_topic,
            Subject=f"{'🧪' if dry_run else '🛡️'} Protections Added ({mode.upper()}) - {len(protections_added)} positions",
            Message=message
        )
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': f'Protections {"simulated" if dry_run else "added"} for {len(protections_added)} positions',
                'mode': mode,
                'protected_count': len(protections_added),
                'error_count': len(errors),
                'dry_run': dry_run,
                'protections': protections_added,
                'errors': errors
            })
        }
        
    except Exception as e:
        error_msg = f"Add protections failed: {str(e)}"
        print(error_msg)
        
        # Send alert notification
        sns_client.publish(
            TopicArn=alert_topic,
            Subject=f'🚨 Add Protections Failed ({mode.upper()})',
            Message=f"{error_msg}\n\nFunction: {context.function_name}\nRequest ID: {context.aws_request_id}"
        )
        
        return {
            'statusCode': 500,
            'body': json.dumps({
                'message': error_msg,
                'mode': mode,
                'request_id': context.aws_request_id
            })
        }
