"""
Lambda handler for portfolio monitoring workflow.
Monitors positions for stop-loss triggers and alerts on threshold breaches.
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

from runners.optimize_and_buy import resolve_credentials
from fin_trade_alpaca.env_loader import load_environment_for_mode
from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest


def get_alpaca_credentials(secrets_client, secret_name):
    """Retrieve Alpaca API credentials from Secrets Manager."""
    response = secrets_client.get_secret_value(SecretId=secret_name)
    secret = json.loads(response['SecretString'])
    return secret['key'], secret['secret']


def lambda_handler(event, context):
    """
    Lambda handler for portfolio monitoring.
    
    Event structure:
    {
        "mode": "paper"  # or "live"
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
        
        print(f"Starting portfolio monitor: mode={mode}")
        
        # Get Alpaca credentials
        secret_name = (os.environ['ALPACA_LIVE_SECRET_NAME'] if mode == 'live' 
                      else os.environ['ALPACA_PAPER_SECRET_NAME'])
        api_key, api_secret = get_alpaca_credentials(secrets_client, secret_name)
        
        # Initialize Alpaca clients
        trading_client = TradingClient(api_key, api_secret, paper=(mode == 'paper'))
        data_client = StockHistoricalDataClient(api_key, api_secret)
        
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
        
        # Check each position for threshold breaches
        alerts = []
        warnings = []
        
        for position in positions:
            symbol = position.symbol
            qty = float(position.qty)
            current_price = float(position.current_price)
            avg_entry = float(position.avg_entry_price)
            market_value = float(position.market_value)
            unrealized_pl = float(position.unrealized_pl)
            unrealized_plpc = float(position.unrealized_plpc)
            
            # Check for significant losses (>5%)
            if unrealized_plpc < -0.05:
                alerts.append({
                    'symbol': symbol,
                    'type': 'LARGE_LOSS',
                    'unrealized_plpc': unrealized_plpc,
                    'unrealized_pl': unrealized_pl,
                    'current_price': current_price
                })
            # Check for moderate losses (>3%)
            elif unrealized_plpc < -0.03:
                warnings.append({
                    'symbol': symbol,
                    'type': 'MODERATE_LOSS',
                    'unrealized_plpc': unrealized_plpc,
                    'unrealized_pl': unrealized_pl,
                    'current_price': current_price
                })
            # Check for large gains (>10%)
            elif unrealized_plpc > 0.10:
                alerts.append({
                    'symbol': symbol,
                    'type': 'LARGE_GAIN',
                    'unrealized_plpc': unrealized_plpc,
                    'unrealized_pl': unrealized_pl,
                    'current_price': current_price
                })
        
        # Send notifications if there are alerts or warnings
        if alerts:
            alert_lines = []
            for alert in alerts:
                alert_lines.append(
                    f"{alert['symbol']}: {alert['type']} - "
                    f"{alert['unrealized_plpc']*100:.2f}% (${alert['unrealized_pl']:.2f}) "
                    f"@ ${alert['current_price']:.2f}"
                )
            
            message = f"""Portfolio Alert - Threshold Breached

Mode: {mode.upper()}
Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Total Positions: {len(positions)}
Alerts: {len(alerts)}

⚠️ ALERTS:
{chr(10).join(alert_lines)}
"""
            
            sns_client.publish(
                TopicArn=alert_topic,
                Subject=f'⚠️ Portfolio Alert ({mode.upper()}) - {len(alerts)} positions',
                Message=message
            )
        
        # Log summary
        total_value = sum(float(p.market_value) for p in positions)
        total_pl = sum(float(p.unrealized_pl) for p in positions)
        total_plpc = (total_pl / (total_value - total_pl)) if (total_value - total_pl) != 0 else 0
        
        summary = {
            'mode': mode,
            'position_count': len(positions),
            'total_market_value': total_value,
            'total_unrealized_pl': total_pl,
            'total_unrealized_plpc': total_plpc,
            'alert_count': len(alerts),
            'warning_count': len(warnings),
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
