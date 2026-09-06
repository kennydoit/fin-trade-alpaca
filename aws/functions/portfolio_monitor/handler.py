"""
Lambda handler for portfolio monitoring workflow.
Monitors positions and executes sells based on:
1. Stop-loss levels
2. Take-profit levels  
3. ML predictions (predicted to drop)
4. Does nothing if none of the above
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path
import boto3
import sqlite3
from botocore.exceptions import ClientError

# Add source code to path
sys.path.insert(0, '/opt/python')  # Lambda layer
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce


def get_alpaca_credentials(secrets_client, secret_name):
    """Retrieve Alpaca API credentials from Secrets Manager."""
    response = secrets_client.get_secret_value(SecretId=secret_name)
    secret = json.loads(response['SecretString'])
    return secret['key'], secret['secret']


def get_position_protections(db_path, symbol, account):
    """Get stop-loss and take-profit levels from database."""
    if not os.path.exists(db_path):
        return None, None
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Get most recent transaction with protections
    cursor.execute("""
        SELECT stop_loss, take_profit 
        FROM transactions 
        WHERE symbol = ? AND account = ? AND status = 'filled'
        ORDER BY filled_at DESC 
        LIMIT 1
    """, (symbol, account))
    
    result = cursor.fetchone()
    conn.close()
    
    if result:
        return result[0], result[1]  # stop_loss, take_profit
    return None, None


def get_ml_prediction(symbol, lookback_days=60):
    """Run ML prediction for a single symbol.
    
    Returns predicted return (negative means predicted to drop).
    """
    try:
        # Import prediction logic
        from runners.predict_screener import predict_single_symbol
        
        # Run prediction
        prediction = predict_single_symbol(symbol, lookback_days=lookback_days)
        return prediction.get('predicted_return', 0.0) if prediction else 0.0
    except Exception as e:
        print(f"Failed to get ML prediction for {symbol}: {e}")
        return 0.0


def lambda_handler(event, context):
    """
    Lambda handler for portfolio monitoring and sell execution.
    
    Event structure:
    {
        "mode": "paper",  # or "live"
        "ml_sell_threshold": -0.05,  # Sell if predicted return < -5%
        "check_ml_predictions": true  # Whether to use ML predictions
    }
    """
    # Initialize AWS clients
    s3_client = boto3.client('s3')
    secrets_client = boto3.client('secretsmanager')
    sns_client = boto3.client('sns')
    
    bucket_name = os.environ['S3_BUCKET']
    alert_topic = os.environ['SNS_ALERT_TOPIC']
    status_topic = os.environ['SNS_STATUS_TOPIC']
    trading_enabled = os.environ.get('TRADING_ENABLED', 'false').lower() == 'true'
    
    try:
        # Extract parameters from event
        mode = event.get('mode', 'paper')
        ml_sell_threshold = event.get('ml_sell_threshold', -0.05)
        check_ml = event.get('check_ml_predictions', False)
        
        print(f"Starting portfolio monitor: mode={mode}, trading_enabled={trading_enabled}, ml_check={check_ml}")
        
        # Download portfolio database from S3
        db_local_path = '/tmp/portfolio.db'
        db_s3_key = 'portfolio_db/portfolio.db'
        try:
            s3_client.download_file(bucket_name, db_s3_key, db_local_path)
            print(f"Downloaded database from s3://{bucket_name}/{db_s3_key}")
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                print("No existing database found in S3")
            else:
                raise
        
        # Get Alpaca credentials
        secret_name = (os.environ['ALPACA_LIVE_SECRET_NAME'] if mode == 'live' 
                      else os.environ['ALPACA_PAPER_SECRET_NAME'])
        api_key, api_secret = get_alpaca_credentials(secrets_client, secret_name)
        
        # Set environment variables for Alpaca
        os.environ['ALPACA_PAPER_API_KEY' if mode == 'paper' else 'ALPACA_LIVE_API_KEY'] = api_key
        os.environ['ALPACA_PAPER_API_SECRET' if mode == 'paper' else 'ALPACA_LIVE_API_SECRET'] = api_secret
        
        # Initialize Alpaca client
        trading_client = TradingClient(api_key, api_secret, paper=(mode == 'paper'))
        
        # Get current positions
        positions = trading_client.get_all_positions()
        
        if not positions:
            print("No positions to monitor")
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'No positions to monitor',
                    'mode': mode
                })
            }
        
        print(f"Monitoring {len(positions)} positions")
        
        # Track sell decisions
        sell_actions = []
        holds = []
        
        for position in positions:
            symbol = position.symbol
            qty = float(position.qty)
            current_price = float(position.current_price)
            avg_entry = float(position.avg_entry_price)
            unrealized_pl = float(position.unrealized_pl)
            unrealized_plpc = float(position.unrealized_plpc)
            
            # Get protection levels from database
            stop_loss, take_profit = get_position_protections(db_local_path, symbol, mode)
            
            sell_reason = None
            
            # Check stop-loss
            if stop_loss and current_price <= stop_loss:
                sell_reason = f'STOP_LOSS (${stop_loss:.2f})'
                print(f"{symbol}: Stop loss triggered - current=${current_price:.2f}, stop=${stop_loss:.2f}")
            
            # Check take-profit
            elif take_profit and current_price >= take_profit:
                sell_reason = f'TAKE_PROFIT (${take_profit:.2f})'
                print(f"{symbol}: Take profit triggered - current=${current_price:.2f}, take=${take_profit:.2f}")
            
            # Check ML prediction if enabled
            elif check_ml:
                predicted_return = get_ml_prediction(symbol)
                if predicted_return < ml_sell_threshold:
                    sell_reason = f'ML_PREDICTION ({predicted_return*100:.2f}% predicted)'
                    print(f"{symbol}: ML sell signal - predicted return={predicted_return*100:.2f}%")
            
            # Execute sell if reason found
            if sell_reason:
                if trading_enabled:
                    try:
                        # Submit market sell order
                        order_request = MarketOrderRequest(
                            symbol=symbol,
                            qty=qty,
                            side=OrderSide.SELL,
                            time_in_force=TimeInForce.DAY
                        )
                        order = trading_client.submit_order(order_request)
                        
                        sell_actions.append({
                            'symbol': symbol,
                            'qty': qty,
                            'price': current_price,
                            'reason': sell_reason,
                            'pl': unrealized_pl,
                            'pl_pct': unrealized_plpc,
                            'order_id': order.id,
                            'status': 'SUBMITTED'
                        })
                        print(f"✓ Submitted sell order for {symbol}: qty={qty}, reason={sell_reason}")
                        
                    except Exception as e:
                        sell_actions.append({
                            'symbol': symbol,
                            'qty': qty,
                            'price': current_price,
                            'reason': sell_reason,
                            'pl': unrealized_pl,
                            'pl_pct': unrealized_plpc,
                            'status': 'FAILED',
                            'error': str(e)
                        })
                        print(f"✗ Failed to sell {symbol}: {e}")
                else:
                    sell_actions.append({
                        'symbol': symbol,
                        'qty': qty,
                        'price': current_price,
                        'reason': sell_reason,
                        'pl': unrealized_pl,
                        'pl_pct': unrealized_plpc,
                        'status': 'SKIPPED_TRADING_DISABLED'
                    })
                    print(f"⚠️ Would sell {symbol} ({sell_reason}) but trading disabled")
            else:
                # Hold position
                holds.append({
                    'symbol': symbol,
                    'qty': qty,
                    'price': current_price,
                    'pl': unrealized_pl,
                    'pl_pct': unrealized_plpc,
                    'stop_loss': stop_loss,
                    'take_profit': take_profit
                })
        
        # Upload updated database to S3 if it exists
        if os.path.exists(db_local_path):
            s3_client.upload_file(db_local_path, bucket_name, db_s3_key)
            print(f"Uploaded database to s3://{bucket_name}/{db_s3_key}")
        
        # Send notification if sells were executed
        if sell_actions:
            sell_lines = []
            for action in sell_actions:
                status_emoji = '✓' if action['status'] == 'SUBMITTED' else ('✗' if action['status'] == 'FAILED' else '⚠️')
                sell_lines.append(
                    f"{status_emoji} {action['symbol']}: SELL {action['qty']:.2f} @ ${action['price']:.2f} "
                    f"({action['reason']}) - P/L: ${action['pl']:.2f} ({action['pl_pct']*100:.2f}%)"
                )
            
            message = f"""Portfolio Monitor - Sell Orders Executed

