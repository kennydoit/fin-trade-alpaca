"""Lambda handler for the statarb optimize/buy workflow."""

import json
import os
import re
import sys
from contextlib import redirect_stdout
from datetime import datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, "/opt/python")
sys.path.insert(0, str(Path(__file__).parent / "src"))

from runners.statarb_optimize_buy import main as run_statarb_optimize_buy

PAIR_LINE_RE = re.compile(r"^PAIR PLAN:\s+")
LEG_LINE_RE = re.compile(r"^\s*(BUY|SELL)\s+[^:]+:\s+\$(?P<notional>\d+(?:\.\d+)?)")
PAIR_LEGS_RE = re.compile(
    r"long_leg=\$(?P<long_notional>\d+(?:\.\d+)?)\s+short_leg=\$(?P<short_notional>\d+(?:\.\d+)?)"
)
SUBMITTED_LEG_RE = re.compile(r"Submitted\s+(BUY|SELL)\s+\S+\s+order\s+id=")
LIQUIDATE_PLAN_RE = re.compile(r"^LIQUIDATE PLAN:\s+")


def get_alpaca_credentials(secrets_client, secret_name):
    response = secrets_client.get_secret_value(SecretId=secret_name)
    secret = json.loads(response["SecretString"])
    return secret["key"], secret["secret"]


def summarize_runner_output(output_text):
    planned_pair_count = 0
    planned_leg_count = 0
    planned_total_notional = Decimal("0.00")
    submitted_leg_count = 0
    counted_from_pair_lines = False
    liquidation_pair_count = 0

    for line in output_text.splitlines():
        if PAIR_LINE_RE.match(line):
            planned_pair_count += 1
            pair_match = PAIR_LEGS_RE.search(line)
            if pair_match:
                counted_from_pair_lines = True
                planned_leg_count += 2
                planned_total_notional += Decimal(pair_match.group("long_notional"))
                planned_total_notional += Decimal(pair_match.group("short_notional"))
                continue

        if not counted_from_pair_lines:
            leg_match = LEG_LINE_RE.match(line)
            if leg_match:
                planned_leg_count += 1
                planned_total_notional += Decimal(leg_match.group("notional"))

        if SUBMITTED_LEG_RE.search(line):
            submitted_leg_count += 1

        if LIQUIDATE_PLAN_RE.match(line):
            liquidation_pair_count += 1

    return {
        "planned_pair_count": planned_pair_count,
        "planned_leg_count": planned_leg_count,
        "planned_total_notional": planned_total_notional,
        "submitted_leg_count": submitted_leg_count,
        "liquidation_pair_count": liquidation_pair_count,
    }


