"""
Lambda handler for equity/growth screener workflow.
Runs equity screener and saves results to S3.
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

from runners.yfinance_growth_screener import main as run_growth_screener


def lambda_handler(event, context):
    """
    Lambda handler for equity screener.
    
    Event structure:
    {
        "run_type": "scheduled",  # or "adhoc"
        "config_path": "configs/equity_screener.json"  # optional override
    }
    """
    # Initialize AWS clients
    s3_client = boto3.client('s3')
    sns_client = boto3.client('sns')
    
    bucket_name = os.environ['S3_BUCKET']
    alert_topic = os.environ['SNS_ALERT_TOPIC']
    status_topic = os.environ['SNS_STATUS_TOPIC']
    
    try:
        # Extract parameters from event
        run_type = event.get('run_type', 'scheduled')
        config_s3_key = event.get('config_path', 'configs/equity_screener.json')
        
        print(f"Starting equity screener: run_type={run_type}, config={config_s3_key}")
        
        # Download config file from S3
        config_local_path = '/tmp/equity_screener.json'
        try:
            s3_client.download_file(bucket_name, config_s3_key, config_local_path)
            print(f"Downloaded config from s3://{bucket_name}/{config_s3_key}")
        except s3_client.exceptions.NoSuchKey:
            raise ValueError(f"Config file not found: s3://{bucket_name}/{config_s3_key}")
        
        # Create temp directory for outputs
        output_dir = '/tmp/screener_results'
        os.makedirs(output_dir, exist_ok=True)
        
        # Run equity screener
        sys.argv = [
            'yfinance_growth_screener',
            '--config', config_local_path,
            '--output-dir', output_dir
        ]
        
        # Execute the screener
        run_growth_screener()
        
        # Find the generated CSV file (most recent)
        csv_files = list(Path(output_dir).glob('equity_screener_*.csv'))
        if not csv_files:
            raise ValueError("No equity screener CSV file generated")
        
        latest_csv = max(csv_files, key=lambda p: p.stat().st_mtime)
        
        # Upload results to S3
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        s3_output_key = f'screener_results/equity_screener_{timestamp}.csv'
        s3_client.upload_file(str(latest_csv), bucket_name, s3_output_key)
        print(f"Uploaded results to s3://{bucket_name}/{s3_output_key}")
        
        # Count candidates
        import pandas as pd
        df = pd.read_csv(latest_csv)
        candidate_count = len(df)
        
        # Send status notification
        message = f"""Equity Screener Completed Successfully

Run Type: {run_type}
Timestamp: {timestamp}
Candidates Found: {candidate_count}
S3 Location: s3://{bucket_name}/{s3_output_key}

Top 5 Candidates:
{df.head(5).to_string() if candidate_count > 0 else 'No candidates'}
"""
        
        sns_client.publish(
            TopicArn=status_topic,
            Subject=f'✅ Equity Screener Success - {candidate_count} candidates',
            Message=message
        )
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Equity screener completed successfully',
                'candidate_count': candidate_count,
                's3_location': f's3://{bucket_name}/{s3_output_key}',
                'timestamp': timestamp
            })
        }
        
    except Exception as e:
        error_msg = f"Equity screener failed: {str(e)}"
        print(error_msg)
        
        # Send alert notification
        sns_client.publish(
            TopicArn=alert_topic,
            Subject='🚨 Equity Screener Failed',
            Message=f"{error_msg}\n\nFunction: {context.function_name}\nRequest ID: {context.request_id}"
        )
        
        return {
            'statusCode': 500,
            'body': json.dumps({
                'message': error_msg,
                'request_id': context.request_id
            })
        }
