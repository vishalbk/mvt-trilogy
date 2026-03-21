"""
EV1-03: mvt-history-api Lambda
GET /api/history?dashboard=overview&metric=distress_index&range=24h&granularity=auto
Returns time-series data for sparklines, charts, and KPI change calculations.
Also supports GET /api/history/export?format=csv for data export.
"""

import json
import os
import time
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

HISTORY_TABLE = os.environ.get('HISTORY_TABLE', 'mvt-kpi-history')

# Lazy init for testability
_history_table = None

def _get_history_table():
    global _history_table
    if _history_table is None:
        dynamodb = boto3.resource('dynamodb', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
        _history_table = dynamodb.Table(HISTORY_TABLE)
    return _history_table

# Time range configurations
RANGE_CONFIG = {
    '1h': {'delta': timedelta(hours=1), 'max_points': 60, 'default_granularity': '1m'},
    '6h': {'delta': timedelta(hours=6), 'max_points': 72, 'default_granularity': '5m'},
    '24h': {'delta': timedelta(hours=24), 'max_points': 96, 'default_granularity': '15m'},
    '7d': {'delta': timedelta(days=7), 'max_points': 168, 'default_granularity': '1h'},
    '30d': {'delta': timedelta(days=30), 'max_points': 180, 'default_granularity': '4h'},
    '90d': {'delta': timedelta(days=90), 'max_points': 180, 'default_granularity': '12h'},
}

GRANULARITY_MINUTES = {
    '1m': 1,
    '5m': 5,
    '15m': 15,
    '1h': 60,
    '4h': 240,
    '12h': 720,
    '1d': 1440,
}

# CORS headers
CORS_HEADERS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Content-Type,Authorization',
    'Access-Control-Allow-Methods': 'GET,OPTIONS',
    'Content-Type': 'application/json',
}


def handler(event, context):
    """Main handler for history API requests."""
    print(f"History API request: {json.dumps(event.get('queryStringParameters', {}))}")

    # Handle CORS preflight
    http_method = event.get('httpMethod', event.get('requestContext', {}).get('http', {}).get('method', 'GET'))
    if http_method == 'OPTIONS':
        return {'statusCode': 200, 'headers': CORS_HEADERS, 'body': ''}

    # Parse path to determine if this is /api/history or /api/history/export
    path = event.get('path', event.get('rawPath', '/api/history'))
    params = event.get('queryStringParameters') or {}

    try:
        if '/export' in path:
            return handle_export(params)
        else:
            return handle_query(params)
    except Exception as e:
        print(f"History API error: {e}")
        return {
            'statusCode': 500,
            'headers': CORS_HEADERS,
            'body': json.dumps({'error': str(e)}),
        }


def handle_query(params):
    """Handle GET /api/history query."""
    dashboard = params.get('dashboard', '')
    metric = params.get('metric', '')
    time_range = params.get('range', '24h')
    granularity = params.get('granularity', 'auto')

    if not dashboard or not metric:
        return {
            'statusCode': 400,
            'headers': CORS_HEADERS,
            'body': json.dumps({
                'error': 'Missing required parameters: dashboard, metric',
                'usage': 'GET /api/history?dashboard=overview&metric=distress_index&range=24h',
            }),
        }

    if time_range not in RANGE_CONFIG:
        return {
            'statusCode': 400,
            'headers': CORS_HEADERS,
            'body': json.dumps({
                'error': f'Invalid range. Must be one of: {", ".join(RANGE_CONFIG.keys())}',
            }),
        }

    config = RANGE_CONFIG[time_range]
    if granularity == 'auto':
        granularity = config['default_granularity']

    # Calculate time bounds
    now = datetime.now(timezone.utc)
    start_time = now - config['delta']
    start_str = start_time.strftime('%Y-%m-%dT%H:%M:00Z')
    end_str = now.strftime('%Y-%m-%dT%H:%M:59Z')

    # Query DynamoDB
    pk = f"{dashboard}#{metric}"
    try:
        response = _get_history_table().query(
            KeyConditionExpression=Key('pk').eq(pk) & Key('sk').between(start_str, end_str),
            ScanIndexForward=True,  # Ascending by time
            Limit=2000,  # Safety limit
        )
        items = response.get('Items', [])

        # Handle pagination if needed
        while 'LastEvaluatedKey' in response and len(items) < 5000:
            response = _get_history_table().query(
                KeyConditionExpression=Key('pk').eq(pk) & Key('sk').between(start_str, end_str),
                ScanIndexForward=True,
                Limit=2000,
                ExclusiveStartKey=response['LastEvaluatedKey'],
            )
            items.extend(response.get('Items', []))

    except Exception as e:
        print(f"DynamoDB query error: {e}")
        items = []

    # Downsample if needed
    granularity_mins = GRANULARITY_MINUTES.get(granularity, 15)
    downsampled = downsample(items, granularity_mins, config['max_points'])

    # Calculate change percentage (current vs first point)
    change_pct = None
    if len(downsampled) >= 2:
        first_val = downsampled[0]['value']
        last_val = downsampled[-1]['value']
        if first_val and first_val != 0:
            change_pct = round(((last_val - first_val) / first_val) * 100, 2)

    return {
        'statusCode': 200,
        'headers': CORS_HEADERS,
        'body': json.dumps({
            'dashboard': dashboard,
            'metric': metric,
            'range': time_range,
            'granularity': granularity,
            'point_count': len(downsampled),
            'change_pct': change_pct,
            'data': downsampled,
        }, default=decimal_serializer),
    }


