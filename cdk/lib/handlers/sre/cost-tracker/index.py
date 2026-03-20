"""
MVT SRE Cost Tracker Lambda (S4-01)
Queries CloudWatch for all mvt-* Lambda metrics and writes cost/performance
data to dashboard-state for the SRE Observatory tab.

Trigger: EventBridge schedule (every 1 hour)
Output: dashboard-state table entries under dashboard='sre_observatory'
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
lambda_client = boto3.client('lambda')

DASHBOARD_STATE_TABLE = os.environ.get('DASHBOARD_STATE_TABLE', 'mvt-dashboard-state')
EVENT_BUS_NAME = os.environ.get('EVENT_BUS_NAME')

# Lambda pricing (us-east-1)
PRICE_PER_GB_SECOND = 0.0000166667
PRICE_PER_REQUEST = 0.0000002
FREE_TIER_REQUESTS = 1_000_000
FREE_TIER_GB_SECONDS = 400_000

# All known MVT Lambda functions
MVT_FUNCTIONS = [
    # Ingestion
    'mvt-finnhub-connector',
    'mvt-fred-poller',
    'mvt-gdelt-querier',
    'mvt-trends-poller',
    'mvt-worldbank-poller',
    'mvt-yfinance-streamer',
    # Processing
    'mvt-sentiment-aggregator',
    'mvt-inequality-scorer',
    'mvt-contagion-modeler',
    'mvt-vulnerability-composite',
    'mvt-cross-dashboard-router',
    # Realtime
    'mvt-ws-connect',
    'mvt-ws-disconnect',
    'mvt-ws-broadcast',
    # Cross-cloud
    'mvt-event-relay',
    'mvt-analytics-relay',
    # History & Alerts
    'mvt-history-writer',
    'mvt-history-api',
    'mvt-alert-manager',
    # Telegram
    'mvt-telegram-bot',
    # SRE
    'mvt-cost-tracker',
    'mvt-health-check',
]


def get_function_metrics(function_name, start_time, end_time, period=3600):
    """
    Query CloudWatch for a Lambda function's key metrics.

    Args:
        function_name: Lambda function name
        start_time: Start of time window
        end_time: End of time window
        period: Aggregation period in seconds

    Returns:
        dict with invocations, errors, duration_avg, duration_p99, throttles
    """
    metrics = {}

    metric_queries = [
        {
            'Id': 'invocations',
            'MetricStat': {
                'Metric': {
                    'Namespace': 'AWS/Lambda',
                    'MetricName': 'Invocations',
                    'Dimensions': [{'Name': 'FunctionName', 'Value': function_name}]
                },
                'Period': period,
                'Stat': 'Sum'
            }
        },
        {
            'Id': 'errors',
            'MetricStat': {
                'Metric': {
                    'Namespace': 'AWS/Lambda',
                    'MetricName': 'Errors',
                    'Dimensions': [{'Name': 'FunctionName', 'Value': function_name}]
                },
                'Period': period,
                'Stat': 'Sum'
            }
        },
        {
            'Id': 'duration_avg',
            'MetricStat': {
                'Metric': {
                    'Namespace': 'AWS/Lambda',
                    'MetricName': 'Duration',
                    'Dimensions': [{'Name': 'FunctionName', 'Value': function_name}]
                },
                'Period': period,
                'Stat': 'Average'
            }
        },
        {
            'Id': 'duration_p99',
            'MetricStat': {
                'Metric': {
                    'Namespace': 'AWS/Lambda',
                    'MetricName': 'Duration',
                    'Dimensions': [{'Name': 'FunctionName', 'Value': function_name}]
                },
                'Period': period,
                'Stat': 'p99'
            }
        },
        {
            'Id': 'throttles',
            'MetricStat': {
                'Metric': {
                    'Namespace': 'AWS/Lambda',
                    'MetricName': 'Throttles',
                    'Dimensions': [{'Name': 'FunctionName', 'Value': function_name}]
                },
                'Period': period,
                'Stat': 'Sum'
            }
        },
        {
            'Id': 'concurrent',
            'MetricStat': {
                'Metric': {
                    'Namespace': 'AWS/Lambda',
                    'MetricName': 'ConcurrentExecutions',
                    'Dimensions': [{'Name': 'FunctionName', 'Value': function_name}]
                },
                'Period': period,
                'Stat': 'Maximum'
            }
        }
    ]

    try:
        response = cloudwatch.get_metric_data(
            MetricDataQueries=metric_queries,
            StartTime=start_time,
            EndTime=end_time
        )

        for result in response.get('MetricDataResults', []):
            metric_id = result['Id']
            values = result.get('Values', [])
            metrics[metric_id] = sum(values) if metric_id in ('invocations', 'errors', 'throttles') else (
                max(values) if metric_id == 'concurrent' else (
                    (sum(values) / len(values)) if values else 0
                )
            )

    except Exception as e:
        logger.warning(f"Failed to get metrics for {function_name}: {e}")
        metrics = {
            'invocations': 0, 'errors': 0, 'duration_avg': 0,
            'duration_p99': 0, 'throttles': 0, 'concurrent': 0
        }

    return metrics


def get_function_config(function_name):
    """Get Lambda function configuration (memory size)."""
    try:
        response = lambda_client.get_function_configuration(FunctionName=function_name)
        return {
            'memory_mb': response.get('MemorySize', 128),
            'timeout': response.get('Timeout', 3),
            'runtime': response.get('Runtime', 'unknown'),
            'code_size': response.get('CodeSize', 0),
            'last_modified': response.get('LastModified', '')
        }
    except Exception as e:
        logger.warning(f"Could not get config for {function_name}: {e}")
        return {'memory_mb': 128, 'timeout': 3, 'runtime': 'unknown', 'code_size': 0, 'last_modified': ''}


def estimate_cost(invocations, avg_duration_ms, memory_mb):
    """
    Estimate Lambda cost based on invocations, duration, and memory.

    Args:
        invocations: Number of invocations
        avg_duration_ms: Average duration in milliseconds
        memory_mb: Allocated memory in MB

    Returns:
        Estimated cost in USD
    """
    if invocations == 0:
        return 0.0

    # Request cost
    request_cost = invocations * PRICE_PER_REQUEST

    # Compute cost: GB-seconds
    duration_seconds = avg_duration_ms / 1000.0
    gb_seconds = (memory_mb / 1024.0) * duration_seconds * invocations
    compute_cost = gb_seconds * PRICE_PER_GB_SECOND

    return request_cost + compute_cost


def categorize_function(function_name):
    """Categorize a function into its group."""
    ingestion = ['finnhub', 'fred', 'gdelt', 'trends', 'worldbank', 'yfinance']
    processing = ['sentiment', 'inequality', 'contagion', 'vulnerability', 'dashboard-router']
    realtime = ['ws-connect', 'ws-disconnect', 'ws-broadcast']
    crosscloud = ['event-relay', 'analytics-relay']
    sre = ['cost-tracker', 'health-check']

    name_lower = function_name.lower()
    for keyword in ingestion:
        if keyword in name_lower:
            return 'ingestion'
    for keyword in processing:
        if keyword in name_lower:
            return 'processing'
    for keyword in realtime:
        if keyword in name_lower:
            return 'realtime'
    for keyword in crosscloud:
        if keyword in name_lower:
            return 'crosscloud'
    for keyword in sre:
        if keyword in name_lower:
            return 'sre'
    return 'other'


def handler(event, context):
    """
    Main Lambda handler. Queries CloudWatch for all MVT Lambda metrics
    and writes cost/performance data to dashboard-state.
    """
    logger.info("Starting cost tracker run")

    now = datetime.utcnow()
    # Query last 24 hours for daily cost
    start_24h = now - timedelta(hours=24)
    # Query last 1 hour for recent metrics
    start_1h = now - timedelta(hours=1)

    table = dynamodb.Table(DASHBOARD_STATE_TABLE)
    function_costs = {}
    group_costs = {'ingestion': 0, 'processing': 0, 'realtime': 0, 'crosscloud': 0, 'sre': 0, 'other': 0}
    total_invocations = 0
    total_errors = 0
    total_cost = 0
    function_details = []

    for fn_name in MVT_FUNCTIONS:
        try:
            # Get 24h metrics for cost calculation
            metrics_24h = get_function_metrics(fn_name, start_24h, now, period=86400)
            # Get 1h metrics for recent performance
            metrics_1h = get_function_metrics(fn_name, start_1h, now, period=3600)

            config = get_function_config(fn_name)

            invocations_24h = metrics_24h.get('invocations', 0)
            errors_24h = metrics_24h.get('errors', 0)
            avg_duration = metrics_24h.get('duration_avg', 0)
            p99_duration = metrics_24h.get('duration_p99', 0)
            throttles = metrics_24h.get('throttles', 0)

            cost = estimate_cost(invocations_24h, avg_duration, config['memory_mb'])
            category = categorize_function(fn_name)

            function_costs[fn_name] = cost
            group_costs[category] += cost
            total_invocations += invocations_24h
            total_errors += errors_24h
            total_cost += cost

            error_rate = (errors_24h / invocations_24h * 100) if invocations_24h > 0 else 0

            detail = {
                'function_name': fn_name,
                'category': category,
                'invocations_24h': int(invocations_24h),
                'errors_24h': int(errors_24h),
                'error_rate': round(error_rate, 2),
                'avg_duration_ms': round(avg_duration, 1),
                'p99_duration_ms': round(p99_duration, 1),
                'throttles': int(throttles),
                'estimated_cost_24h': round(cost, 6),
                'memory_mb': config['memory_mb'],
                'concurrent_max': int(metrics_24h.get('concurrent', 0)),
                # Recent 1h metrics
                'invocations_1h': int(metrics_1h.get('invocations', 0)),
                'errors_1h': int(metrics_1h.get('errors', 0)),
                'avg_duration_1h': round(metrics_1h.get('duration_avg', 0), 1)
            }
            function_details.append(detail)

            # Write per-function cost to dashboard-state
            table.put_item(Item={
                'dashboard': 'sre_observatory',
                'metric': f'lambda_cost_{fn_name}',
                'function_name': fn_name,
                'category': category,
                'invocations_24h': Decimal(str(int(invocations_24h))),
                'errors_24h': Decimal(str(int(errors_24h))),
                'error_rate': Decimal(str(round(error_rate, 2))),
                'avg_duration_ms': Decimal(str(round(avg_duration, 1))),
                'p99_duration_ms': Decimal(str(round(p99_duration, 1))),
                'throttles': Decimal(str(int(throttles))),
                'estimated_cost': Decimal(str(round(cost, 8))),
                'memory_mb': Decimal(str(config['memory_mb'])),
                'timestamp': now.isoformat(),
                'updated_at': now.isoformat()
            })

        except Exception as e:
            logger.error(f"Error processing {fn_name}: {e}")
            continue

    # Write summary to dashboard-state
    overall_error_rate = (total_errors / total_invocations * 100) if total_invocations > 0 else 0
    monthly_projected = total_cost * 30

    summary = {
        'dashboard': 'sre_observatory',
        'metric': 'cost_summary',
        'total_cost_24h': Decimal(str(round(total_cost, 6))),
        'monthly_projected': Decimal(str(round(monthly_projected, 4))),
        'total_invocations_24h': Decimal(str(int(total_invocations))),
        'total_errors_24h': Decimal(str(int(total_errors))),
        'overall_error_rate': Decimal(str(round(overall_error_rate, 2))),
        'group_costs': json.loads(json.dumps({k: round(v, 6) for k, v in group_costs.items()})),
        'function_count': Decimal(str(len(MVT_FUNCTIONS))),
        'functions_with_errors': Decimal(str(len([d for d in function_details if d['errors_24h'] > 0]))),
        'timestamp': now.isoformat(),
        'updated_at': now.isoformat()
    }

    table.put_item(Item=summary)

    # Write per-group summaries
    for group, cost in group_costs.items():
        group_fns = [d for d in function_details if d['category'] == group]
        group_invocations = sum(d['invocations_24h'] for d in group_fns)
        group_errors = sum(d['errors_24h'] for d in group_fns)

        table.put_item(Item={
            'dashboard': 'sre_observatory',
            'metric': f'group_{group}',
            'group_name': group,
            'total_cost_24h': Decimal(str(round(cost, 6))),
            'function_count': Decimal(str(len(group_fns))),
            'total_invocations': Decimal(str(int(group_invocations))),
            'total_errors': Decimal(str(int(group_errors))),
            'error_rate': Decimal(str(round((group_errors / group_invocations * 100) if group_invocations > 0 else 0, 2))),
            'timestamp': now.isoformat(),
            'updated_at': now.isoformat()
        })

    logger.info(f"Cost tracker complete: {len(function_details)} functions, "
                f"${total_cost:.6f}/day, ${monthly_projected:.4f}/month projected, "
                f"{total_invocations} invocations, {total_errors} errors")

    return {
        'statusCode': 200,
        'body': json.dumps({
            'message': 'Cost tracking complete',
            'total_cost_24h': round(total_cost, 6),
            'monthly_projected': round(monthly_projected, 4),
            'total_invocations': int(total_invocations),
            'total_errors': int(total_errors),
            'functions_tracked': len(function_details)
        })
    }
