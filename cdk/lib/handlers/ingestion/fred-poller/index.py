import json
import os
import urllib.request
import urllib.error
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

FRED_SERIES = [
    'DRCCLACBS',  # Credit card delinquency rate
    'TDSP',       # Household debt service ratio
    'NFCIC',      # Financial conditions credit
    'BOGZ1FL154022386Q'  # Household debt securities
]

FRED_BASE_URL = 'https://api.stlouisfed.org/fred/series/observations'


def fetch_series_data(series_id: str, max_retries: int = 3) -> dict:
    """Fetch observations for a FRED series."""
    for attempt in range(max_retries):
        try:
            url = f"{FRED_BASE_URL}?series_id={series_id}&api_key={FRED_API_KEY}&file_type=json"
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


def write_to_dynamodb(series_id: str, observations: list) -> None:
    """Write observations to DynamoDB."""
    table = dynamodb.Table(SIGNALS_TABLE)
    ttl_timestamp = int((datetime.utcnow() + timedelta(days=30)).timestamp())

    with table.batch_writer(batch_size=25) as batch:
        for obs in observations:
            if obs.get('value') and obs['value'] != '.':
                try:
                    item = {
                        'dashboard': 'inequality_pulse',
                        'sort_key': f"{series_id}#{obs['date']}",
                        'source': 'fred',
                        'value': float(obs['value']),
                        'raw_data': json.dumps(obs),
                        'ttl': ttl_timestamp,
                        'timestamp': datetime.utcnow().isoformat()
                    }
                    batch.put_item(Item=item)
                except (ValueError, KeyError) as e:
                    logger.error(f"Error processing observation for {series_id}: {str(e)}")


def publish_event(updated_series: list) -> None:
    """Publish EventBridge event."""
    try:
        event = {
            'Source': 'mvt.ingestion.inequality',
            'DetailType': 'FREDDataUpdated',
            'EventBusName': EVENT_BUS_NAME,
            'Detail': json.dumps({
                'series': updated_series,
                'timestamp': datetime.utcnow().isoformat()
            })
        }
        events.put_events(Entries=[event])
        logger.info(f"Published event for series: {updated_series}")
    except Exception as e:
        logger.error(f"Error publishing event: {str(e)}")
        raise


def lambda_handler(event, context):
    """Main Lambda handler."""
    logger.info("Starting FRED data poller")

    try:
        updated_series = []

        for series_id in FRED_SERIES:
            try:
                logger.info(f"Fetching data for {series_id}")
                data = fetch_series_data(series_id)

                if 'observations' in data:
                    write_to_dynamodb(series_id, data['observations'])
                    updated_series.append(series_id)
                    logger.info(f"Successfully processed {len(data['observations'])} observations for {series_id}")
                else:
                    logger.warning(f"No observations found for {series_id}")
            except Exception as e:
                logger.error(f"Error processing series {series_id}: {str(e)}")
                continue

        if updated_series:
            publish_event(updated_series)

        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'FRED polling completed',
                'updated_series': updated_series
            })
        }

    except Exception as e:
        logger.error(f"Unhandled error in lambda_handler: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
