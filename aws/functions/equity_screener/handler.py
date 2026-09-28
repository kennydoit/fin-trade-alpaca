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
import pandas as pd
from botocore.exceptions import ClientError

# Add source code to path
sys.path.insert(0, '/opt/python')  # Lambda layer
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from runners.yfinance_growth_screener import main as run_growth_screener


def _pick_first_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for col in candidates:
        if col in df.columns:
            return col
    return None


def _format_value(value: object) -> str:
    if pd.isna(value):
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _pick_sort_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for col in candidates:
        if col in df.columns:
            return col
    return None


def _value_counts_lines(df: pd.DataFrame, column: str, label: str) -> list[str]:
    if column not in df.columns:
        return [f"{label}: unavailable (missing '{column}' column)"]

    normalized = df[column].fillna("Unknown").astype(str).str.strip()
    normalized = normalized.mask(normalized == "", "Unknown")
    counts = normalized.value_counts(dropna=False)

    lines = [f"{label}:"]
    for key, count in counts.items():
        lines.append(f"- {key}: {int(count)}")
    return lines


def _top_assets_by_column_lines(
    df: pd.DataFrame,
    symbol_col: str,
    label: str,
    sort_candidates: list[str],
    limit: int = 10,
    ascending: bool = False,
) -> list[str]:
    sort_col = _pick_sort_column(df, sort_candidates)
    if sort_col is None:
        return [f"{label}: unavailable (no supported columns found)"]

    ranked = df[[symbol_col, sort_col]].copy()
    ranked[sort_col] = pd.to_numeric(ranked[sort_col], errors="coerce")
    ranked = ranked.dropna(subset=[sort_col]).sort_values(sort_col, ascending=ascending).head(limit)

    if ranked.empty:
        return [f"{label}: unavailable (no numeric values in '{sort_col}')"]

    lines = [f"{label} (by {sort_col}):"]
    for idx, (_, row) in enumerate(ranked.iterrows(), start=1):
        lines.append(f"- {idx}. {row[symbol_col]}: {_format_value(row[sort_col])}")
    return lines


def _build_equity_email_report(df: pd.DataFrame, config: dict[str, object] | None = None) -> str:
    if df.empty:
        return "No candidates found."

    config = config or {}

    symbol_col = _pick_first_column(df, ["symbol", "ticker", "asset"])
    if symbol_col is None:
        symbol_col = df.columns[0]

    configured_sectors = config.get("sectors") if isinstance(config.get("sectors"), list) else []
    configured_industry_choices = (
        config.get("industry_choices") if isinstance(config.get("industry_choices"), list) else []
    )
    configured_industry_filter = config.get("industry")

    observed_sectors_series = pd.Series(dtype="object")
    if "sector" in df.columns:
        observed_sectors_series = df["sector"].fillna("Unknown").astype(str).str.strip()
        observed_sectors_series = observed_sectors_series.mask(observed_sectors_series == "", "Unknown")

    observed_sectors = sorted(set(observed_sectors_series.tolist())) if not observed_sectors_series.empty else []
    configured_sector_set = {str(item).strip() for item in configured_sectors if str(item).strip()}
    observed_sector_set = {str(item).strip() for item in observed_sectors if str(item).strip() and item != "Unknown"}

    missing_configured_sectors = sorted(configured_sector_set - observed_sector_set)

    lines = [
        "Universe Summary:",
        f"- number of assets pulled: {len(df)}",
        f"- number sectors pulled: {len(observed_sector_set) if observed_sector_set else len(observed_sectors)}",
        "",
        "Configured Universe Verification:",
        f"- configured sectors count: {len(configured_sectors)}",
        f"- configured sectors: {', '.join(configured_sectors) if configured_sectors else 'n/a'}",
        f"- configured industry choices count: {len(configured_industry_choices)}",
        (
            f"- configured industry filter: {configured_industry_filter}"
            if configured_industry_filter not in (None, "")
            else "- configured industry filter: all configured industry choices"
        ),
        f"- observed sectors in pulled assets: {', '.join(observed_sectors) if observed_sectors else 'n/a'}",
        (
            f"- configured sectors missing from this run: {', '.join(missing_configured_sectors)}"
            if missing_configured_sectors
            else "- configured sectors missing from this run: none"
        ),
        "",
    ]

    lines.extend(_value_counts_lines(df, "sector", "number of assets for each sector"))
    lines.append("")
    lines.extend(_value_counts_lines(df, "industry", "number of assets for each industry"))
    lines.append("")

    lines.extend(
        _top_assets_by_column_lines(
            df,
            symbol_col,
            "top 10 assets by volume",
            ["regularMarketVolume", "averageDailyVolume3Month", "averageDailyVolume", "avgVolume", "volume", "rel_volume"],
            limit=10,
            ascending=False,
        )
    )
    lines.append("")
    lines.extend(
        _top_assets_by_column_lines(
            df,
            symbol_col,
            "top 10 assets by score",
            ["score_norm", "score_raw", "pct_1m", "pct_1w", "revenueGrowth"],
            limit=10,
            ascending=False,
        )
    )
    lines.append("")

    top_symbol_preview = [str(v) for v in df.head(10)[symbol_col].tolist()]
    lines.append(f"top 10 ranked symbols from screener output: {', '.join(top_symbol_preview)}")

    return "\n".join(lines).strip()


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
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                raise ValueError(f"Config file not found: s3://{bucket_name}/{config_s3_key}")
            else:
                raise

        with open(config_local_path, 'r', encoding='utf-8') as config_file:
            screener_config = json.load(config_file)
        
        # Create temp directory for outputs
        output_dir = '/tmp/screener_results'
        os.makedirs(output_dir, exist_ok=True)
        
        # Run equity screener
        sys.argv = [
            'yfinance_growth_screener',
            '--config', config_local_path,
            '--out', output_dir + '/equity_screener.csv'
        ]
        
        # Execute the screener
        run_growth_screener()
        
        # Find the deduplicated CSV file (most recent)
        # Screener creates: equity_screener_YYYYMMDD_ranked_deduped_YYYYMMDD.csv
        csv_files = list(Path(output_dir).glob('*_ranked_deduped_*.csv'))
        if not csv_files:
            # Fallback to ranked file if dedup not enabled
            csv_files = list(Path(output_dir).glob('*_ranked.csv'))
        if not csv_files:
            # Final fallback to any equity screener file
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
        df = pd.read_csv(latest_csv)
        candidate_count = len(df)
        formatted_report = _build_equity_email_report(df, config=screener_config)
        
        # Send status notification
        message = f"""Equity Screener Completed Successfully

Run Type: {run_type}
Timestamp: {timestamp}
Candidates Found: {candidate_count}
S3 Location: s3://{bucket_name}/{s3_output_key}

Detailed Report:
{formatted_report}
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
            Message=f"{error_msg}\n\nFunction: {context.function_name}\nRequest ID: {context.aws_request_id}"
        )
        
        return {
            'statusCode': 500,
            'body': json.dumps({
                'message': error_msg,
                'request_id': context.aws_request_id
            })
        }
