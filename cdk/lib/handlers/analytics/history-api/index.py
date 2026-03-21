"""
MVT Historical Trend API (S6-05)
REST API endpoint serving historical KPI data with date range queries,
aggregation support, and export formats.

Serves from mvt-dashboard-state and mvt-signals tables via API Gateway.

Trigger: API Gateway REST (GET /history/{kpi})
Output: JSON response with historical data points
"""

import json
import os
import logging
from datetime import datetime, timedelta
from decimal import Decimal
import boto3
from boto3.dynamodb.conditions import Key, Attr

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')

DASHBOARD_STATE_TABLE = os.environ.get('DASHBOARD_STATE_TABLE', 'mvt-dashboard-state')
SIGNALS_TABLE = os.environ.get('SIGNALS_TABLE', 'mvt-signals')

VALID_KPIS = [
    'distress_index',
    'trigger_probability',
    'contagion_risk',
    'vix_level',
    'cpi_yoy',
    'fed_rate',
    'treasury_10y',
    'unemployment',
    'm2_supply',
    'spy_price',
    'qqq_price',
    'market_sentiment',
]

VALID_AGGREGATIONS = ['none', 'hourly', 'daily', 'weekly']

# Signal type to table key mapping
SIGNAL_TYPE_MAP = {
    'distress_index': 'composite_distress',
    'trigger_probability': 'trigger_prob',
    'contagion_risk': 'contagion_risk',
    'vix_level': 'market_vix',
    'cpi_yoy': 'fred_cpi',
    'fed_rate': 'fred_fedfunds',
    'treasury_10y': 'fred_treasury10y',
    'unemployment': 'fred_unemployment',
    'm2_supply': 'fred_m2',
    'spy_price': 'yfinance_spy',
    'qqq_price': 'yfinance_qqq',
    'market_sentiment': 'finnhub_sentiment',
}


