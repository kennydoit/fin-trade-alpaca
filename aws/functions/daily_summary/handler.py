"""
Lambda handler for daily summary generation.
Generates portfolio summary report and sends via email.
"""
import json
import os
import sys
from datetime import datetime, timedelta
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
    Lambda handler for daily summary.
    
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
    status_topic = os.environ['SNS_STATUS_TOPIC']
    
    try:
        # Extract parameters from event
        mode = event.get('mode', 'paper')
        
        print(f"Generating daily summary: mode={mode}")
        
        # Get Alpaca credentials
        secret_name = (os.environ['ALPACA_LIVE_SECRET_NAME'] if mode == 'live' 
                      else os.environ['ALPACA_PAPER_SECRET_NAME'])
        api_key, api_secret = get_alpaca_credentials(secrets_client, secret_name)
        
        # Initialize Alpaca client
        from alpaca.trading.client import TradingClient
        
        trading_client = TradingClient(api_key, api_secret, paper=(mode == 'paper'))
        
        # Get account info
        account = trading_client.get_account()
        
        # Get current positions
        positions = trading_client.get_all_positions()
        
        # Get recent orders (last 24 hours)
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        
        after_date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        order_request = GetOrdersRequest(
            status=QueryOrderStatus.ALL,
            limit=100,
            after=after_date
        )
        recent_orders = trading_client.get_orders(filter=order_request)
        
        # Calculate portfolio metrics
        total_equity = float(account.equity)
        total_cash = float(account.cash)
        buying_power = float(account.buying_power)
        portfolio_value = float(account.portfolio_value)
        
        total_market_value = sum(float(p.market_value) for p in positions) if positions else 0
        total_unrealized_pl = sum(float(p.unrealized_pl) for p in positions) if positions else 0
        total_cost_basis = total_market_value - total_unrealized_pl
        total_unrealized_plpc = (total_unrealized_pl / total_cost_basis * 100) if total_cost_basis != 0 else 0
        
        # Count orders by status
        filled_orders = [o for o in recent_orders if str(o.status) == 'filled']
        pending_orders = [o for o in recent_orders if str(o.status) in ['new', 'accepted', 'pending_new']]
        cancelled_orders = [o for o in recent_orders if str(o.status) in ['canceled', 'cancelled']]
        
        # Build summary report
        report = f"""📊 Daily Portfolio Summary - {mode.upper()}
{'='*60}
Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S ET')}

💰 ACCOUNT OVERVIEW
{'─'*60}
Portfolio Value:    ${portfolio_value:,.2f}
Cash Available:     ${total_cash:,.2f}
Buying Power:       ${buying_power:,.2f}
Total Equity:       ${total_equity:,.2f}

📈 POSITIONS ({len(positions)})
{'─'*60}
Total Market Value: ${total_market_value:,.2f}
Total Cost Basis:   ${total_cost_basis:,.2f}
Unrealized P&L:     ${total_unrealized_pl:,.2f} ({total_unrealized_plpc:+.2f}%)

"""
        
        # Add top positions
        if positions:
            # Sort by market value descending
            sorted_positions = sorted(positions, key=lambda p: float(p.market_value), reverse=True)
            report += "\nTop Positions:\n"
            for i, pos in enumerate(sorted_positions[:10], 1):
                symbol = pos.symbol
                qty = float(pos.qty)
                current_price = float(pos.current_price)
                market_value = float(pos.market_value)
                unrealized_pl = float(pos.unrealized_pl)
                unrealized_plpc = float(pos.unrealized_plpc) * 100
                
                report += (f"{i:2d}. {symbol:6s} | {qty:8.2f} @ ${current_price:7.2f} = ${market_value:9,.2f} "
                          f"| P&L: ${unrealized_pl:8,.2f} ({unrealized_plpc:+6.2f}%)\n")
        
        # Add recent orders
        report += f"\n\n📋 RECENT ORDERS (Last 24 Hours)\n{'─'*60}\n"
        report += f"Filled:    {len(filled_orders)}\n"
        report += f"Pending:   {len(pending_orders)}\n"
        report += f"Cancelled: {len(cancelled_orders)}\n"
        
        if filled_orders:
            report += "\nRecent Fills:\n"
            for order in filled_orders[:5]:  # Show last 5 fills
                symbol = order.symbol
                side = str(order.side).upper()
                qty = float(order.filled_qty) if order.filled_qty else 0
                fill_price = float(order.filled_avg_price) if order.filled_avg_price else 0
                filled_at = order.filled_at.strftime('%H:%M') if order.filled_at else 'N/A'
                report += f"  • {filled_at} | {side:4s} {qty:8.2f} {symbol:6s} @ ${fill_price:7.2f}\n"
        
        if pending_orders:
            report += "\nPending Orders:\n"
            for order in pending_orders[:5]:
                symbol = order.symbol
                side = str(order.side).upper()
                qty = float(order.qty) if order.qty else 0
                order_type = str(order.type).upper()
                report += f"  • {order_type:6s} | {side:4s} {qty:8.2f} {symbol}\n"
        
        # Add performance indicators
        report += f"\n\n📊 PERFORMANCE INDICATORS\n{'─'*60}\n"
        
        # Calculate diversification
        if positions:
            largest_position_pct = max(float(p.market_value) / total_market_value * 100 for p in positions) if total_market_value > 0 else 0
            report += f"Position Count:     {len(positions)}\n"
            report += f"Largest Position:   {largest_position_pct:.2f}% of portfolio\n"
            
            # Count winners vs losers
            winners = [p for p in positions if float(p.unrealized_pl) > 0]
            losers = [p for p in positions if float(p.unrealized_pl) < 0]
            report += f"Winners / Losers:   {len(winners)} / {len(losers)}\n"
            
            # Largest gain/loss
            if positions:
                best_performer = max(positions, key=lambda p: float(p.unrealized_plpc))
                worst_performer = min(positions, key=lambda p: float(p.unrealized_plpc))
                report += f"Best Performer:     {best_performer.symbol} ({float(best_performer.unrealized_plpc)*100:+.2f}%)\n"
                report += f"Worst Performer:    {worst_performer.symbol} ({float(worst_performer.unrealized_plpc)*100:+.2f}%)\n"
        
        report += f"\n{'='*60}\n"
        report += f"Generated by fin-trade-alpaca automated trading system\n"
        
        # Send summary via SNS
        sns_client.publish(
            TopicArn=status_topic,
            Subject=f'📊 Daily Portfolio Summary ({mode.upper()}) - {len(positions)} positions, ${total_unrealized_pl:,.2f} P&L',
            Message=report
        )
        
        # Save summary to S3
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        summary_key = f'summaries/{mode}/daily_summary_{timestamp}.txt'
        s3_client.put_object(
            Bucket=bucket_name,
            Key=summary_key,
            Body=report.encode('utf-8'),
            ContentType='text/plain'
        )
        print(f"Saved summary to s3://{bucket_name}/{summary_key}")
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Daily summary generated successfully',
                'mode': mode,
                'position_count': len(positions),
                'total_unrealized_pl': total_unrealized_pl,
                's3_location': f's3://{bucket_name}/{summary_key}',
                'timestamp': timestamp
            })
        }
        
    except Exception as e:
        error_msg = f"Daily summary generation failed: {str(e)}"
        print(error_msg)
        
        # Send alert notification (but don't use alert topic for this)
        sns_client.publish(
            TopicArn=status_topic,
            Subject=f'⚠️ Daily Summary Failed ({mode.upper()})',
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
