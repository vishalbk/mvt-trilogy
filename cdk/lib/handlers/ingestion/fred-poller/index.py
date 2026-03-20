import json
import os
import urllib.request
import urllib.error
import time
from datetime import datetime, timedelta
import boto3
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')
events = boto3.client('events')

SIGNALS_TABLE = os.environ.get('SIGNALS_TABLE')
EVENT_BUS_NAME = os.environ.get('EVENT_BUS_NAME')
FRED_API_KEY = os.environ.get('FRED_API_KEY')

FRED_BASE_URL = 'https://api.stlouisfed.org/fred/series/observations'

# FRED endpoints with their dashboard mapping
FRED_SERIES = {
    'DRCCLACBS': 'Consumer credit delinquency',
    'TDSP': 'Debt service ratio',
    'NFCIC': 'Financial conditions credit',
    'M2NS': 'M2 money supply',
    'UNRATE': 'Unemployment rate',
    'DCOILWTICO': 'WTI crude oil price',
    'T10Y2Y': '10Y-2Y Treasury yield spread',
    'UMCSENT': 'Consumer sentiment'
}


def fetch_series_data(series_id: str, max_retries: int = 3) -> dict:
    """Fetch latest observation for a FRED series."""
    for attempt in range(max_retries):
        try:
            url = f"{FRED_BASE_URL}?series_id={series_id}&api_key={FRED_API_KEY}&file_type=json&limit=1&sort_order=desc"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                return data
        except urllib.error.URLError as e:
            logger.warning(f"Attempt {attempt + 1} failed for {series_id}: {str(e)}")
            if attempt < max_retries - 1:
                continue
            raise
    return {}


def compute_inequality_score(fred_data: dict) -> float:
    """
    Compute composite inequality pulse score (0-100).
    - DRCCLACBS (delinquency): >2.5% is high stress
    - TDSP (debt service): >10% is high stress
    - NFCIC (financial tightening): >0 indicates tightening
    - M2NS: scale 15000-25000 (billions) to 0-100
    - UNRATE: scale 3-10 (%) to 0-100
    - DCOILWTICO: scale 40-120 (USD) to 0-100
    - T10Y2Y: scale -1 to 3 to 0-100 (inverted yield = stress)
    - UMCSENT: scale 50-100 to 0-100 (inverted: low sentiment = high stress)
    """
    scores = []

    for series_id, description in FRED_SERIES.items():
        if series_id in fred_data and fred_data[series_id].get('value'):
            val = float(fred_data[series_id]['value'])
            if series_id == 'DRCCLACBS':
                # Delinquency: scale 0-2.5% to 0-100
                score = min(100, (val / 2.5) * 100)
            elif series_id == 'TDSP':
                # Debt service: scale 0-10% to 0-100
                score = min(100, (val / 10.0) * 100)
            elif series_id == 'NFCIC':
                # Financial conditions: tightening (>0) is stress, loosen (<0) is relief
                # Scale -5 to 5 to 0-100
                score = max(0, min(100, (val + 5) / 10.0 * 100))
            elif series_id == 'M2NS':
                # M2 money supply: scale 15000-25000 (billions) to 0-100
                score = max(0, min(100, (val - 15000) / (25000 - 15000) * 100))
            elif series_id == 'UNRATE':
                # Unemployment rate: scale 3-10 (%) to 0-100
                score = max(0, min(100, (val - 3) / (10 - 3) * 100))
            elif series_id == 'DCOILWTICO':
                # WTI crude oil price: scale 40-120 (USD) to 0-100
                score = max(0, min(100, (val - 40) / (120 - 40) * 100))
            elif series_id == 'T10Y2Y':
                # 10Y-2Y Treasury yield spread: scale -1 to 3 to 0-100 (inverted: negative spread = stress)
                score = max(0, min(100, (val - (-1)) / (3 - (-1)) * 100))
            elif series_id == 'UMCSENT':
                # Consumer sentiment: scale 50-100 to 0-100 (inverted: low sentiment = high stress)
                score = max(0, min(100, 100 - (val - 50) / (100 - 50) * 100))
            else:
                score = 0
            scores.append(score)

    return sum(scores) / len(scores) if scores else 0.0


def write_to_dynamodb(series_data: dict) -> None:
    """Write FRED observations to DynamoDB."""
    table = dynamodb.Table(SIGNALS_TABLE)
    ttl_timestamp = int(time.time()) + 30*86400
    timestamp = datetime.utcnow().isoformat()

    for series_id, obs in series_data.items():
        try:
            if obs.get('value') and obs['value'] != '.':
                value = float(obs['value'])
                signal_id = f"fred#{series_id}#{obs['date']}"

                item = {
                    'dashboard': 'inequality_pulse',
                    'signalId_timestamp': signal_id,
                    'source': 'fred',
                    'value': value,
                    'raw_data': {
                        'series_id': series_id,
                        'date': obs['date'],
                        'value': value,
                        'description': FRED_SERIES.get(series_id, '')
                    },
                    'ttl': ttl_timestamp
                }
                table.put_item(Item=item)
                logger.info(f"Wrote {series_id}={value} to DynamoDB")
        except (ValueError, KeyError) as e:
            logger.error(f"Error processing {series_id}: {str(e)}")


def publish_event(score: float, timestamp: str) -> None:
    """Publish EventBridge event."""
    try:
        event = {
            'Source': 'mvt.ingestion.inequality',
            'DetailType': 'FREDDataUpdated',
            'EventBusName': EVENT_BUS_NAME,
            'Detail': json.dumps({
                'score': score,
                'timestamp': timestamp
            })
        }
        events.put_events(Entries=[event])
        logger.info(f"Published FREDDataUpdated event: score={score}")
    except Exception as e:
        logger.error(f"Error publishing event: {str(e)}")


def handler(event, context):
    """Main Lambda handler."""
    logger.info("Starting FRED data poller")

    try:
        fred_data = {}
        timestamp = datetime.utcnow().isoformat()

        for series_id in FRED_SERIES.keys():
            try:
                logger.info(f"Fetching {series_id}")
                data = fetch_series_data(series_id)

                if data.get('observations') and len(data['observations']) > 0:
                    obs = data['observations'][0]
                    fred_data[series_id] = obs
                    logger.info(f"Got {series_id}={obs.get('value')}")
                else:
                    logger.warning(f"No data for {series_id}")
            except Exception as e:
                logger.error(f"Error fetching {series_id}: {str(e)}")
                continue

        if fred_data:
            write_to_dynamodb(fred_data)
            score = compute_inequality_score(fred_data)
            publish_event(score, timestamp)

            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'FRED polling completed',
                    'score': score,
                    'series_count': len(fred_data)
                })
            }
        else:
            logger.warning("No FRED data fetched")
            return {
                'statusCode': 200,
                'body': json.dumps({'message': 'No data', 'series_count': 0})
            }

    except Exception as e:
        logger.error(f"Unhandled error: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
