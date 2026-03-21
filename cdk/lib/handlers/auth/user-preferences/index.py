"""
MVT User Preferences API (S7-02)
CRUD operations for user preferences including alert thresholds,
notification settings, dashboard layout, and display options.

Trigger: API Gateway REST (GET/PUT /preferences)
Auth: JWT token validation via Cognito authorizer
Output: User preference object from mvt-user-preferences table
"""

import json
import os
import logging
from datetime import datetime
from decimal import Decimal
import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')

PREFERENCES_TABLE = os.environ.get('PREFERENCES_TABLE', 'mvt-user-preferences')

# Default preferences for new users
DEFAULT_PREFERENCES = {
    'alerts': {
        'distress_index_threshold': 70,
        'trigger_probability_threshold': 60,
        'contagion_risk_threshold': 65,
        'vix_level_threshold': 30,
        'enabled': True,
        'channels': ['dashboard'],  # dashboard, telegram, email
        'quiet_hours': {'start': '22:00', 'end': '07:00', 'enabled': False},
    },
    'dashboard': {
        'default_tab': 'overview',
        'refresh_interval': 30,  # seconds
        'theme': 'dark',
        'compact_mode': False,
        'show_sparklines': True,
        'show_predictions': True,
    },
    'notifications': {
        'email_digest': 'daily',  # none, daily, weekly
        'telegram_enabled': False,
        'telegram_chat_id': '',
        'browser_notifications': True,
    },
    'watchlists': {
        'countries': [],
        'sectors': [],
        'kpis': ['distress_index', 'trigger_probability', 'contagion_risk', 'vix_level'],
    },
}


def build_response(status_code, body):
    """Build API Gateway response with CORS."""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, PUT, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, Authorization',
        },
        'body': json.dumps(body, default=str),
    }


def get_user_id(event):
    """Extract user ID from JWT claims in the API Gateway event."""
    claims = event.get('requestContext', {}).get('authorizer', {}).get('claims', {})
    user_id = claims.get('sub') or claims.get('cognito:username', '')

    if not user_id:
        # Fallback: check Authorization header or query param
        headers = event.get('headers', {}) or {}
        user_id = headers.get('x-user-id', '')

    return user_id


