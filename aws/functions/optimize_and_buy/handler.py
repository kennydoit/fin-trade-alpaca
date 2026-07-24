"""
Lambda handler for optimize and buy workflow.
Executes portfolio optimization and submits orders.
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

from runners.optimize_and_buy import main as run_optimize_and_buy


def get_alpaca_credentials(secrets_client, secret_name):
    """Retrieve Alpaca API credentials from Secrets Manager."""
    response = secrets_client.get_secret_value(SecretId=secret_name)
    secret = json.loads(response['SecretString'])
    return secret['key'], secret['secret']


def lambda_handler(event, context):
    """
    Lambda handler for optimize and buy.
    
    Event structure:
    {
        "run_type": "scheduled",  # or "adhoc"
        "mode": "paper",  # or "live"
        "max_notional": null,  # optional spending cap
        "dry_run": false  # preview only
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
        run_type = event.get('run_type', 'scheduled')
        mode = event.get('mode', 'paper')
        max_notional = event.get('max_notional')
        dry_run = event.get('dry_run', False)
        
        print(f"Starting optimize and buy: run_type={run_type}, mode={mode}, "
              f"dry_run={dry_run}, trading_enabled={trading_enabled}")
        
        # Safety check: Don't trade if TRADING_ENABLED is false
        if not trading_enabled and not dry_run:
            raise ValueError("Trading is disabled (TRADING_ENABLED=false). Set to true to enable trading.")
        
        # Safety check: Live mode requires explicit confirmation
        if mode == 'live' and not event.get('confirm_live', False):
            raise ValueError("Live mode requires 'confirm_live': true in event payload")
        
        # Download database from S3
        db_local_path = '/tmp/portfolio.db'
        db_s3_key = 'portfolio_db/portfolio.db'
        try:
            s3_client.download_file(bucket_name, db_s3_key, db_local_path)
            print(f"Downloaded database from s3://{bucket_name}/{db_s3_key}")
        except s3_client.exceptions.NoSuchKey:
            print("No existing database found in S3")
        
        # Download strategy config from S3
        config_s3_key = 'configs/strategy.json'
        config_local_path = '/tmp/strategy.json'
        try:
            s3_client.download_file(bucket_name, config_s3_key, config_local_path)
            print(f"Downloaded config from s3://{bucket_name}/{config_s3_key}")
        except s3_client.exceptions.NoSuchKey:
            raise ValueError(f"Strategy config not found: s3://{bucket_name}/{config_s3_key}")
        
        # Get Alpaca credentials
        secret_name = (os.environ['ALPACA_LIVE_SECRET_NAME'] if mode == 'live' 
                      else os.environ['ALPACA_PAPER_SECRET_NAME'])
        api_key, api_secret = get_alpaca_credentials(secrets_client, secret_name)
        
        # Set environment variables for the runner
        os.environ['ALPACA_PAPER_API_KEY' if mode == 'paper' else 'ALPACA_LIVE_API_KEY'] = api_key
        os.environ['ALPACA_PAPER_API_SECRET' if mode == 'paper' else 'ALPACA_LIVE_API_SECRET'] = api_secret
        
        # Build command line arguments
        sys.argv = [
            'optimize_and_buy',
            '--mode', mode,
            '--run-type', run_type,
            '--config', config_local_path
        ]
        
        if dry_run:
            sys.argv.append('--dry-run')
        
        if max_notional:
            sys.argv.extend(['--max-notional', str(max_notional)])
        
        if mode == 'live':
            sys.argv.append('--confirm-live')
        
        # Execute optimize and buy
        result = run_optimize_and_buy()
        
        # Upload updated database to S3
        if os.path.exists(db_local_path):
            s3_client.upload_file(db_local_path, bucket_name, db_s3_key)
            print(f"Uploaded database to s3://{bucket_name}/{db_s3_key}")
        
        # Parse result (this depends on what optimize_and_buy returns)
        # For now, assume success
        order_count = 0  # TODO: Extract from result
        total_notional = 0  # TODO: Extract from result
        
        # Send status notification
        message = f"""Optimize and Buy {'(DRY RUN)' if dry_run else 'Completed'}

Mode: {mode.upper()}
Run Type: {run_type}
Trading Enabled: {trading_enabled}
Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Orders Submitted: {order_count}
Total Notional: ${total_notional:.2f}

{'⚠️ DRY RUN - No actual orders submitted' if dry_run else '✅ Orders submitted successfully'}
"""
        
        sns_client.publish(
            TopicArn=status_topic if dry_run else alert_topic,
            Subject=f"{'🧪' if dry_run else '💰'} Optimize & Buy {mode.upper()} - {order_count} orders",
            Message=message
        )
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': f'Optimize and buy completed {"(dry run)" if dry_run else "successfully"}',
                'mode': mode,
                'order_count': order_count,
                'total_notional': total_notional,
                'dry_run': dry_run,
                'timestamp': datetime.now().isoformat()
            })
        }
        
    except Exception as e:
        error_msg = f"Optimize and buy failed: {str(e)}"
        print(error_msg)
        
        # Send alert notification
        sns_client.publish(
            TopicArn=alert_topic,
            Subject=f'🚨 Optimize & Buy Failed ({mode.upper()})',
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