def lambda_handler(event, context):
    s3_client = boto3.client("s3")
    secrets_client = boto3.client("secretsmanager")
    sns_client = boto3.client("sns")

    bucket_name = os.environ["S3_BUCKET"]
    alert_topic = os.environ["SNS_ALERT_TOPIC"]
    status_topic = os.environ["SNS_STATUS_TOPIC"]
    trading_enabled = os.environ.get("TRADING_ENABLED", "false").lower() == "true"

    try:
        run_type = event.get("run_type", "scheduled")
        mode = event.get("mode", "paper")
        dry_run = event.get("dry_run", False)
        max_pairs = event.get("max_pairs")

        print(f"Starting statarb optimize and buy: run_type={run_type}, mode={mode}, dry_run={dry_run}, trading_enabled={trading_enabled}")

        if not trading_enabled and not dry_run:
            raise ValueError("Trading is disabled (TRADING_ENABLED=false). Set to true to enable trading.")

        if mode == "live" and not event.get("confirm_live", False):
            raise ValueError("Live mode requires 'confirm_live': true in event payload")

        config_s3_key = "configs/statarb_strategy.json"
        config_local_path = "/tmp/statarb_strategy.json"
        try:
            s3_client.download_file(bucket_name, config_s3_key, config_local_path)
            print(f"Downloaded statarb config from s3://{bucket_name}/{config_s3_key}")
        except ClientError as ex:
            if ex.response["Error"]["Code"] == "404":
                raise ValueError(f"Statarb config not found: s3://{bucket_name}/{config_s3_key}")
            raise

        pairs_prefix = os.environ.get("STATARB_RESULTS_PREFIX", "statarb_results/pairs_")
        pairs_local_path = "/tmp/statarb_pairs.csv"
        try:
            response = s3_client.list_objects_v2(Bucket=bucket_name, Prefix=pairs_prefix)
            if "Contents" not in response or len(response["Contents"]) == 0:
                raise ValueError("No statarb pair files found in S3. Run the statarb screener first.")

            latest_file = max(response["Contents"], key=lambda item: item["LastModified"])
            pairs_s3_key = latest_file["Key"]
            s3_client.download_file(bucket_name, pairs_s3_key, pairs_local_path)
            print(f"Downloaded statarb pairs from s3://{bucket_name}/{pairs_s3_key}")
        except ClientError as ex:
            if ex.response["Error"]["Code"] == "404":
                raise ValueError("No statarb pair candidates found in S3. Run the statarb screener first.")
            raise

        secret_name = os.environ["STATARB_LIVE_SECRET_NAME"] if mode == "live" else os.environ["STATARB_PAPER_SECRET_NAME"]
        api_key, api_secret = get_alpaca_credentials(secrets_client, secret_name)

        os.environ["ALPACA_PAPER_API_KEY" if mode == "paper" else "ALPACA_LIVE_API_KEY"] = api_key
        os.environ["ALPACA_PAPER_API_SECRET" if mode == "paper" else "ALPACA_LIVE_API_SECRET"] = api_secret

        sys.argv = ["statarb_optimize_buy", "--mode", mode, "--run-type", run_type, "--config", config_local_path, "--pairs-csv", pairs_local_path]
        if dry_run:
            sys.argv.append("--dry-run")
        if max_pairs:
            sys.argv.extend(["--max-pairs", str(max_pairs)])
        if mode == "live":
            sys.argv.append("--confirm-live")

        stdout_buffer = StringIO()
        with redirect_stdout(stdout_buffer):
            result = run_statarb_optimize_buy()
        runner_output = stdout_buffer.getvalue()
        if runner_output:
            print(runner_output, end="" if runner_output.endswith("\n") else "\n")
        summary = summarize_runner_output(runner_output)

        planned_pair_count = summary["planned_pair_count"]
        planned_leg_count = summary["planned_leg_count"]
        submitted_leg_count = summary["submitted_leg_count"]
        liquidation_pair_count = summary["liquidation_pair_count"]
        total_notional = float(summary["planned_total_notional"])
        order_count = planned_leg_count if dry_run else submitted_leg_count

        message = f"""Statarb Optimize and Buy {'(DRY RUN)' if dry_run else 'Completed'}

Mode: {mode.upper()}
Run Type: {run_type}
Trading Enabled: {trading_enabled}
Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Pairs Planned: {planned_pair_count}
Pairs Marked For Liquidation: {liquidation_pair_count}
Legs {('Planned' if dry_run else 'Submitted')}: {order_count}
Planned Notional: ${total_notional:.2f}

{'⚠️ DRY RUN - No actual orders submitted' if dry_run else '✅ Orders submitted successfully'}
"""

        sns_client.publish(
            TopicArn=status_topic if dry_run else alert_topic,
            Subject=f"{'🧪' if dry_run else '💰'} Statarb Optimize & Buy {mode.upper()} - {order_count} legs",
            Message=message,
        )

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": f"Statarb optimize and buy completed {'(dry run)' if dry_run else 'successfully'}",
                    "mode": mode,
                    "pair_count": planned_pair_count,
                    "planned_leg_count": planned_leg_count,
                    "submitted_leg_count": submitted_leg_count,
                    "liquidation_pair_count": liquidation_pair_count,
                    "total_notional": total_notional,
                    "dry_run": dry_run,
                    "timestamp": datetime.now().isoformat(),
                }
            ),
        }

    except Exception as ex:
        error_msg = f"Statarb optimize and buy failed: {str(ex)}"
        print(error_msg)
        sns_client.publish(
            TopicArn=alert_topic,
            Subject=f"🚨 Statarb Optimize & Buy Failed ({mode.upper()})",
            Message=f"{error_msg}\n\nFunction: {context.function_name}\nRequest ID: {context.aws_request_id}",
        )

        return {
            "statusCode": 500,
            "body": json.dumps(
                {
                    "message": error_msg,
                    "mode": mode,
                    "request_id": context.aws_request_id,
                }
            ),
        }