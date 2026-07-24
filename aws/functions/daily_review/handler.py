"""
Lambda handler for daily portfolio review.
Evaluates positions and executes exit logic based on predictions and price movements.
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

from tools.daily_portfolio_review import AGGRESSIVENESS_PRESETS


def get_alpaca_credentials(secrets_client, secret_name):
    """Retrieve Alpaca API credentials from Secrets Manager."""
    response = secrets_client.get_secret_value(SecretId=secret_name)
    secret = json.loads(response['SecretString'])
    return secret['key'], secret['secret']


def lambda_handler(event, context):
    """
    Lambda handler for daily portfolio review.
    
    Event structure:
    {
        "mode": "paper",  # or "live"
        "aggressiveness": "moderate",  # conservative, moderate, aggressive, or custom
        "min_prediction_rank": 10,  # optional: custom threshold
        "stop_loss_pct": -3.0,  # optional: custom threshold
        "take_profit_pct": 7.0,  # optional: custom threshold
        "dry_run": false
    }
    """
    # Initialize AWS clients
    s3_client = boto3.client('s3')
    secrets_client = boto3.client('secretsmanager')
    sns_client = boto3.client('sns')
    
    bucket_name = os.environ['S3_BUCKET']
    alert_topic = os.environ['SNS_ALERT_TOPIC']
    status_topic = os.environ['SNS_STATUS_TOPIC']
    
    try:
        # Extract parameters from event
        mode = event.get('mode', 'paper')
        aggressiveness = event.get('aggressiveness', 'moderate')
        dry_run = event.get('dry_run', False)
        
        print(f"Starting daily review: mode={mode}, aggressiveness={aggressiveness}, dry_run={dry_run}")
        
        # Get thresholds (either from preset or custom)
        if aggressiveness in AGGRESSIVENESS_PRESETS:
            thresholds = AGGRESSIVENESS_PRESETS[aggressiveness]
        else:
            thresholds = {
                'min_prediction_rank': event.get('min_prediction_rank', 10),
                'stop_loss_pct': event.get('stop_loss_pct', -3.0),
                'take_profit_pct': event.get('take_profit_pct', 7.0),
            }
        
        print(f"Using thresholds: {thresholds}")
        
        # Get Alpaca credentials
        secret_name = (os.environ['ALPACA_LIVE_SECRET_NAME'] if mode == 'live' 
                      else os.environ['ALPACA_PAPER_SECRET_NAME'])
        api_key, api_secret = get_alpaca_credentials(secrets_client, secret_name)
        
        # Initialize Alpaca clients
        from alpaca.trading.client import TradingClient
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestQuoteRequest
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        
        trading_client = TradingClient(api_key, api_secret, paper=(mode == 'paper'))
        data_client = StockHistoricalDataClient(api_key, api_secret)
        
        # Get current positions
        positions = trading_client.get_all_positions()
        
        if not positions:
            print("No positions to review")
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'No positions to review',
                    'mode': mode
                })
            }
        
        # Download latest predictions from S3
        # Find the most recent predictions file
        response = s3_client.list_objects_v2(
            Bucket=bucket_name,
            Prefix='screener_results/predictions_'
        )
        
        if 'Contents' not in response or not response['Contents']:
            print("No predictions found in S3, skipping prediction-based exits")
            predictions_df = None
        else:
            # Get most recent predictions file
            latest_predictions = max(response['Contents'], key=lambda x: x['LastModified'])
            predictions_key = latest_predictions['Key']
            predictions_local_path = '/tmp/latest_predictions.csv'
            s3_client.download_file(bucket_name, predictions_key, predictions_local_path)
            
            import pandas as pd
            predictions_df = pd.read_csv(predictions_local_path)
            print(f"Loaded {len(predictions_df)} predictions from {predictions_key}")
        
        # Evaluate each position for exit
        exit_decisions = []
        
        for position in positions:
            symbol = position.symbol
            qty = float(position.qty)
            current_price = float(position.current_price)
            avg_entry = float(position.avg_entry_price)
            unrealized_plpc = float(position.unrealized_plpc)
            
            exit_reason = None
            
            # Check prediction rank (if we have predictions)
            if predictions_df is not None:
                symbol_predictions = predictions_df[predictions_df['symbol'] == symbol]
                if len(symbol_predictions) > 0:
                    rank = symbol_predictions.index[0] + 1  # 1-indexed rank
                    if rank > thresholds['min_prediction_rank']:
                        exit_reason = f"Prediction rank dropped to {rank} (threshold: {thresholds['min_prediction_rank']})"
                else:
                    exit_reason = f"Not in top predictions anymore"
            
            # Check stop loss
            if unrealized_plpc <= (thresholds['stop_loss_pct'] / 100):
                exit_reason = f"Stop loss triggered: {unrealized_plpc*100:.2f}% (threshold: {thresholds['stop_loss_pct']}%)"
            
            # Check take profit
            if unrealized_plpc >= (thresholds['take_profit_pct'] / 100):
                exit_reason = f"Take profit triggered: {unrealized_plpc*100:.2f}% (threshold: {thresholds['take_profit_pct']}%)"
            
            if exit_reason:
                exit_decisions.append({
                    'symbol': symbol,
                    'qty': qty,
                    'current_price': current_price,
                    'avg_entry': avg_entry,
                    'unrealized_plpc': unrealized_plpc,
                    'reason': exit_reason
                })
        
        # Execute exits
        executed_exits = []
        errors = []
        
        for exit_decision in exit_decisions:
            symbol = exit_decision['symbol']
            qty = exit_decision['qty']
            
            try:
                if not dry_run:
                    order = trading_client.submit_order(
                        MarketOrderRequest(
                            symbol=symbol,
                            qty=qty,
                            side=OrderSide.SELL,
                            time_in_force=TimeInForce.DAY
                        )
                    )
                    exit_decision['order_id'] = order.id
                    executed_exits.append(exit_decision)
                    print(f"Submitted exit order for {symbol}: {qty} shares")
                else:
                    executed_exits.append(exit_decision)
                    print(f"[DRY RUN] Would exit {symbol}: {qty} shares")
            
            except Exception as e:
                error_msg = f"{symbol}: {str(e)}"
                print(f"Error exiting {symbol}: {e}")
                errors.append(error_msg)
        
        # Send notification
        message = f"""Daily Portfolio Review {'(DRY RUN)' if dry_run else 'Completed'}

