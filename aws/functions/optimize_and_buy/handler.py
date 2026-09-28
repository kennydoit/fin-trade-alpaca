"""
Lambda handler for optimize and buy workflow.
Executes portfolio optimization and submits orders.
"""
import json
import os
import re
import sys
from contextlib import redirect_stdout
from datetime import datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path
import time
import boto3
from botocore.exceptions import ClientError

# Add source code to path
sys.path.insert(0, '/opt/python')  # Lambda layer
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from runners.optimize_and_buy import main as run_optimize_and_buy

BUY_LINE_RE = re.compile(r"^\s*BUY\s+[^:]+:\s+\$(?P<notional>\d+(?:\.\d+)?)")
SUBMITTED_LINE_RE = re.compile(r"Submitted\s+\S+\s+order\s+id=")
ORDER_SUMMARY_PREFIX = "ORDER_SUMMARY|"


def get_alpaca_credentials(secrets_client, secret_name):
    """Retrieve Alpaca API credentials from Secrets Manager."""
    response = secrets_client.get_secret_value(SecretId=secret_name)
    secret = json.loads(response['SecretString'])
    return secret['key'], secret['secret']


def get_secret_payload(secrets_client, secret_name):
    response = secrets_client.get_secret_value(SecretId=secret_name)
    secret_string = response.get('SecretString') or '{}'
    return json.loads(secret_string)


def _safe_decimal(value, default='0.00'):
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def _format_trigger(order_detail):
    description = order_detail.get('trigger_description')
    if description:
        return str(description)
    metric_name = order_detail.get('trigger_metric_name')
    metric_value = order_detail.get('trigger_metric_value')
    if metric_name and metric_value is not None:
        return f"{metric_name}={metric_value}"
    return 'n/a'


def _format_order_line(order_detail):
    symbol = order_detail.get('symbol', '?')
    notional = _safe_decimal(order_detail.get('notional'))
    section = str(order_detail.get('section') or 'unknown').replace('_', ' ')
    trigger = _format_trigger(order_detail)
    return f"- {symbol}: ${notional:.2f} [{section}] trigger={trigger}"


def wait_for_sell_orders_to_settle(api_key, api_secret, mode, timeout_seconds, poll_interval_seconds):
    from alpaca.trading.client import TradingClient

    client = TradingClient(api_key, api_secret, paper=(mode == 'paper'))
    deadline = time.time() + float(timeout_seconds)

    while True:
        try:
            open_orders = client.get_all_orders(status='open')
        except Exception:
            try:
                open_orders = client.get_orders(status='open')
            except Exception:
                open_orders = []

        open_sell_orders = []
        for order in open_orders:
            side = str(getattr(getattr(order, 'side', ''), 'value', getattr(order, 'side', ''))).lower()
            if side == 'sell':
                open_sell_orders.append(order)

        if not open_sell_orders:
            print('No open sell orders remain; continuing with optimize-and-buy.')
            return

        if time.time() >= deadline:
            symbols = sorted({str(getattr(order, 'symbol', '?')) for order in open_sell_orders})
            raise TimeoutError(
                'Timed out waiting for sell orders to settle before buy run: '
                + ', '.join(symbols)
            )

        symbols = sorted({str(getattr(order, 'symbol', '?')) for order in open_sell_orders})
        print(
            f"Waiting for {len(open_sell_orders)} open sell orders to settle before buy run: "
            + ', '.join(symbols)
        )
        time.sleep(max(1, int(poll_interval_seconds)))


def summarize_runner_output(output_text):
    """Extract planned/submitted orders and detailed order context from runner stdout."""
    planned_order_count = 0
    planned_total_notional = Decimal('0.00')
    submitted_order_count = 0
    order_details = []
    planned_orders = []
    submitted_orders = []

    for line in output_text.splitlines():
        if line.startswith(ORDER_SUMMARY_PREFIX):
            payload_text = line[len(ORDER_SUMMARY_PREFIX):]
            try:
                payload = json.loads(payload_text)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                order_details.append(payload)
                status = payload.get('status')
                if status == 'planned':
                    planned_orders.append(payload)
                elif status == 'submitted':
                    submitted_orders.append(payload)

        buy_match = BUY_LINE_RE.match(line)
        if buy_match:
            planned_order_count += 1
            planned_total_notional += Decimal(buy_match.group('notional'))

        if SUBMITTED_LINE_RE.search(line):
            submitted_order_count += 1

    if planned_orders:
        planned_order_count = len(planned_orders)
        planned_total_notional = sum((_safe_decimal(order.get('notional')) for order in planned_orders), Decimal('0.00'))

    if submitted_orders:
        submitted_order_count = len(submitted_orders)

    return {
        'planned_order_count': planned_order_count,
        'planned_total_notional': planned_total_notional,
        'submitted_order_count': submitted_order_count,
        'planned_orders': planned_orders,
        'submitted_orders': submitted_orders,
        'order_details': order_details,
    }


