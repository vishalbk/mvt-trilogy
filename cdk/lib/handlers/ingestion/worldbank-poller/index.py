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

WORLD_BANK_BASE_URL = 'https://api.worldbank.org/v2'
COUNTRIES = ['ARG', 'BRA', 'TUR', 'EGY', 'PAK', 'NGA', 'ZAF', 'MEX', 'IDN', 'IND']


def fetch_reserves_data(country_code: str, max_retries: int = 3) -> dict:
    """Fetch total reserves data from World Bank API."""
    for attempt in range(max_retries):
        try:
            url = f"{WORLD_BANK_BASE_URL}/country/{country_code}/indicator/FI.RES.TOTL.CD?format=json&per_page=1&date=2023"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                return data
        except urllib.error.URLError as e:
            logger.warning(f"Attempt {attempt + 1} failed for {country_code}: {str(e)}")
            if attempt < max_retries - 1:
                continue
            raise
    return {}


def extract_value(response_data: list) -> dict:
    """Extract value and date from World Bank response."""
    if not response_data or len(response_data) < 2:
        return {'value': None, 'date': None}

    data_array = response_data[1]
    if not data_array:
        return {'value': None, 'date': None}

    # Find latest non-null value
    for item in data_array:
        if item.get('value') is not None:
            return {
                'value': float(item['value']),
                'date': item.get('date'),
                'country': item.get('countryiso3code', '')
            }

    return {'value': None, 'date': None}


def reserves_to_signal_value(reserves: float) -> float:
    """
    Convert reserves (in USD) to 0-100 signal.
    Emerging markets typically have 100B-500B in reserves.
    Scale: 0 = 50B (low), 100 = 400B (high)
    """
    if reserves < 50e9:
        return 0.0
    elif reserves > 400e9:
        return 100.0
    else:
        return (reserves - 50e9) / (400e9 - 50e9) * 100


def write_to_dynamodb(data_records: list) -> None:
    """Write World Bank data to DynamoDB."""
    table = dynamodb.Table(SIGNALS_TABLE)
    ttl_timestamp = int(time.time()) + 30*86400
    timestamp = datetime.utcnow().isoformat()

    with table.batch_writer(batch_size=25) as batch:
        for record in data_records:
            try:
                signal_value = reserves_to_signal_value(record['value'])
                signal_id = f"worldbank#{record['country']}#{record['date']}"

                item = {
                    'dashboard': 'sovereign_dominoes',
                    'signalId_timestamp': signal_id,
                    'source': 'worldbank',
                    'value': signal_value,
                    'raw_data': {
                        'country': record['country'],
                        'reserves_usd': record['value'],
                        'date': record['date'],
                        'signal_value': signal_value
                    },
                    'ttl': ttl_timestamp
                }
                batch.put_item(Item=item)
                logger.info(f"Wrote {record['country']}: reserves={record['value']}, signal={signal_value}")
            except Exception as e:
                logger.error(f"Error writing {record.get('country')}: {str(e)}")


def publish_event(countries_updated: list, timestamp: str) -> None:
    """Publish EventBridge event."""
    try:
        event = {
            'Source': 'mvt.ingestion.sovereign',
            'DetailType': 'WorldBankUpdated',
            'EventBusName': EVENT_BUS_NAME,
            'Detail': json.dumps({
                'countries_updated': countries_updated,
                'count': len(countries_updated),
                'timestamp': timestamp
            })
        }
        events.put_events(Entries=[event])
        logger.info(f"Published WorldBankUpdated: {len(countries_updated)} countries")
    except Exception as e:
        logger.error(f"Error publishing event: {str(e)}")


def handler(event, context):
    """Main Lambda handler."""
    logger.info("Starting World Bank data poller")

    try:
        data_records = []
        countries_updated = []
        timestamp = datetime.utcnow().isoformat()

        for country in COUNTRIES:
            try:
                logger.info(f"Fetching reserves for {country}")
                response = fetch_reserves_data(country)

                extracted = extract_value(response)
                if extracted['value'] is not None:
                    data_records.append({
                        'country': country,
                        'value': extracted['value'],
                        'date': extracted['date']
                    })
                    countries_updated.append(country)
                    logger.info(f"Got {country}: {extracted['value']}")
                else:
                    logger.warning(f"No reserves data for {country}")

            except Exception as e:
                logger.error(f"Error fetching {country}: {str(e)}")
                continue

        if data_records:
            write_to_dynamodb(data_records)
            publish_event(countries_updated, timestamp)

            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'World Bank polling completed',
                    'records_processed': len(data_records),
                    'countries_updated': countries_updated
                })
            }
        else:
            logger.warning("No World Bank data processed")
            return {
                'statusCode': 200,
                'body': json.dumps({'message': 'No data processed', 'records_processed': 0})
            }

    except Exception as e:
        logger.error(f"Unhandled error: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