def handle_export(params):
    """Handle GET /api/history/export?format=csv."""
    export_format = params.get('format', 'json')
    dashboard = params.get('dashboard', '')
    metric = params.get('metric', '')
    time_range = params.get('range', '24h')

    if not dashboard or not metric:
        return {
            'statusCode': 400,
            'headers': CORS_HEADERS,
            'body': json.dumps({'error': 'Missing required parameters: dashboard, metric'}),
        }

    config = RANGE_CONFIG.get(time_range, RANGE_CONFIG['24h'])
    now = datetime.now(timezone.utc)
    start_time = now - config['delta']
    pk = f"{dashboard}#{metric}"

    response = _get_history_table().query(
        KeyConditionExpression=Key('pk').eq(pk) & Key('sk').between(
            start_time.strftime('%Y-%m-%dT%H:%M:00Z'),
            now.strftime('%Y-%m-%dT%H:%M:59Z')
        ),
        ScanIndexForward=True,
        Limit=5000,
    )
    items = response.get('Items', [])

    if export_format == 'csv':
        lines = ['timestamp,value']
        for item in items:
            ts = item.get('timestamp', item.get('sk', ''))
            val = item.get('value', '')
            lines.append(f"{ts},{val}")
        body = '\n'.join(lines)
        headers = {**CORS_HEADERS, 'Content-Type': 'text/csv',
                    'Content-Disposition': f'attachment; filename=mvt_{dashboard}_{metric}_{time_range}.csv'}
        return {'statusCode': 200, 'headers': headers, 'body': body}
    else:
        data = [{'timestamp': item.get('sk', ''), 'value': float(item.get('value', 0))} for item in items]
        return {
            'statusCode': 200,
            'headers': CORS_HEADERS,
            'body': json.dumps({'dashboard': dashboard, 'metric': metric, 'data': data}, default=decimal_serializer),
        }


def downsample(items, granularity_mins, max_points):
    """Downsample time-series data by averaging within time buckets."""
    if not items:
        return []

    if len(items) <= max_points:
        # No downsampling needed — just format
        result = []
        for item in items:
            result.append({
                'timestamp': item.get('sk', item.get('timestamp', '')),
                'value': float(item.get('value', 0)),
            })
        return result

    # Group by time bucket
    buckets = {}
    for item in items:
        ts_str = item.get('sk', item.get('timestamp', ''))
        try:
            ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
            # Round down to bucket
            total_mins = ts.hour * 60 + ts.minute
            bucket_mins = (total_mins // granularity_mins) * granularity_mins
            bucket_ts = ts.replace(hour=bucket_mins // 60, minute=bucket_mins % 60, second=0)
            bucket_key = bucket_ts.strftime('%Y-%m-%dT%H:%M:00Z')

            if bucket_key not in buckets:
                buckets[bucket_key] = []
            buckets[bucket_key].append(float(item.get('value', 0)))
        except (ValueError, TypeError):
            continue

    # Average each bucket
    result = []
    for ts_key in sorted(buckets.keys()):
        values = buckets[ts_key]
        avg_value = sum(values) / len(values)
        result.append({
            'timestamp': ts_key,
            'value': round(avg_value, 4),
        })

    # Final cap at max_points (take evenly spaced samples)
    if len(result) > max_points:
        step = len(result) / max_points
        sampled = []
        for i in range(max_points):
            idx = int(i * step)
            sampled.append(result[idx])
        result = sampled

    return result


def decimal_serializer(obj):
    """JSON serializer for Decimal types."""
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Type {type(obj)} not serializable")
