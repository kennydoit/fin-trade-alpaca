"""Lambda handler for statarb pair-candidate generation workflow."""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import boto3
import pandas as pd
from botocore.exceptions import ClientError

sys.path.insert(0, "/opt/python")
sys.path.insert(0, str(Path(__file__).parent / "src"))

from runners.statarb_screener import main as run_statarb_screener


def lambda_handler(event, context):
    s3_client = boto3.client("s3")
    sns_client = boto3.client("sns")

    bucket_name = os.environ["S3_BUCKET"]
    alert_topic = os.environ["SNS_ALERT_TOPIC"]
    status_topic = os.environ["SNS_STATUS_TOPIC"]

    try:
        run_type = event.get("run_type", "scheduled")
        lookback = event.get("lookback")
        top_pairs = event.get("top_pairs")
        min_correlation = event.get("min_correlation")

        print(
            f"Starting statarb screener: run_type={run_type}, "
            f"lookback={lookback}, top_pairs={top_pairs}, min_correlation={min_correlation}"
        )

        config_s3_key = "configs/statarb_screener.json"
        config_local_path = "/tmp/statarb_screener.json"
        try:
            s3_client.download_file(bucket_name, config_s3_key, config_local_path)
            print(f"Downloaded statarb screener config from s3://{bucket_name}/{config_s3_key}")
        except ClientError as ex:
            if ex.response["Error"]["Code"] == "404":
                raise ValueError(f"Statarb screener config not found: s3://{bucket_name}/{config_s3_key}")
            raise

        output_file = "/tmp/statarb_pairs.csv"
        sys.argv = ["statarb_screener", "--config", config_local_path, "--out", output_file, "--versioned"]
        if lookback is not None:
            sys.argv.extend(["--lookback", str(lookback)])
        if top_pairs is not None:
            sys.argv.extend(["--top-pairs", str(top_pairs)])
        if min_correlation is not None:
            sys.argv.extend(["--min-correlation", str(min_correlation)])

        run_statarb_screener()

        if not os.path.exists(output_file):
            raise ValueError("No statarb pair candidates file generated.")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        s3_output_key = f"statarb_results/pairs_{timestamp}.csv"
        s3_client.upload_file(output_file, bucket_name, s3_output_key)
        print(f"Uploaded statarb pair candidates to s3://{bucket_name}/{s3_output_key}")

        df = pd.read_csv(output_file)
        pair_count = len(df)

        message = f"""Statarb Screener Completed Successfully

Run Type: {run_type}
Timestamp: {timestamp}
Pairs Generated: {pair_count}
S3 Location: s3://{bucket_name}/{s3_output_key}

Top 5 Pairs:
{df.head(5).to_string(index=False) if pair_count > 0 else 'No pairs'}
"""

        sns_client.publish(
            TopicArn=status_topic,
            Subject=f"✅ Statarb Screener Success - {pair_count} pairs",
            Message=message,
        )

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": "Statarb screener completed successfully",
                    "pair_count": pair_count,
                    "s3_location": f"s3://{bucket_name}/{s3_output_key}",
                    "timestamp": timestamp,
                }
            ),
        }

    except Exception as ex:
        error_msg = f"Statarb screener failed: {str(ex)}"
        print(error_msg)

        sns_client.publish(
            TopicArn=alert_topic,
            Subject="🚨 Statarb Screener Failed",
            Message=f"{error_msg}\n\nFunction: {context.function_name}\nRequest ID: {context.aws_request_id}",
        )

        return {
            "statusCode": 500,
            "body": json.dumps(
                {
                    "message": error_msg,
                    "request_id": context.aws_request_id,
                }
            ),
        }