"""
MVT Custom Watchlists API (S7-05)
Per-user country and sector watchlists for personalized dashboard views.
Users can create, update, and delete watchlists that filter dashboard data.

Trigger: API Gateway REST (GET/POST/PUT/DELETE /watchlists)
Auth: JWT token via Cognito authorizer
Output: Watchlist CRUD operations on mvt-user-preferences table
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

# Available countries for sovereign risk watchlist
AVAILABLE_COUNTRIES = [
    'ARG', 'BRA', 'CHL', 'CHN', 'COL', 'EGY', 'GHA', 'IDN', 'IND',
    'KEN', 'MEX', 'MYS', 'NGA', 'PAK', 'PER', 'PHL', 'RUS', 'THA',
    'TUR', 'UKR', 'VEN', 'VNM', 'ZAF', 'ZMB',
]

# Available sectors for sentiment watchlist
AVAILABLE_SECTORS = [
    'Technology', 'Healthcare', 'Financial', 'Consumer Discretionary',
    'Consumer Staples', 'Energy', 'Industrials', 'Materials',
    'Real Estate', 'Utilities', 'Communication Services',
]

# Max watchlist sizes
MAX_COUNTRIES = 50
MAX_SECTORS = 20
MAX_WATCHLISTS = 10


def build_response(status_code, body):
    """Build API Gateway response with CORS."""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, Authorization',
        },
        'body': json.dumps(body, default=str),
    }


def get_user_id(event):
    """Extract user ID from JWT claims."""
    claims = event.get('requestContext', {}).get('authorizer', {}).get('claims', {})
    return claims.get('sub') or claims.get('cognito:username', '') or \
           (event.get('headers') or {}).get('x-user-id', '')


def get_user_watchlists(table, user_id):
    """Get all watchlists for a user."""
    try:
        response = table.get_item(Key={'user_id': user_id})
        item = response.get('Item', {})
        prefs = item.get('preferences', {})
        return prefs.get('watchlists', {
            'countries': [],
            'sectors': [],
            'kpis': ['distress_index', 'trigger_probability', 'contagion_risk', 'vix_level'],
            'custom': [],
        })
    except Exception as e:
        logger.error(f"Failed to get watchlists: {e}")
        return None


def save_user_watchlists(table, user_id, watchlists):
    """Save watchlists to user preferences."""
    try:
        timestamp = datetime.utcnow().isoformat() + 'Z'

        # Get existing preferences
        response = table.get_item(Key={'user_id': user_id})
        item = response.get('Item', {})
        prefs = item.get('preferences', {})

        prefs['watchlists'] = watchlists

        prefs_safe = json.loads(
            json.dumps(prefs),
            parse_float=lambda x: Decimal(str(x))
        )

        table.put_item(Item={
            'user_id': user_id,
            'preferences': prefs_safe,
            'updated_at': timestamp,
        })

        return True
    except Exception as e:
        logger.error(f"Failed to save watchlists: {e}")
        return False


def handle_get(event, user_id):
    """Get user's watchlists."""
    table = dynamodb.Table(PREFERENCES_TABLE)
    watchlists = get_user_watchlists(table, user_id)

    if watchlists is None:
        return build_response(500, {'error': 'Failed to retrieve watchlists'})

    return build_response(200, {
        'user_id': user_id,
        'watchlists': watchlists,
        'available_countries': AVAILABLE_COUNTRIES,
        'available_sectors': AVAILABLE_SECTORS,
    })


def handle_update_countries(event, user_id, body):
    """Update country watchlist."""
    countries = body.get('countries', [])

    if not isinstance(countries, list):
        return build_response(400, {'error': 'Countries must be a list'})

    # Validate and normalize
    validated = []
    for c in countries[:MAX_COUNTRIES]:
        code = str(c).strip().upper()[:3]
        if code in AVAILABLE_COUNTRIES:
            validated.append(code)
        else:
            logger.warning(f"Invalid country code: {code}")

    table = dynamodb.Table(PREFERENCES_TABLE)
    watchlists = get_user_watchlists(table, user_id) or {}
    watchlists['countries'] = validated

    if save_user_watchlists(table, user_id, watchlists):
        return build_response(200, {
            'message': f'Country watchlist updated ({len(validated)} countries)',
            'watchlists': watchlists,
        })
    else:
        return build_response(500, {'error': 'Failed to update watchlist'})