Mode: {mode.upper()}
Trading Enabled: {trading_enabled}
Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Total Positions: {len(positions)}
Sells: {len(sell_actions)}
Holds: {len(holds)}

📤 SELL ORDERS:
{chr(10).join(sell_lines)}
"""
            
            sns_client.publish(
                TopicArn=alert_topic if trading_enabled else status_topic,
                Subject=f'{"💰" if trading_enabled else "🧪"} Portfolio Monitor ({mode.upper()}) - {len(sell_actions)} sells',
                Message=message
            )
        
        # Prepare summary
        summary = {
            'mode': mode,
            'trading_enabled': trading_enabled,
            'position_count': len(positions),
            'sells': len(sell_actions),
            'holds': len(holds),
            'sell_actions': sell_actions,
            'timestamp': datetime.now().isoformat()
        }
        
        print(f"Monitor summary: {json.dumps(summary, indent=2)}")
        
        return {
            'statusCode': 200,
            'body': json.dumps(summary)
        }
        
    except Exception as e:
        error_msg = f"Portfolio monitoring failed: {str(e)}"
        print(error_msg)
        
        # Send alert notification
        sns_client.publish(
            TopicArn=alert_topic,
            Subject=f'🚨 Portfolio Monitor Failed ({mode.upper()})',
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
