"""
MVT SRE Capacity Planning View (S4-07)
Monitors DynamoDB read/write capacity utilization, Lambda concurrent execution
trends, and projects costs at current growth rate.

Trigger: EventBridge schedule (every 6 hours) or on-demand
Output: Capacity metrics and projections in dashboard-state
"""

import json
import os
import logging
from datetime import datetime, timedelta
from decimal import Decimal
import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb_resource = boto3.resource('dynamodb')
dynamodb_client = boto3.client('dynamodb')
cloudwatch = boto3.client('cloudwatch')

DASHBOARD_STATE_TABLE = os.environ.get('DASHBOARD_STATE_TABLE', 'mvt-dashboard-state')

DYNAMO_TABLES = ['mvt-signals', 'mvt-dashboard-state', 'mvt-connections', 'mvt-history']

# Lambda pricing (us-east-1)
PRICE_PER_GB_SECOND = 0.0000166667
PRICE_PER_REQUEST = 0.0000002


def get_dynamodb_capacity(table_name, start_time, end_time):
    """
    Get DynamoDB read/write capacity utilization for a table.

    Returns dict with read_units, write_units, throttled_reads, throttled_writes.
    """
    queries = [
        {'Id': 'read_units', 'MetricStat': {
            'Metric': {'Namespace': 'AWS/DynamoDB', 'MetricName': 'ConsumedReadCapacityUnits',
                       'Dimensions': [{'Name': 'TableName', 'Value': table_name}]},
            'Period': 3600, 'Stat': 'Sum'}},
        {'Id': 'write_units', 'MetricStat': {
            'Metric': {'Namespace': 'AWS/DynamoDB', 'MetricName': 'ConsumedWriteCapacityUnits',
                       'Dimensions': [{'Name': 'TableName', 'Value': table_name}]},
            'Period': 3600, 'Stat': 'Sum'}},
        {'Id': 'throttled_reads', 'MetricStat': {
            'Metric': {'Namespace': 'AWS/DynamoDB', 'MetricName': 'ReadThrottleEvents',
                       'Dimensions': [{'Name': 'TableName', 'Value': table_name}]},
            'Period': 3600, 'Stat': 'Sum'}},
        {'Id': 'throttled_writes', 'MetricStat': {
            'Metric': {'Namespace': 'AWS/DynamoDB', 'MetricName': 'WriteThrottleEvents',
                       'Dimensions': [{'Name': 'TableName', 'Value': table_name}]},
            'Period': 3600, 'Stat': 'Sum'}},
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
            metrics[result['Id']] = sum(values)

        return metrics
    except Exception as e:
        logger.warning(f"Failed to get DynamoDB capacity for {table_name}: {e}")
        return {'read_units': 0, 'write_units': 0, 'throttled_reads': 0, 'throttled_writes': 0}


def get_lambda_concurrency(start_time, end_time):
    """Get account-level Lambda concurrent executions."""
    try:
        response = cloudwatch.get_metric_data(
            MetricDataQueries=[
                {'Id': 'concurrent', 'MetricStat': {
                    'Metric': {'Namespace': 'AWS/Lambda', 'MetricName': 'ConcurrentExecutions'},
                    'Period': 3600, 'Stat': 'Maximum'}},
            ],
            StartTime=start_time,
            EndTime=end_time
        )

        values = []
        for result in response.get('MetricDataResults', []):
            if result['Id'] == 'concurrent':
                values = result.get('Values', [])

        return {
            'max_concurrent': max(values) if values else 0,
            'avg_concurrent': (sum(values) / len(values)) if values else 0,
            'data_points': len(values),
            'hourly_values': [round(v, 1) for v in values[:24]]  # Last 24 data points
        }
    except Exception as e:
        logger.warning(f"Failed to get Lambda concurrency: {e}")
        return {'max_concurrent': 0, 'avg_concurrent': 0, 'data_points': 0, 'hourly_values': []}


def get_table_size_trend(table_name):
    """Get DynamoDB table size and item count."""
    try:
        response = dynamodb_client.describe_table(TableName=table_name)
        table = response['Table']
        return {
            'item_count': table.get('ItemCount', 0),
            'size_bytes': table.get('TableSizeBytes', 0),
            'size_mb': round(table.get('TableSizeBytes', 0) / (1024 * 1024), 2),
            'billing_mode': table.get('BillingModeSummary', {}).get('BillingMode', 'PAY_PER_REQUEST')
        }
    except Exception as e:
        logger.warning(f"Failed to get table size for {table_name}: {e}")
        return {'item_count': 0, 'size_bytes': 0, 'size_mb': 0, 'billing_mode': 'unknown'}


def project_costs(current_daily_cost, days_of_data=7):
    """
    Project future costs based on current daily spend.

    Returns monthly, quarterly, and yearly projections.
    """
    monthly = current_daily_cost * 30
    quarterly = current_daily_cost * 90
    yearly = current_daily_cost * 365

    # Assume 15% growth per month for conservative projection
    growth_rate = 0.15
    monthly_with_growth = monthly * (1 + growth_rate)
    quarterly_with_growth = sum(monthly * (1 + growth_rate) ** i for i in range(3))
    yearly_with_growth = sum(monthly * (1 + growth_rate) ** i for i in range(12))

    return {
        'daily': round(current_daily_cost, 4),
        'monthly_flat': round(monthly, 2),
        'quarterly_flat': round(quarterly, 2),
        'yearly_flat': round(yearly, 2),
        'monthly_with_growth': round(monthly_with_growth, 2),
        'quarterly_with_growth': round(quarterly_with_growth, 2),
        'yearly_with_growth': round(yearly_with_growth, 2),
        'growth_rate_assumed': growth_rate
    }


def handler(event, context):
    """
    Generate capacity planning report.
    Analyzes DynamoDB utilization, Lambda concurrency, and cost projections.
    """
    logger.info("Starting capacity planning analysis")

    now = datetime.utcnow()
    start_24h = now - timedelta(hours=24)
    start_7d = now - timedelta(days=7)

    table = dynamodb_resource.Table(DASHBOARD_STATE_TABLE)

    # === DynamoDB Capacity Analysis ===
    dynamo_capacity = {}
    total_read_units = 0
    total_write_units = 0
    total_throttles = 0

    for tbl_name in DYNAMO_TABLES:
        capacity = get_dynamodb_capacity(tbl_name, start_24h, now)
        size_info = get_table_size_trend(tbl_name)

        dynamo_capacity[tbl_name] = {
            **capacity,
            **size_info,
            'avg_reads_per_hour': round(capacity['read_units'] / 24, 1),
            'avg_writes_per_hour': round(capacity['write_units'] / 24, 1)
        }

        total_read_units += capacity['read_units']
        total_write_units += capacity['write_units']
        total_throttles += capacity['throttled_reads'] + capacity['throttled_writes']

        # Write per-table capacity to dashboard-state
        table.put_item(Item={
            'dashboard': 'sre_observatory',
            'panel': f'capacity_{tbl_name}',
            'table_name': tbl_name,
            'read_units_24h': Decimal(str(int(capacity['read_units']))),
            'write_units_24h': Decimal(str(int(capacity['write_units']))),
            'throttled_reads': Decimal(str(int(capacity['throttled_reads']))),
            'throttled_writes': Decimal(str(int(capacity['throttled_writes']))),
            'item_count': Decimal(str(size_info['item_count'])),
            'size_mb': Decimal(str(size_info['size_mb'])),
            'billing_mode': size_info['billing_mode'],
            'timestamp': now.isoformat(),
            'updated_at': now.isoformat()
        })

    # === Lambda Concurrency Analysis ===
    concurrency = get_lambda_concurrency(start_24h, now)
    concurrency_7d = get_lambda_concurrency(start_7d, now)

    # Account limit is 1000 by default
    ACCOUNT_CONCURRENCY_LIMIT = 1000
    utilization_pct = (concurrency['max_concurrent'] / ACCOUNT_CONCURRENCY_LIMIT * 100)

    table.put_item(Item={
        'dashboard': 'sre_observatory',
        'panel': 'lambda_concurrency',
        'max_concurrent_24h': Decimal(str(int(concurrency['max_concurrent']))),
        'avg_concurrent_24h': Decimal(str(round(concurrency['avg_concurrent'], 1))),
        'max_concurrent_7d': Decimal(str(int(concurrency_7d['max_concurrent']))),
        'account_limit': Decimal(str(ACCOUNT_CONCURRENCY_LIMIT)),
        'utilization_pct': Decimal(str(round(utilization_pct, 1))),
        'timestamp': now.isoformat(),
        'updated_at': now.isoformat()
    })

    # === Cost Projections ===
    # Read current daily cost from cost-tracker output
    try:
        cost_response = table.get_item(Key={
            'dashboard': 'sre_observatory',
            'panel': 'cost_summary'
        })
        daily_cost = float(cost_response['Item'].get('total_cost_24h', 0)) if 'Item' in cost_response else 0
    except Exception:
        daily_cost = 0

    projections = project_costs(daily_cost)

    table.put_item(Item={
        'dashboard': 'sre_observatory',
        'panel': 'cost_projections',
        **{k: Decimal(str(v)) if isinstance(v, (int, float)) else v for k, v in projections.items()},
        'timestamp': now.isoformat(),
        'updated_at': now.isoformat()
    })

    # === Summary ===
    total_items = sum(dynamo_capacity[t].get('item_count', 0) for t in DYNAMO_TABLES)
    total_size_mb = sum(dynamo_capacity[t].get('size_mb', 0) for t in DYNAMO_TABLES)

    summary = {
        'dashboard': 'sre_observatory',
        'panel': 'capacity_summary',
        'dynamo_total_read_units_24h': Decimal(str(int(total_read_units))),
        'dynamo_total_write_units_24h': Decimal(str(int(total_write_units))),
        'dynamo_total_throttles_24h': Decimal(str(int(total_throttles))),
        'dynamo_total_items': Decimal(str(int(total_items))),
        'dynamo_total_size_mb': Decimal(str(round(total_size_mb, 2))),
        'lambda_max_concurrent': Decimal(str(int(concurrency['max_concurrent']))),
        'lambda_utilization_pct': Decimal(str(round(utilization_pct, 1))),
        'projected_monthly_cost': Decimal(str(projections['monthly_flat'])),
        'projected_yearly_cost': Decimal(str(projections['yearly_flat'])),
        'timestamp': now.isoformat(),
        'updated_at': now.isoformat()
    }
    table.put_item(Item=summary)

    logger.info(f"Capacity planning complete: {total_items} items across {len(DYNAMO_TABLES)} tables, "
                f"{total_size_mb:.2f}MB total, {concurrency['max_concurrent']} max concurrent, "
                f"${projections['monthly_flat']}/month projected")

    return {
        'statusCode': 200,
        'body': json.dumps({
            'message': 'Capacity planning complete',
            'dynamo_tables': len(DYNAMO_TABLES),
            'total_items': int(total_items),
            'total_size_mb': round(total_size_mb, 2),
            'max_concurrent_executions': int(concurrency['max_concurrent']),
            'concurrency_utilization_pct': round(utilization_pct, 1),
            'projected_monthly_cost': projections['monthly_flat']
        })
    }