class DecimalEncoder(json.JSONEncoder):
    """Handle Decimal serialization."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            f = float(obj)
            if f == int(f):
                return int(f)
            return f
        return super().default(obj)


def parse_date(date_str, default=None):
    """
    Parse date string in various formats.
    Returns datetime or default.
    """
    if not date_str:
        return default

    formats = [
        '%Y-%m-%d',
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%dT%H:%M:%SZ',
        '%Y-%m-%dT%H:%M:%S.%fZ',
    ]

    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue

    return default


def aggregate_values(data_points, aggregation):
    """
    Aggregate data points by time bucket.
    Returns aggregated list with min/max/avg/count per bucket.
    """
    if aggregation == 'none' or not data_points:
        return data_points

    bucket_formats = {
        'hourly': '%Y-%m-%dT%H:00:00Z',
        'daily': '%Y-%m-%d',
        'weekly': None,  # Special handling
    }

    buckets = {}

    for point in data_points:
        ts_str = point.get('timestamp', '')
        value = point.get('value')
        if value is None:
            continue

        try:
            ts = datetime.fromisoformat(ts_str.replace('Z', ''))
        except (ValueError, TypeError):
            continue

        if aggregation == 'weekly':
            # Monday-based week
            week_start = ts - timedelta(days=ts.weekday())
            bucket_key = week_start.strftime('%Y-%m-%d')
        else:
            bucket_key = ts.strftime(bucket_formats[aggregation])

        if bucket_key not in buckets:
            buckets[bucket_key] = []
        buckets[bucket_key].append(float(value))

    # Build aggregated output
    result = []
    for bucket_key in sorted(buckets.keys()):
        values = buckets[bucket_key]
        result.append({
            'timestamp': bucket_key,
            'avg': round(sum(values) / len(values), 4),
            'min': round(min(values), 4),
            'max': round(max(values), 4),
            'count': len(values),
            'first': round(values[0], 4),
            'last': round(values[-1], 4),
        })

    return result


def query_dashboard_state(table, kpi, start_date, end_date, limit):
    """
    Query dashboard-state for historical KPI values.
    """
    try:
        response = table.query(
            KeyConditionExpression=Key('dashboard').eq('overview') & Key('component').begins_with(kpi),
            ScanIndexForward=True,
            Limit=min(limit, 1000)
        )
        items = response.get('Items', [])

        data_points = []
        for item in items:
            payload = item.get('payload', {})
            if isinstance(payload, str):
                payload = json.loads(payload)

            val = payload.get('value') or payload.get('current_value')
            ts = payload.get('timestamp') or item.get('updated_at', '')

            if val is not None and ts:
                try:
                    item_dt = parse_date(ts.replace('Z', ''))
                    if item_dt and start_date <= item_dt <= end_date:
                        data_points.append({
                            'timestamp': ts,
                            'value': float(val),
                        })
                except (ValueError, TypeError):
                    continue

        return data_points
    except Exception as e:
        logger.warning(f"Dashboard-state query failed for {kpi}: {e}")
        return []


def query_signals(table, signal_type, start_date, end_date, limit):
    """
    Query mvt-signals for historical raw signals.
    """
    try:
        start_iso = start_date.isoformat()
        end_iso = end_date.isoformat()

        response = table.query(
            KeyConditionExpression=Key('signalType').eq(signal_type),
            FilterExpression=Attr('timestamp').between(start_iso, end_iso),
            ScanIndexForward=True,
            Limit=min(limit, 2000)
        )
        items = response.get('Items', [])

        data_points = []
        for item in items:
            val = item.get('value')
            ts = item.get('timestamp', '')
            if val is not None:
                data_points.append({
                    'timestamp': ts,
                    'value': float(val),
                    'source': item.get('source', ''),
                })

        return data_points
    except Exception as e:
        logger.warning(f"Signals query failed for {signal_type}: {e}")
        return []


def build_response(status_code, body, headers=None):
    """Build API Gateway response with CORS headers."""
    resp = {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
        },
        'body': json.dumps(body, cls=DecimalEncoder),
    }
    if headers:
        resp['headers'].update(headers)
    return resp


def handler(event, context):
    """
    Main Lambda handler for historical trend API.

    Query parameters:
    - kpi (required): KPI name from VALID_KPIS
    - start: Start date (ISO format, default: 30 days ago)
    - end: End date (ISO format, default: now)
    - aggregation: none|hourly|daily|weekly (default: none)
    - limit: Max data points (default: 500, max: 2000)
    - source: dashboard|signals|auto (default: auto)
    """
    logger.info(f"History API request: {json.dumps(event, default=str)}")

    # Handle CORS preflight
    http_method = event.get('httpMethod', event.get('requestContext', {}).get('http', {}).get('method', 'GET'))
    if http_method == 'OPTIONS':
        return build_response(200, {'status': 'ok'})

    # Extract parameters
    path_params = event.get('pathParameters') or {}
    query_params = event.get('queryStringParameters') or {}

    kpi = path_params.get('kpi') or query_params.get('kpi')

    if not kpi:
        return build_response(400, {
            'error': 'Missing required parameter: kpi',
            'valid_kpis': VALID_KPIS,
        })

    if kpi not in VALID_KPIS:
        return build_response(400, {
            'error': f'Invalid KPI: {kpi}',
            'valid_kpis': VALID_KPIS,
        })

    # Parse query parameters
    now = datetime.utcnow()
    start_date = parse_date(query_params.get('start'), now - timedelta(days=30))
    end_date = parse_date(query_params.get('end'), now)
    aggregation = query_params.get('aggregation', 'none')
    limit = min(int(query_params.get('limit', '500')), 2000)
    source = query_params.get('source', 'auto')

    if aggregation not in VALID_AGGREGATIONS:
        return build_response(400, {
            'error': f'Invalid aggregation: {aggregation}',
            'valid_aggregations': VALID_AGGREGATIONS,
        })

    # Query data
    state_table = dynamodb.Table(DASHBOARD_STATE_TABLE)
    signals_table = dynamodb.Table(SIGNALS_TABLE)

    data_points = []

    if source in ('dashboard', 'auto'):
        data_points = query_dashboard_state(state_table, kpi, start_date, end_date, limit)

    if (source == 'signals' or (source == 'auto' and len(data_points) < 5)):
        signal_type = SIGNAL_TYPE_MAP.get(kpi, kpi)
        signal_data = query_signals(signals_table, signal_type, start_date, end_date, limit)
        if source == 'signals' or len(signal_data) > len(data_points):
            data_points = signal_data

    # Apply aggregation
    if aggregation != 'none' and data_points:
        data_points = aggregate_values(data_points, aggregation)

    # Compute summary stats
    values = []
    for dp in data_points:
        v = dp.get('value') or dp.get('avg')
        if v is not None:
            values.append(float(v))

    summary = {}
    if values:
        summary = {
            'count': len(values),
            'min': round(min(values), 4),
            'max': round(max(values), 4),
            'mean': round(sum(values) / len(values), 4),
            'first': round(values[0], 4),
            'last': round(values[-1], 4),
            'change': round(values[-1] - values[0], 4) if len(values) > 1 else 0,
            'change_pct': round((values[-1] - values[0]) / abs(values[0]) * 100, 2) if values[0] != 0 and len(values) > 1 else 0,
        }

    response_body = {
        'kpi': kpi,
        'start': start_date.isoformat() + 'Z',
        'end': end_date.isoformat() + 'Z',
        'aggregation': aggregation,
        'data_points': len(data_points),
        'summary': summary,
        'data': data_points,
    }

    return build_response(200, response_body)
