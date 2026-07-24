"""
Lambda handler for prediction screener workflow.
Runs ML predictions and saves results to S3.
"""
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
import boto3

# Add source code to path
sys.path.insert(0, '/opt/python')  # Lambda layer
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from runners.predict_screener import main as run_predict_screener, resolve_runtime_config
import argparse


def lambda_handler(event, context):
    """
    Lambda handler for prediction screener.
    
    Event structure:
    {
        "run_type": "scheduled",  # or "adhoc"
        "limit": 200,
        "return_days": 5,
        "lookback": 180
    }
    """
    # Initialize AWS clients
    s3_client = boto3.client('s3')
    sns_client = boto3.client('sns')
    
    bucket_name = os.environ['S3_BUCKET']
    alert_topic = os.environ['SNS_ALERT_TOPIC']
    status_topic = os.environ['SNS_STATUS_TOPIC']
    trading_enabled = os.environ.get('TRADING_ENABLED', 'false').lower() == 'true'
    
    try:
        # Extract parameters from event
        run_type = event.get('run_type', 'scheduled')
        limit = event.get('limit', 200)
        return_days = event.get('return_days', 5)
        lookback = event.get('lookback', 180)
        
        print(f"Starting prediction screener: run_type={run_type}, limit={limit}, "
              f"return_days={return_days}, lookback={lookback}")
        
        # Download database from S3 if it exists
        db_local_path = '/tmp/portfolio.db'
        db_s3_key = 'portfolio_db/portfolio.db'
        try:
            s3_client.download_file(bucket_name, db_s3_key, db_local_path)
            print(f"Downloaded database from s3://{bucket_name}/{db_s3_key}")
        except s3_client.exceptions.NoSuchKey:
            print("No existing database found in S3, will create new")
        
        # Download config file from S3
        config_s3_key = 'configs/prediction_screener.json'
        config_local_path = '/tmp/prediction_screener.json'
        try:
            s3_client.download_file(bucket_name, config_s3_key, config_local_path)
            print(f"Downloaded config from s3://{bucket_name}/{config_s3_key}")
        except s3_client.exceptions.NoSuchKey:
            print("No config found in S3, using defaults")
            config_local_path = None
        
        # Create temp directory for outputs
        output_dir = '/tmp/screener_results'
        os.makedirs(output_dir, exist_ok=True)
        
        # Run prediction screener
        # Simulate argparse.Namespace for the existing code
        args = argparse.Namespace(
            limit=limit,
            return_days=return_days,
            lookback=lookback,
            config=config_local_path,
            db_path=db_local_path if os.path.exists(db_local_path) else None,
            output_dir=output_dir
        )
        
        # Call the prediction screener logic
        # Note: This requires adapting the main() function to accept args
        # For now, we'll set sys.argv to simulate CLI call
        sys.argv = [
            'predict_screener',
            '--limit', str(limit),
            '--return-days', str(return_days),
            '--lookback', str(lookback),
            '--output-dir', output_dir
        ]
        
        if config_local_path:
            sys.argv.extend(['--config', config_local_path])
        
        # Execute the screener (this will write CSV to output_dir)
        run_predict_screener()
        
        # Find the generated CSV file (most recent)
        csv_files = list(Path(output_dir).glob('predictions_*.csv'))
        if not csv_files:
            raise ValueError("No prediction CSV file generated")
        
        latest_csv = max(csv_files, key=lambda p: p.stat().st_mtime)
        
        # Upload results to S3
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        s3_output_key = f'screener_results/predictions_{timestamp}.csv'
        s3_client.upload_file(str(latest_csv), bucket_name, s3_output_key)
        print(f"Uploaded results to s3://{bucket_name}/{s3_output_key}")
        
        # Upload database if it was updated
        if os.path.exists(db_local_path):
            s3_client.upload_file(db_local_path, bucket_name, db_s3_key)
            print(f"Uploaded database to s3://{bucket_name}/{db_s3_key}")
        
        # Count predictions
        import pandas as pd
        df = pd.read_csv(latest_csv)
        prediction_count = len(df)
        
        # Send status notification
        message = f"""Prediction Screener Completed Successfully

Run Type: {run_type}
Timestamp: {timestamp}
Predictions Generated: {prediction_count}
S3 Location: s3://{bucket_name}/{s3_output_key}

Top 5 Predictions:
{df.head(5).to_string() if prediction_count > 0 else 'No predictions'}
"""
        
        sns_client.publish(
            TopicArn=status_topic,
            Subject=f'✅ Prediction Screener Success - {prediction_count} predictions',
            Message=message
        )
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Prediction screener completed successfully',
                'prediction_count': prediction_count,
                's3_location': f's3://{bucket_name}/{s3_output_key}',
                'timestamp': timestamp
            })
        }
        
    except Exception as e:
        error_msg = f"Prediction screener failed: {str(e)}"
        print(error_msg)
        
        # Send alert notification
        sns_client.publish(
            TopicArn=alert_topic,
            Subject='🚨 Prediction Screener Failed',
            Message=f"{error_msg}\n\nFunction: {context.function_name}\nRequest ID: {context.request_id}"
        )
        
        return {
            'statusCode': 500,
            'body': json.dumps({
                'message': error_msg,
                'request_id': context.request_id
            })
        }
