"""
MVT User Activity Audit Log (S7-06)
Tracks user logins, preference changes, alert acknowledgments, and
admin actions. Provides query API for admin dashboard widget.

Trigger: API Gateway REST (GET /audit) or direct invocation
Auth: Admin role required via RBAC
Output: Audit log entries from mvt-audit-log table
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

AUDIT_TABLE = os.environ.get('AUDIT_TABLE', 'mvt-audit-log')

# Valid event types for filtering
VALID_EVENT_TYPES = [
    'auth_login', 'auth_register', 'auth_verify_email',
    'auth_forgot_password', 'auth_password_reset',
    'pref_update', 'pref_alert_threshold', 'pref_watchlist',
    'alert_acknowledged', 'alert_dismissed',
    'admin_role_change', 'admin_user_delete', 'admin_config_change',
    'api_access', 'export_data',
]

# Retention period
RETENTION_DAYS = 90


class DecimalEncoder(json.JSONEncoder):
    """Handle Decimal serialization."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            f = float(obj)
            return int(f) if f == int(f) else f
        return super().default(obj)


def build_response(status_code, body):
    """Build API Gateway response with CORS."""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, Authorization',
        },
        'body': json.dumps(body, cls=DecimalEncoder),
    }


def get_user_role(event):
    """Extract user role from authorizer context."""
    claims = event.get('requestContext', {}).get('authorizer', {}).get('claims', {})
    return claims.get('custom:role', claims.get('role', 'viewer'))


def write_audit_event(event_type, user_id, detail='', metadata=None):
    """
    Write an audit event to the log table.
    Called by other Lambda functions to record user activity.
    """
    try:
        table = dynamodb.Table(AUDIT_TABLE)
        timestamp = datetime.utcnow().isoformat() + 'Z'

        item = {
            'event_type': event_type,
            'event_id': f'{user_id}#{timestamp}',
            'username': user_id,
            'detail': detail,
            'timestamp': timestamp,
            'ttl': int((datetime.utcnow() + timedelta(days=RETENTION_DAYS)).timestamp()),
        }

        if metadata:
            item['metadata'] = json.loads(
                json.dumps(metadata),
                parse_float=lambda x: Decimal(str(x))
            )

        table.put_item(Item=item)
        return True

    except Exception as e:
        logger.error(f"Failed to write audit event: {e}")
        return False


def query_events_by_type(table, event_type, limit=50, start_key=None):
    """Query audit events by event type."""
    params = {
        'KeyConditionExpression': Key('event_type').eq(event_type),
        'ScanIndexForward': False,  # Most recent first
        'Limit': min(limit, 200),
    }

    if start_key:
        params['ExclusiveStartKey'] = start_key

    try:
        response = table.query(**params)
        return {
            'items': response.get('Items', []),
            'last_key': response.get('LastEvaluatedKey'),
        }
    except Exception as e:
        logger.error(f"Audit query failed: {e}")
        return {'items': [], 'last_key': None}


def query_events_by_user(table, username, limit=50):
    """Query audit events for a specific user (scan with filter)."""
    try:
        response = table.scan(
            FilterExpression=Attr('username').eq(username),
            Limit=min(limit, 200),
        )
        items = sorted(response.get('Items', []),
                       key=lambda x: x.get('timestamp', ''), reverse=True)
        return items[:limit]
    except Exception as e:
        logger.error(f"User audit query failed: {e}")
        return []


def get_audit_summary(table, hours=24):
    """Get summary statistics for the audit dashboard widget."""
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()

    summary = {
        'period_hours': hours,
        'total_events': 0,
        'by_type': {},
        'unique_users': set(),
        'recent_logins': 0,
        'failed_logins': 0,
        'preference_changes': 0,
        'admin_actions': 0,
    }

    try:
        # Scan recent events (limited scan for dashboard)
        for event_type in VALID_EVENT_TYPES:
            result = query_events_by_type(table, event_type, limit=100)
            items = result['items']

            # Filter by time
            recent = [i for i in items if i.get('timestamp', '') >= cutoff]

            if recent:
                summary['by_type'][event_type] = len(recent)
                summary['total_events'] += len(recent)

                for item in recent:
                    summary['unique_users'].add(item.get('username', ''))

                    if event_type == 'auth_login':
                        if item.get('success', True):
                            summary['recent_logins'] += 1
                        else:
                            summary['failed_logins'] += 1

                    if event_type.startswith('pref_'):
                        summary['preference_changes'] += 1

                    if event_type.startswith('admin_'):
                        summary['admin_actions'] += 1

        summary['unique_users'] = len(summary['unique_users'])

    except Exception as e:
        logger.error(f"Audit summary failed: {e}")

    return summary


def handle_query(event):
    """Handle GET /audit — query audit events."""
    role = get_user_role(event)
    if role != 'admin':
        return build_response(403, {'error': 'Admin access required'})

    query_params = event.get('queryStringParameters') or {}
    event_type = query_params.get('type', '')
    username = query_params.get('user', '')
    limit = min(int(query_params.get('limit', '50')), 200)
    view = query_params.get('view', 'list')  # list or summary

    table = dynamodb.Table(AUDIT_TABLE)

    if view == 'summary':
        hours = int(query_params.get('hours', '24'))
        summary = get_audit_summary(table, hours)
        return build_response(200, {'summary': summary})

    if username:
        items = query_events_by_user(table, username, limit)
        return build_response(200, {
            'events': items,
            'count': len(items),
            'filter': {'user': username},
        })

    if event_type:
        if event_type not in VALID_EVENT_TYPES:
            return build_response(400, {
                'error': f'Invalid event type: {event_type}',
                'valid_types': VALID_EVENT_TYPES,
            })
        result = query_events_by_type(table, event_type, limit)
        return build_response(200, {
            'events': result['items'],
            'count': len(result['items']),
            'filter': {'type': event_type},
            'has_more': result['last_key'] is not None,
        })

    # Default: return summary
    summary = get_audit_summary(table)
    return build_response(200, {'summary': summary})


def handle_write(event):
    """Handle POST /audit — write an audit event (internal use)."""
    body = event.get('body', '{}')
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            return build_response(400, {'error': 'Invalid JSON body'})

    event_type = body.get('event_type', '')
    user_id = body.get('user_id', '')
    detail = body.get('detail', '')
    metadata = body.get('metadata')

    if not event_type or not user_id:
        return build_response(400, {'error': 'event_type and user_id are required'})

    if event_type not in VALID_EVENT_TYPES:
        return build_response(400, {'error': f'Invalid event type: {event_type}'})

    success = write_audit_event(event_type, user_id, detail, metadata)

    if success:
        return build_response(201, {'message': 'Audit event recorded'})
    else:
        return build_response(500, {'error': 'Failed to record audit event'})


def handler(event, context):
    """Main handler — routes GET (query) and POST (write) requests."""
    logger.info(f"Audit log request: {json.dumps({k: v for k, v in event.items() if k != 'body'}, default=str)}")

    method = event.get('httpMethod', event.get('requestContext', {}).get('http', {}).get('method', 'GET'))

    if method == 'OPTIONS':
        return build_response(200, {'status': 'ok'})
    elif method == 'GET':
        return handle_query(event)
    elif method == 'POST':
        return handle_write(event)
    else:
        return build_response(405, {'error': f'Method {method} not allowed'})