def deep_merge(base, override):
    """Deep merge override into base dict, preserving base structure."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def sanitize_preferences(prefs):
    """Validate and sanitize user preference values."""
    sanitized = {}

    if 'alerts' in prefs:
        alerts = prefs['alerts']
        sanitized['alerts'] = {}

        # Threshold values must be 0-100
        for key in ['distress_index_threshold', 'trigger_probability_threshold',
                     'contagion_risk_threshold', 'vix_level_threshold']:
            if key in alerts:
                val = alerts[key]
                if isinstance(val, (int, float)) and 0 <= val <= 100:
                    sanitized['alerts'][key] = val

        if 'enabled' in alerts and isinstance(alerts['enabled'], bool):
            sanitized['alerts']['enabled'] = alerts['enabled']

        if 'channels' in alerts and isinstance(alerts['channels'], list):
            valid_channels = ['dashboard', 'telegram', 'email']
            sanitized['alerts']['channels'] = [c for c in alerts['channels'] if c in valid_channels]

        if 'quiet_hours' in alerts and isinstance(alerts['quiet_hours'], dict):
            sanitized['alerts']['quiet_hours'] = alerts['quiet_hours']

    if 'dashboard' in prefs:
        dash = prefs['dashboard']
        sanitized['dashboard'] = {}

        valid_tabs = ['overview', 'inequality', 'sentiment', 'sovereign', 'sre', 'predictions']
        if 'default_tab' in dash and dash['default_tab'] in valid_tabs:
            sanitized['dashboard']['default_tab'] = dash['default_tab']

        if 'refresh_interval' in dash:
            val = dash['refresh_interval']
            if isinstance(val, (int, float)) and 5 <= val <= 300:
                sanitized['dashboard']['refresh_interval'] = int(val)

        valid_themes = ['dark', 'light']
        if 'theme' in dash and dash['theme'] in valid_themes:
            sanitized['dashboard']['theme'] = dash['theme']

        for bool_key in ['compact_mode', 'show_sparklines', 'show_predictions']:
            if bool_key in dash and isinstance(dash[bool_key], bool):
                sanitized['dashboard'][bool_key] = dash[bool_key]

    if 'notifications' in prefs:
        notif = prefs['notifications']
        sanitized['notifications'] = {}

        valid_digests = ['none', 'daily', 'weekly']
        if 'email_digest' in notif and notif['email_digest'] in valid_digests:
            sanitized['notifications']['email_digest'] = notif['email_digest']

        for bool_key in ['telegram_enabled', 'browser_notifications']:
            if bool_key in notif and isinstance(notif[bool_key], bool):
                sanitized['notifications'][bool_key] = notif[bool_key]

        if 'telegram_chat_id' in notif:
            sanitized['notifications']['telegram_chat_id'] = str(notif['telegram_chat_id'])

    if 'watchlists' in prefs:
        wl = prefs['watchlists']
        sanitized['watchlists'] = {}

        if 'countries' in wl and isinstance(wl['countries'], list):
            sanitized['watchlists']['countries'] = [str(c)[:3].upper() for c in wl['countries'][:50]]

        if 'sectors' in wl and isinstance(wl['sectors'], list):
            sanitized['watchlists']['sectors'] = [str(s)[:50] for s in wl['sectors'][:20]]

        if 'kpis' in wl and isinstance(wl['kpis'], list):
            valid_kpis = ['distress_index', 'trigger_probability', 'contagion_risk', 'vix_level',
                          'cpi_yoy', 'fed_rate', 'treasury_10y', 'unemployment']
            sanitized['watchlists']['kpis'] = [k for k in wl['kpis'] if k in valid_kpis]

    return sanitized


def handle_get(event):
    """Get user preferences (creates defaults if none exist)."""
    user_id = get_user_id(event)
    if not user_id:
        return build_response(401, {'error': 'Authentication required'})

    table = dynamodb.Table(PREFERENCES_TABLE)

    try:
        response = table.get_item(Key={'user_id': user_id})
        item = response.get('Item')

        if item:
            prefs = item.get('preferences', {})
            # Merge with defaults to fill any missing fields
            merged = deep_merge(DEFAULT_PREFERENCES, prefs)
            return build_response(200, {
                'user_id': user_id,
                'preferences': merged,
                'updated_at': item.get('updated_at', ''),
            })
        else:
            # Create default preferences
            timestamp = datetime.utcnow().isoformat() + 'Z'
            prefs_safe = json.loads(
                json.dumps(DEFAULT_PREFERENCES),
                parse_float=lambda x: Decimal(str(x))
            )
            table.put_item(Item={
                'user_id': user_id,
                'preferences': prefs_safe,
                'created_at': timestamp,
                'updated_at': timestamp,
            })

            return build_response(200, {
                'user_id': user_id,
                'preferences': DEFAULT_PREFERENCES,
                'updated_at': timestamp,
                'is_default': True,
            })

    except Exception as e:
        logger.error(f"Get preferences failed: {e}")
        return build_response(500, {'error': 'Failed to retrieve preferences'})


def handle_put(event):
    """Update user preferences (partial update supported)."""
    user_id = get_user_id(event)
    if not user_id:
        return build_response(401, {'error': 'Authentication required'})

    body = event.get('body', '{}')
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            return build_response(400, {'error': 'Invalid JSON body'})

    prefs_update = body.get('preferences', body)
    if not prefs_update or not isinstance(prefs_update, dict):
        return build_response(400, {'error': 'Preferences object required'})

    # Sanitize input
    sanitized = sanitize_preferences(prefs_update)

    table = dynamodb.Table(PREFERENCES_TABLE)

    try:
        # Get existing preferences
        response = table.get_item(Key={'user_id': user_id})
        existing = response.get('Item', {}).get('preferences', {})

        # Deep merge
        merged = deep_merge(DEFAULT_PREFERENCES, existing)
        merged = deep_merge(merged, sanitized)

        timestamp = datetime.utcnow().isoformat() + 'Z'
        prefs_safe = json.loads(
            json.dumps(merged),
            parse_float=lambda x: Decimal(str(x))
        )

        table.put_item(Item={
            'user_id': user_id,
            'preferences': prefs_safe,
            'updated_at': timestamp,
        })

        return build_response(200, {
            'user_id': user_id,
            'preferences': merged,
            'updated_at': timestamp,
        })

    except Exception as e:
        logger.error(f"Update preferences failed: {e}")
        return build_response(500, {'error': 'Failed to update preferences'})


def handler(event, context):
    """Main Lambda handler — routes GET/PUT requests."""
    logger.info(f"Preferences request: {json.dumps({k: v for k, v in event.items() if k != 'body'}, default=str)}")

    method = event.get('httpMethod', event.get('requestContext', {}).get('http', {}).get('method', 'GET'))

    if method == 'OPTIONS':
        return build_response(200, {'status': 'ok'})
    elif method == 'GET':
        return handle_get(event)
    elif method == 'PUT':
        return handle_put(event)
    else:
        return build_response(405, {'error': f'Method {method} not allowed'})
