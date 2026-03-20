import json
import os
import logging
from datetime import datetime, timedelta
from decimal import Decimal
import boto3
from urllib.parse import parse_qs

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')
HISTORY_TABLE = os.environ.get('HISTORY_TABLE', 'mvt-history')


class DecimalEncoder(json.JSONEncoder):
    """Custom JSON encoder to handle Decimal objects from DynamoDB."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def parse_query_params(event):
    """
    Parse query parameters from API Gateway event.

    Args:
        event: API Gateway event

    Returns:
        Dictionary of query parameters
    """
    # Handle both queryStringParameters and rawQueryString
    query_string = event.get('queryStringParameters') or {}

    # If rawQueryString exists, parse it
    raw_query = event.get('rawQueryString', '')
    if raw_query:
        parsed = parse_qs(raw_query)
        # parse_qs returns lists, flatten to strings
        query_string = {k: v[0] if isinstance(v, list) and len(v) > 0 else v for k, v in parsed.items()}

    return query_string


def get_history(dashboard, metric, days=7):
    """
    Query history table for metric data over N days.

    Args:
        dashboard: Dashboard identifier
        metric: Metric/panel name
        days: Number of days to retrieve (default 7)

    Returns:
        List of {timestamp, value} dictionaries
    """
    try:
        table = dynamodb.Table(HISTORY_TABLE)

        # Partition key: {dashboard}#{metric}
        pk = f"{dashboard}#{metric}"

        # Calculate time range
        now = datetime.utcnow()
        start_time = (now - timedelta(days=days)).isoformat()
        end_time = now.isoformat()

        # Query with SK range
        response = table.query(
            KeyConditionExpression='dashboard_metric = :pk AND #ts BETWEEN :start AND :end',
            ExpressionAttributeNames={
                '#ts': 'timestamp'
            },
            ExpressionAttributeValues={
                ':pk': pk,
                ':start': start_time,
                ':end': end_time
            },
            ScanIndexForward=True,  # Ascending order (oldest first)
            Limit=1000
        )

        # Format results as array of {timestamp, value}
        items = []
        for item in response.get('Items', []):
            items.append({
                'timestamp': item.get('timestamp'),
                'value': float(item.get('value', 0))
            })

        logger.info(f"Retrieved {len(items)} history points for {pk}")
        return items

    except Exception as e:
        logger.error(f"Error querying history: {str(e)}")
        return []


def build_response(status_code, body):
    """
    Build API Gateway response.

    Args:
        status_code: HTTP status code
        body: Response body (dict or string)

    Returns:
        API Gateway response format
    """
    if isinstance(body, dict):
        body = json.dumps(body, cls=DecimalEncoder)

    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*'
        },
        'body': body
    }


def handler(event, context):
    """
    Lambda handler for history API.

    Endpoints:
    - GET /history?dashboard=overview&metric=distress_index&days=7

    Args:
        event: API Gateway event
        context: Lambda context

    Returns:
        API Gateway response
    """
    logger.info("History API handler triggered")
    logger.info(f"Event: {json.dumps(event, default=str)}")

    try:
        # Parse query parameters
        query_params = parse_query_params(event)

        dashboard = query_params.get('dashboard')
        metric = query_params.get('metric')
        days_str = query_params.get('days', '7')

        # Validate required parameters
        if not dashboard or not metric:
            return build_response(
                400,
                {
                    'error': 'Missing required parameters',
                    'required': ['dashboard', 'metric'],
                    'optional': ['days']
                }
            )

        # Parse and validate days parameter
        try:
            days = int(days_str)
            if days < 1 or days > 90:
                days = 7
        except (ValueError, TypeError):
            days = 7

        # Query history
        history_data = get_history(dashboard, metric, days)

        # Return results
        return build_response(
            200,
            {
                'success': True,
                'dashboard': dashboard,
                'metric': metric,
                'days': days,
                'data': history_data,
                'count': len(history_data)
            }
        )

    except Exception as e:
        logger.error(f"Unhandled error: {str(e)}")
        return build_response(
            500,
            {'error': 'Internal server error', 'message': str(e)}
        )