def lambda_handler(event, context):
    """
    Lambda handler for optimize and buy.
    
    Event structure:
    {
        "run_type": "scheduled",  # or "adhoc"
        "mode": "paper",  # or "live"
        "max_notional": null,  # optional spending cap
        "dry_run": false,  # preview only
        "sell_cooldown_days_override": 0,  # optional override for recent sell cooldown
        "wait_for_sell_orders": true,  # optional pre-buy settlement gate
        "sell_order_wait_timeout_seconds": 600,
        "sell_order_poll_interval_seconds": 10
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
        sell_cooldown_override = event.get('sell_cooldown_days_override')
        wait_for_sell_orders = event.get('wait_for_sell_orders', run_type == 'scheduled' and mode == 'paper')
        sell_order_wait_timeout_seconds = int(event.get('sell_order_wait_timeout_seconds', 600))
        sell_order_poll_interval_seconds = int(event.get('sell_order_poll_interval_seconds', 10))
        
        print(f"Starting optimize and buy: run_type={run_type}, mode={mode}, "
              f"dry_run={dry_run}, trading_enabled={trading_enabled}")
        if sell_cooldown_override is not None:
            print(f"Applying sell_cooldown_days_override={sell_cooldown_override}")
        
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
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                print("No existing database found in S3")
            else:
                raise
        
        # Download latest predictions from S3
        print("Downloading latest predictions from S3...")
        predictions_local_path = '/tmp/predictions.csv'
        try:
            response = s3_client.list_objects_v2(
                Bucket=bucket_name,
                Prefix='screener_results/predictions_'
            )
            if 'Contents' not in response or len(response['Contents']) == 0:
                raise ValueError("No prediction files found in S3. Run prediction screener first.")
            
            # Get most recent predictions file
            latest_file = max(response['Contents'], key=lambda x: x['LastModified'])
            predictions_s3_key = latest_file['Key']
            s3_client.download_file(bucket_name, predictions_s3_key, predictions_local_path)
            print(f"Downloaded predictions from s3://{bucket_name}/{predictions_s3_key}")
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                raise ValueError("No predictions found in S3. Run prediction screener first.")
            else:
                raise
        
        # Download strategy config from S3
        config_s3_key = 'configs/strategy.json'
        config_local_path = '/tmp/strategy.json'
        try:
            s3_client.download_file(bucket_name, config_s3_key, config_local_path)
            print(f"Downloaded config from s3://{bucket_name}/{config_s3_key}")
            
            # Update config to point to local predictions file
            with open(config_local_path, 'r') as f:
                config = json.load(f)
            
            # Update short_term bucket to use downloaded predictions
            if 'buckets' in config and 'short_term' in config['buckets']:
                config['buckets']['short_term']['screener'] = predictions_local_path
                print(f"Updated strategy config to use predictions: {predictions_local_path}")

            statarb_exclusion_cfg = config.get('statarb_exclusion') or {}
            if statarb_exclusion_cfg.get('enabled', False):
                pairs_prefix = os.environ.get('STATARB_RESULTS_PREFIX', 'statarb_results/pairs_')
                statarb_pairs_local_path = '/tmp/statarb_pairs.csv'
                try:
                    statarb_response = s3_client.list_objects_v2(Bucket=bucket_name, Prefix=pairs_prefix)
                    if 'Contents' in statarb_response and len(statarb_response['Contents']) > 0:
                        latest_statarb_file = max(statarb_response['Contents'], key=lambda x: x['LastModified'])
                        statarb_pairs_s3_key = latest_statarb_file['Key']
                        s3_client.download_file(bucket_name, statarb_pairs_s3_key, statarb_pairs_local_path)
                        statarb_exclusion_cfg['pairs_csv'] = statarb_pairs_local_path
                        config['statarb_exclusion'] = statarb_exclusion_cfg
                        print(f"Updated strategy config to use statarb exclusion pairs: {statarb_pairs_local_path}")
                    else:
                        print('Statarb exclusion enabled, but no statarb pairs file found in S3.')
                except ClientError as ex:
                    print(f"Statarb exclusion pair lookup failed: {ex}")
            
            # Write updated config
            with open(config_local_path, 'w') as f:
                json.dump(config, f, indent=2)
                
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                raise ValueError(f"Strategy config not found: s3://{bucket_name}/{config_s3_key}")
            else:
                raise
        
        # Get Alpaca credentials
        secret_name = (os.environ['ALPACA_LIVE_SECRET_NAME'] if mode == 'live' 
                      else os.environ['ALPACA_PAPER_SECRET_NAME'])
        api_key, api_secret = get_alpaca_credentials(secrets_client, secret_name)

        perplexity_secret_name = os.environ.get('PERPLEXITY_SECRET_NAME')
        if perplexity_secret_name and not os.environ.get('PERPLEXITY_API_KEY'):
            try:
                perplexity_secret = get_secret_payload(secrets_client, perplexity_secret_name)
                os.environ['PERPLEXITY_API_KEY'] = (
                    perplexity_secret.get('key')
                    or perplexity_secret.get('api_key')
                    or perplexity_secret.get('token')
                    or ''
                )
                if os.environ['PERPLEXITY_API_KEY']:
                    print(f"Loaded PERPLEXITY_API_KEY from Secrets Manager secret {perplexity_secret_name}")
            except Exception as ex:
                print(f"Unable to load Perplexity secret {perplexity_secret_name}: {ex}")
        
        # Set environment variables for the runner
        os.environ['ALPACA_PAPER_API_KEY' if mode == 'paper' else 'ALPACA_LIVE_API_KEY'] = api_key
        os.environ['ALPACA_PAPER_API_SECRET' if mode == 'paper' else 'ALPACA_LIVE_API_SECRET'] = api_secret
        if sell_cooldown_override is not None:
            os.environ['FINTRADE_SELL_COOLDOWN_DAYS_OVERRIDE'] = str(sell_cooldown_override)
        elif 'FINTRADE_SELL_COOLDOWN_DAYS_OVERRIDE' in os.environ:
            del os.environ['FINTRADE_SELL_COOLDOWN_DAYS_OVERRIDE']

        if wait_for_sell_orders and not dry_run:
            wait_for_sell_orders_to_settle(
                api_key=api_key,
                api_secret=api_secret,
                mode=mode,
                timeout_seconds=sell_order_wait_timeout_seconds,
                poll_interval_seconds=sell_order_poll_interval_seconds,
            )
        
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
        
        # Execute optimize and buy and preserve stdout for both logs and summary reporting.
        stdout_buffer = StringIO()
        with redirect_stdout(stdout_buffer):
            result = run_optimize_and_buy()
        runner_output = stdout_buffer.getvalue()
        if runner_output:
            print(runner_output, end='' if runner_output.endswith('\n') else '\n')
        summary = summarize_runner_output(runner_output)
        
        # Upload updated database to S3
        if os.path.exists(db_local_path):
            s3_client.upload_file(db_local_path, bucket_name, db_s3_key)
            print(f"Uploaded database to s3://{bucket_name}/{db_s3_key}")
        
        planned_order_count = summary['planned_order_count']
        submitted_order_count = summary['submitted_order_count']
        total_notional = float(summary['planned_total_notional'])
        order_count = planned_order_count if dry_run else submitted_order_count
        count_label = 'Orders Planned' if dry_run else 'Orders Submitted'
        notional_label = 'Planned Notional' if dry_run else 'Total Notional'
        detailed_orders = summary['planned_orders'] if dry_run else summary['submitted_orders']
        
        # Send status notification
        message = f"""Optimize and Buy {'(DRY RUN)' if dry_run else 'Completed'}

Mode: {mode.upper()}
Run Type: {run_type}
Trading Enabled: {trading_enabled}
Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
{count_label}: {order_count}
{notional_label}: ${total_notional:.2f}

{'⚠️ DRY RUN - No actual orders submitted' if dry_run else '✅ Orders submitted successfully'}
"""

        if detailed_orders:
            message += "\nAssets:\n"
            for order_detail in detailed_orders:
                message += _format_order_line(order_detail) + "\n"
        
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
                'planned_order_count': planned_order_count,
                'submitted_order_count': submitted_order_count,
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
