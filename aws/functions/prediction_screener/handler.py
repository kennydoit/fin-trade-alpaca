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
from botocore.exceptions import ClientError

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
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                print("No existing database found in S3, will create new")
            else:
                raise
        
        # Download config file from S3
        config_s3_key = 'configs/prediction_screener.json'
        config_local_path = '/tmp/prediction_screener.json'
        try:
            s3_client.download_file(bucket_name, config_s3_key, config_local_path)
            print(f"Downloaded config from s3://{bucket_name}/{config_s3_key}")
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                print("No config found in S3, using defaults")
                config_local_path = None
            else:
                raise
        
        # Create temp directory for outputs
        output_dir = '/tmp/screener_results'
        os.makedirs(output_dir, exist_ok=True)
        
        # Download the latest equity screener results from S3
        print("Downloading latest equity screener results from S3...")
        
        # List all equity screener files in S3
        try:
            response = s3_client.list_objects_v2(
                Bucket=bucket_name,
                Prefix='screener_results/equity_screener_'
            )
            
            if 'Contents' not in response or len(response['Contents']) == 0:
                raise ValueError(
                    "No equity screener results found in S3. "
                    "Please run the equity screener first to generate candidates."
                )
            
            # Get the most recent file
            latest_file = max(response['Contents'], key=lambda x: x['LastModified'])
            candidates_s3_key = latest_file['Key']
            candidates_local_path = f"{output_dir}/candidates.csv"
            
            s3_client.download_file(bucket_name, candidates_s3_key, candidates_local_path)
            print(f"Downloaded candidates from s3://{bucket_name}/{candidates_s3_key}")
            
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                raise ValueError(
                    "No equity screener results found in S3. "
                    "Please run the equity screener first to generate candidates."
                )
            else:
                raise
        
        # Run prediction screener using the candidates
        print(f"Running prediction screener with {limit} symbols...")
        output_file = os.path.join(output_dir, 'predictions.csv')
        sys.argv = [
            'predict_screener',
            '--candidates-file', candidates_local_path,
            '--limit', str(limit),
            '--return-days', str(return_days),
            '--lookback', str(lookback),
            '--out', output_file
        ]
        
        if config_local_path:
            sys.argv.extend(['--config', config_local_path])
        
        # Execute the screener (this will write CSV to output_file)
        run_predict_screener()
        
        # Use the output file we specified
        latest_csv = Path(output_file)
        if not latest_csv.exists():
            raise ValueError(f"No prediction CSV file generated at {output_file}")
        
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
            Message=f"{error_msg}\n\nFunction: {context.function_name}\nRequest ID: {context.aws_request_id}"
        )
        
        return {
            'statusCode': 500,
            'body': json.dumps({
                'message': error_msg,
                'request_id': context.aws_request_id
            })
        }