def handle_update_sectors(event, user_id, body):
    """Update sector watchlist."""
    sectors = body.get('sectors', [])

    if not isinstance(sectors, list):
        return build_response(400, {'error': 'Sectors must be a list'})

    validated = [s for s in sectors[:MAX_SECTORS] if s in AVAILABLE_SECTORS]

    table = dynamodb.Table(PREFERENCES_TABLE)
    watchlists = get_user_watchlists(table, user_id) or {}
    watchlists['sectors'] = validated

    if save_user_watchlists(table, user_id, watchlists):
        return build_response(200, {
            'message': f'Sector watchlist updated ({len(validated)} sectors)',
            'watchlists': watchlists,
        })
    else:
        return build_response(500, {'error': 'Failed to update watchlist'})


def handle_create_custom(event, user_id, body):
    """Create a custom named watchlist."""
    name = body.get('name', '').strip()
    watchlist_type = body.get('type', 'mixed')  # countries, sectors, kpis, mixed
    items = body.get('items', [])

    if not name:
        return build_response(400, {'error': 'Watchlist name is required'})

    if len(name) > 50:
        return build_response(400, {'error': 'Watchlist name must be 50 characters or less'})

    table = dynamodb.Table(PREFERENCES_TABLE)
    watchlists = get_user_watchlists(table, user_id) or {}

    custom = watchlists.get('custom', [])
    if len(custom) >= MAX_WATCHLISTS:
        return build_response(400, {'error': f'Maximum {MAX_WATCHLISTS} custom watchlists allowed'})

    # Check for duplicate names
    if any(w.get('name') == name for w in custom):
        return build_response(409, {'error': f'Watchlist "{name}" already exists'})

    new_watchlist = {
        'id': f'wl_{datetime.utcnow().strftime("%Y%m%d%H%M%S")}',
        'name': name,
        'type': watchlist_type,
        'items': items[:100],  # Max 100 items
        'created_at': datetime.utcnow().isoformat() + 'Z',
    }

    custom.append(new_watchlist)
    watchlists['custom'] = custom

    if save_user_watchlists(table, user_id, watchlists):
        return build_response(201, {
            'message': f'Custom watchlist "{name}" created',
            'watchlist': new_watchlist,
        })
    else:
        return build_response(500, {'error': 'Failed to create watchlist'})


def handle_delete_custom(event, user_id, body):
    """Delete a custom watchlist by ID or name."""
    watchlist_id = body.get('id', '')
    watchlist_name = body.get('name', '')

    if not watchlist_id and not watchlist_name:
        return build_response(400, {'error': 'Watchlist ID or name is required'})

    table = dynamodb.Table(PREFERENCES_TABLE)
    watchlists = get_user_watchlists(table, user_id) or {}
    custom = watchlists.get('custom', [])

    original_count = len(custom)
    custom = [w for w in custom if w.get('id') != watchlist_id and w.get('name') != watchlist_name]

    if len(custom) == original_count:
        return build_response(404, {'error': 'Watchlist not found'})

    watchlists['custom'] = custom

    if save_user_watchlists(table, user_id, watchlists):
        return build_response(200, {'message': 'Watchlist deleted'})
    else:
        return build_response(500, {'error': 'Failed to delete watchlist'})


def handler(event, context):
    """Main handler — routes watchlist CRUD operations."""
    logger.info(f"Watchlist request: {json.dumps({k: v for k, v in event.items() if k != 'body'}, default=str)}")

    method = event.get('httpMethod', event.get('requestContext', {}).get('http', {}).get('method', 'GET'))

    if method == 'OPTIONS':
        return build_response(200, {'status': 'ok'})

    user_id = get_user_id(event)
    if not user_id:
        return build_response(401, {'error': 'Authentication required'})

    # Parse body for POST/PUT/DELETE
    body = {}
    if method in ('POST', 'PUT', 'DELETE'):
        raw = event.get('body', '{}')
        if isinstance(raw, str):
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                return build_response(400, {'error': 'Invalid JSON body'})

    # Route by action
    path = event.get('path', event.get('rawPath', ''))
    action = body.get('action', '')

    if method == 'GET':
        return handle_get(event, user_id)
    elif method == 'PUT' and 'countries' in body:
        return handle_update_countries(event, user_id, body)
    elif method == 'PUT' and 'sectors' in body:
        return handle_update_sectors(event, user_id, body)
    elif method == 'POST' or action == 'create':
        return handle_create_custom(event, user_id, body)
    elif method == 'DELETE' or action == 'delete':
        return handle_delete_custom(event, user_id, body)
    else:
        return build_response(400, {'error': 'Invalid request'})
