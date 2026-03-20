"""
MVT SRE Performance Baseline Report (S4-06)
Generates baseline metrics for all Lambdas including P50/P90/P99 latency.
Writes to dashboard-state and can optionally push to Confluence via Docs Agent.

Trigger: EventBridge schedule (every 6 hours) or on-demand
Output: Performance baseline data in dashboard-state
"""

import json
import os
import logging
from datetime import datetime, timedelta
from decimal import Decimal
import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')
cloudwatch = boto3.client('cloudwatch')

DASHBOARD_STATE_TABLE = os.environ.get('DASHBOARD_STATE_TABLE', 'mvt-dashboard-state')

MVT_FUNCTIONS = [
    'mvt-finnhub-connector', 'mvt-fred-poller', 'mvt-gdelt-querier',
    'mvt-trends-poller', 'mvt-worldbank-poller', 'mvt-yfinance-streamer',
    'mvt-sentiment-aggregator', 'mvt-inequality-scorer', 'mvt-contagion-modeler',
    'mvt-vulnerability-composite', 'mvt-cross-dashboard-router',
    'mvt-ws-connect', 'mvt-ws-disconnect', 'mvt-ws-broadcast',
    'mvt-telegram-bot', 'mvt-alert-manager',
    'mvt-cost-tracker', 'mvt-health-check'
]


def get_percentile_metrics(function_name, start_time, end_time):
    """
    Query CloudWatch for P50, P90, P99 duration metrics.

    Returns:
        dict with p50, p90, p99 duration in ms, plus invocations and error count
    """
    queries = [
        {'Id': 'p50', 'MetricStat': {
            'Metric': {'Namespace': 'AWS/Lambda', 'MetricName': 'Duration',
                       'Dimensions': [{'Name': 'FunctionName', 'Value': function_name}]},
            'Period': 86400, 'Stat': 'p50'}},
        {'Id': 'p90', 'MetricStat': {
            'Metric': {'Namespace': 'AWS/Lambda', 'MetricName': 'Duration',
                       'Dimensions': [{'Name': 'FunctionName', 'Value': function_name}]},
            'Period': 86400, 'Stat': 'p90'}},
        {'Id': 'p99', 'MetricStat': {
            'Metric': {'Namespace': 'AWS/Lambda', 'MetricName': 'Duration',
                       'Dimensions': [{'Name': 'FunctionName', 'Value': function_name}]},
            'Period': 86400, 'Stat': 'p99'}},
        {'Id': 'avg', 'MetricStat': {
            'Metric': {'Namespace': 'AWS/Lambda', 'MetricName': 'Duration',
                       'Dimensions': [{'Name': 'FunctionName', 'Value': function_name}]},
            'Period': 86400, 'Stat': 'Average'}},
        {'Id': 'max_dur', 'MetricStat': {
            'Metric': {'Namespace': 'AWS/Lambda', 'MetricName': 'Duration',
                       'Dimensions': [{'Name': 'FunctionName', 'Value': function_name}]},
            'Period': 86400, 'Stat': 'Maximum'}},
        {'Id': 'invocations', 'MetricStat': {
            'Metric': {'Namespace': 'AWS/Lambda', 'MetricName': 'Invocations',
                       'Dimensions': [{'Name': 'FunctionName', 'Value': function_name}]},
            'Period': 86400, 'Stat': 'Sum'}},
        {'Id': 'errors', 'MetricStat': {
            'Metric': {'Namespace': 'AWS/Lambda', 'MetricName': 'Errors',
                       'Dimensions': [{'Name': 'FunctionName', 'Value': function_name}]},
            'Period': 86400, 'Stat': 'Sum'}},
        {'Id': 'throttles', 'MetricStat': {
            'Metric': {'Namespace': 'AWS/Lambda', 'MetricName': 'Throttles',
                       'Dimensions': [{'Name': 'FunctionName', 'Value': function_name}]},
            'Period': 86400, 'Stat': 'Sum'}},
    ]

    try:
        response = cloudwatch.get_metric_data(
            MetricDataQueries=queries,
            StartTime=start_time,
            EndTime=end_time
        )

        metrics = {}
        for result in response.get('MetricDataResults', []):
            values = result.get('Values', [])
            if result['Id'] in ('invocations', 'errors', 'throttles'):
                metrics[result['Id']] = sum(values)
            else:
                metrics[result['Id']] = (sum(values) / len(values)) if values else 0

        return metrics
    except Exception as e:
        logger.warning(f"Failed to get percentile metrics for {function_name}: {e}")
        return {'p50': 0, 'p90': 0, 'p99': 0, 'avg': 0, 'max_dur': 0,
                'invocations': 0, 'errors': 0, 'throttles': 0}