Mode: {mode.upper()}
Aggressiveness: {aggressiveness}
Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Positions Reviewed: {len(positions)}
Exits Triggered: {len(executed_exits)}
Errors: {len(errors)}

Thresholds:
- Min Prediction Rank: {thresholds['min_prediction_rank']}
- Stop Loss: {thresholds['stop_loss_pct']}%
- Take Profit: {thresholds['take_profit_pct']}%

"""
        
        if executed_exits:
            message += "\n📤 Exits:\n"
            for exit_dec in executed_exits:
                message += (f"\n{exit_dec['symbol']}: {exit_dec['qty']} shares @ ${exit_dec['current_price']:.2f}\n"
                           f"   P&L: {exit_dec['unrealized_plpc']*100:.2f}%\n"
                           f"   Reason: {exit_dec['reason']}\n")
        
        if errors:
            message += f"\n\n❌ Errors:\n" + "\n".join(errors)
        
        sns_client.publish(
            TopicArn=status_topic if dry_run or len(executed_exits) == 0 else alert_topic,
            Subject=f"{'🧪' if dry_run else '📊'} Daily Review ({mode.upper()}) - {len(executed_exits)} exits",
            Message=message
        )
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': f'Daily review completed {"(dry run)" if dry_run else ""}',
                'mode': mode,
                'positions_reviewed': len(positions),
                'exits_triggered': len(executed_exits),
                'error_count': len(errors),
                'dry_run': dry_run,
                'exits': executed_exits,
                'errors': errors
            })
        }
        
    except Exception as e:
        error_msg = f"Daily review failed: {str(e)}"
        print(error_msg)
        
        # Send alert notification
        sns_client.publish(
            TopicArn=alert_topic,
            Subject=f'🚨 Daily Review Failed ({mode.upper()})',
            Message=f"{error_msg}\n\nFunction: {context.function_name}\nRequest ID: {context.request_id}"
        )
        
        return {
            'statusCode': 500,
            'body': json.dumps({
                'message': error_msg,
                'mode': mode,
                'request_id': context.request_id
            })
        }
