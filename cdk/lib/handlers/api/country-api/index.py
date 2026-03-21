"""
MVT Country API Lambda (EV5-01)
GET /api/country/{code} — Returns indicator breakdown + 30d history
for a specific country from the sovereign_dominoes dashboard.

Trigger: API Gateway HTTP GET
Output: JSON with current indicators, risk score, and 30d history
"""

import json
import os
import logging
from datetime import datetime, timedelta
from decimal import Decimal
import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')

SIGNALS_TABLE = os.environ.get('SIGNALS_TABLE', 'mvt-signals')
DASHBOARD_STATE_TABLE = os.environ.get('DASHBOARD_STATE_TABLE', 'mvt-dashboard-state')
KPI_HISTORY_TABLE = os.environ.get('KPI_HISTORY_TABLE', 'mvt-kpi-history')

COUNTRIES = {
    'ARG': 'Argentina', 'BRA': 'Brazil', 'TUR': 'Turkey',
    'EGY': 'Egypt', 'PAK': 'Pakistan', 'NGA': 'Nigeria',
    'ZAF': 'South Africa', 'MEX': 'Mexico', 'IDN': 'Indonesia', 'IND': 'India'
}

INDICATOR_META = {
    'NY.GNP.PCAP.CD': {'name': 'GNI per Capita', 'unit': 'USD', 'description': 'Gross National Income per capita'},
    'SI.POV.GINI': {'name': 'Gini Index', 'unit': 'index', 'description': 'Income inequality (0=perfect equality, 100=perfect inequality)'},
    'SL.UEM.TOTL.ZS': {'name': 'Unemployment Rate', 'unit': '%', 'description': 'Total unemployment as % of labor force'},
}


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def get_country_indicators(country_code):
    """Fetch current indicator values for a country from signals table."""
    table = dynamodb.Table(SIGNALS_TABLE)
    indicators = {}

    try:
        response = table.query(
            KeyConditionExpression=Key('dashboard').eq('sovereign_dominoes'),
            ScanIndexForward=False,
            Limit=200
        )

        for item in response.get('Items', []):
            sk = item.get('signalId_timestamp', '')
            if f'#{country_code}#' not in sk:
                continue

            raw_data = item.get('raw_data', {})
            indicator_code = raw_data.get('indicator', '')

            if indicator_code and indicator_code not in indicators:
                meta = INDICATOR_META.get(indicator_code, {})
                indicators[indicator_code] = {
                    'code': indicator_code,
                    'name': meta.get('name', indicator_code),
                    'unit': meta.get('unit', ''),
                    'description': meta.get('description', ''),
                    'raw_value': float(raw_data.get('value', 0)),
                    'signal_value': float(item.get('value', 0)),
                    'date': raw_data.get('date', ''),
                }

        return indicators
    except Exception as e:
        logger.error(f"Error fetching indicators for {country_code}: {e}")
        return {}


def get_country_risk_score(country_code):
    """Get current risk score from dashboard-state."""
    table = dynamodb.Table(DASHBOARD_STATE_TABLE)
    try:
        response = table.get_item(Key={
            'dashboard': 'sovereign_dominoes',
            'panel': 'risk_scores'
        })
        if 'Item' in response:
            scores = response['Item'].get('country_scores', {})
            return float(scores.get(country_code, 0))
        return 0
    except Exception as e:
        logger.error(f"Error fetching risk score for {country_code}: {e}")
        return 0


def get_country_history(country_code, days=30):
    """Fetch 30d history for the country's risk score from kpi-history table."""
    table = dynamodb.Table(KPI_HISTORY_TABLE)
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    history = []

    try:
        response = table.query(
            KeyConditionExpression=Key('dashboard_metric').eq(f'sovereign_dominoes#country_{country_code}')
                & Key('timestamp').gte(cutoff),
            ScanIndexForward=True,
            Limit=500
        )

        for item in response.get('Items', []):
            history.append({
                'timestamp': item.get('timestamp', ''),
                'value': float(item.get('value', 0)),
            })

    except Exception as e:
        logger.warning(f"History query failed for {country_code}: {e}")

    # If no history from kpi-history, try building from signals
    if not history:
        try:
            signals_table = dynamodb.Table(SIGNALS_TABLE)
            response = signals_table.query(
                KeyConditionExpression=Key('dashboard').eq('sovereign_dominoes'),
                ScanIndexForward=False,
                Limit=500
            )
            country_signals = []
            for item in response.get('Items', []):
                sk = item.get('signalId_timestamp', '')
                if f'#{country_code}#' in sk:
                    country_signals.append({
                        'timestamp': item.get('timestamp', datetime.utcnow().isoformat()),
                        'value': float(item.get('value', 0)),
                        'indicator': item.get('raw_data', {}).get('indicator', ''),
                    })
            if country_signals:
                history = country_signals[:30]
        except Exception as e:
            logger.warning(f"Signal history fallback failed: {e}")

    return history


def get_contagion_paths(country_code):
    """Get contagion paths involving this country."""
    table = dynamodb.Table(DASHBOARD_STATE_TABLE)
    try:
        response = table.get_item(Key={
            'dashboard': 'sovereign_dominoes',
            'panel': 'contagion_paths'
        })
        if 'Item' in response:
            all_paths = response['Item'].get('paths', [])
            country_paths = [
                p for p in all_paths
                if p.get('source') == country_code or p.get('target') == country_code
            ]
            return country_paths
        return []
    except Exception as e:
        logger.error(f"Error fetching contagion paths for {country_code}: {e}")
        return []


def handler(event, context):
    """Main Lambda handler — GET /api/country/{code}."""
    logger.info(f"Country API request: {json.dumps(event, default=str)}")

    # Extract country code from path parameters
    path_params = event.get('pathParameters', {}) or {}
    country_code = path_params.get('code', '').upper()

    # Also check rawPath for HTTP API v2
    if not country_code:
        raw_path = event.get('rawPath', '')
        parts = raw_path.strip('/').split('/')
        if len(parts) >= 3 and parts[1] == 'country':
            country_code = parts[2].upper()

    if not country_code or country_code not in COUNTRIES:
        return {
            'statusCode': 400,
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({
                'error': f'Invalid country code: {country_code}',
                'valid_codes': list(COUNTRIES.keys())
            })
        }

    # Gather all data
    indicators = get_country_indicators(country_code)
    risk_score = get_country_risk_score(country_code)
    history = get_country_history(country_code)
    contagion_paths = get_contagion_paths(country_code)

    # Determine risk status
    if risk_score > 70:
        status = 'critical'
    elif risk_score > 50:
        status = 'warning'
    else:
        status = 'monitoring'

    response_body = {
        'country_code': country_code,
        'country_name': COUNTRIES[country_code],
        'risk_score': round(risk_score, 2),
        'status': status,
        'indicators': indicators,
        'history': history,
        'contagion_paths': contagion_paths,
        'timestamp': datetime.utcnow().isoformat(),
    }

    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET,OPTIONS',
        },
        'body': json.dumps(response_body, cls=DecimalEncoder)
    }