def handler(event, context):
    """
    Generate performance baseline report for all MVT Lambdas.
    Queries 7-day window for stable percentile calculations.
    """
    logger.info("Starting performance baseline generation")

    now = datetime.utcnow()
    start = now - timedelta(days=7)

    table = dynamodb.Table(DASHBOARD_STATE_TABLE)
    baselines = []

    for fn_name in MVT_FUNCTIONS:
        try:
            metrics = get_percentile_metrics(fn_name, start, now)

            invocations = metrics.get('invocations', 0)
            errors = metrics.get('errors', 0)
            error_rate = (errors / invocations * 100) if invocations > 0 else 0

            baseline = {
                'function_name': fn_name,
                'p50_ms': round(metrics.get('p50', 0), 1),
                'p90_ms': round(metrics.get('p90', 0), 1),
                'p99_ms': round(metrics.get('p99', 0), 1),
                'avg_ms': round(metrics.get('avg', 0), 1),
                'max_ms': round(metrics.get('max_dur', 0), 1),
                'invocations_7d': int(invocations),
                'errors_7d': int(errors),
                'error_rate_7d': round(error_rate, 2),
                'throttles_7d': int(metrics.get('throttles', 0)),
                'daily_avg_invocations': int(invocations / 7) if invocations > 0 else 0
            }
            baselines.append(baseline)

            # Write per-function baseline to dashboard-state
            table.put_item(Item={
                'dashboard': 'sre_observatory',
                'metric': f'baseline_{fn_name}',
                **{k: Decimal(str(v)) if isinstance(v, (int, float)) else v for k, v in baseline.items()},
                'window_start': start.isoformat(),
                'window_end': now.isoformat(),
                'timestamp': now.isoformat(),
                'updated_at': now.isoformat()
            })

        except Exception as e:
            logger.error(f"Error generating baseline for {fn_name}: {e}")
            continue

    # Write summary baseline
    active_fns = [b for b in baselines if b['invocations_7d'] > 0]
    avg_p50 = sum(b['p50_ms'] for b in active_fns) / len(active_fns) if active_fns else 0
    avg_p90 = sum(b['p90_ms'] for b in active_fns) / len(active_fns) if active_fns else 0
    avg_p99 = sum(b['p99_ms'] for b in active_fns) / len(active_fns) if active_fns else 0
    total_invocations = sum(b['invocations_7d'] for b in baselines)
    total_errors = sum(b['errors_7d'] for b in baselines)

    summary = {
        'dashboard': 'sre_observatory',
        'metric': 'perf_baseline_summary',
        'functions_measured': Decimal(str(len(baselines))),
        'active_functions': Decimal(str(len(active_fns))),
        'fleet_p50_ms': Decimal(str(round(avg_p50, 1))),
        'fleet_p90_ms': Decimal(str(round(avg_p90, 1))),
        'fleet_p99_ms': Decimal(str(round(avg_p99, 1))),
        'total_invocations_7d': Decimal(str(int(total_invocations))),
        'total_errors_7d': Decimal(str(int(total_errors))),
        'overall_error_rate': Decimal(str(round((total_errors / total_invocations * 100) if total_invocations > 0 else 0, 2))),
        'window_start': start.isoformat(),
        'window_end': now.isoformat(),
        'timestamp': now.isoformat(),
        'updated_at': now.isoformat()
    }
    table.put_item(Item=summary)

    # Find outliers: functions with P99 > 3x their P50
    outliers = [b['function_name'] for b in active_fns if b['p50_ms'] > 0 and b['p99_ms'] > b['p50_ms'] * 3]

    logger.info(f"Performance baseline complete: {len(active_fns)} active functions, "
                f"fleet P50={avg_p50:.1f}ms P90={avg_p90:.1f}ms P99={avg_p99:.1f}ms, "
                f"{len(outliers)} outliers")

    return {
        'statusCode': 200,
        'body': json.dumps({
            'message': 'Performance baseline generated',
            'functions_measured': len(baselines),
            'active_functions': len(active_fns),
            'fleet_p50_ms': round(avg_p50, 1),
            'fleet_p90_ms': round(avg_p90, 1),
            'fleet_p99_ms': round(avg_p99, 1),
            'outlier_functions': outliers
        })
    }
