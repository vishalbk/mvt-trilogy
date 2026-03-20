import json
import os
import urllib.request
import urllib.error
import time
from datetime import datetime, timedelta
from decimal import Decimal
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

# World Bank indicators
INDICATORS = {
    'NY.GNP.PCAP.CD': 'GNI per capita',
    'SI.POV.GINI': 'Gini index',
    'SL.UEM.TOTL.ZS': 'Unemployment'
}


def fetch_indicator_data(country_code: str, indicator: str, max_retries: int = 3) -> dict:
    """Fetch indicator data from World Bank API."""
    for attempt in range(max_retries):
        try:
            url = f"{WORLD_BANK_BASE_URL}/country/{country_code}/indicator/{indicator}?format=json&per_page=1&date=2023"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                return data
        except urllib.error.URLError as e:
            logger.warning(f"Attempt {attempt + 1} failed for {country_code}/{indicator}: {str(e)}")
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


def indicator_to_signal_value(value: float, indicator: str) -> float:
    """
    Convert indicator value to 0-100 signal based on indicator type.
    - GNI: scale 1000-60000 to 0-100 (inverted: low GNI = more vulnerability)
    - Gini: scale 25-60 to 0-100 (high Gini = more inequality)
    - Unemployment: scale 2-25 to 0-100 (high = more stress)
    """
    if indicator == 'NY.GNP.PCAP.CD':
        # GNI per capita: inverted (low = vulnerability)
        if value < 1000:
            return 100.0
        elif value > 60000:
            return 0.0
        else:
            return 100 - (value - 1000) / (60000 - 1000) * 100
    elif indicator == 'SI.POV.GINI':
        # Gini index: scale 25-60 to 0-100 (higher = more stress)
        if value < 25:
            return 0.0
        elif value > 60:
            return 100.0
        else:
            return (value - 25) / (60 - 25) * 100
    elif indicator == 'SL.UEM.TOTL.ZS':
        # Unemployment: scale 2-25 to 0-100 (higher = more stress)
        if value < 2:
            return 0.0
        elif value > 25:
            return 100.0
        else:
            return (value - 2) / (25 - 2) * 100
    else:
        return 0.0


def write_to_dynamodb(data_records: list) -> None:
    """Write World Bank data to DynamoDB."""
    table = dynamodb.Table(SIGNALS_TABLE)
    ttl_timestamp = int(time.time()) + 30*86400
    timestamp = datetime.utcnow().isoformat()

    with table.batch_writer() as batch:
        for record in data_records:
            try:
                signal_value = indicator_to_signal_value(record['value'], record['indicator'])
                signal_id = f"worldbank#{record['country']}#{record['indicator']}#{record['date']}"

                item = {
                    'dashboard': 'sovereign_dominoes',
                    'signalId_timestamp': signal_id,
                    'source': 'worldbank',
                    'value': Decimal(str(signal_value)),
                    'raw_data': {
                        'country': record['country'],
                        'indicator': record['indicator'],
                        'indicator_name': INDICATORS.get(record['indicator'], ''),
                        'value': Decimal(str(record['value'])),
                        'date': record['date'],
                        'signal_value': Decimal(str(signal_value))
                    },
                    'ttl': ttl_timestamp
                }
                batch.put_item(Item=item)
                logger.info(f"Wrote {record['country']}/{record['indicator']}: value={record['value']}, signal={signal_value}")
            except Exception as e:
                logger.error(f"Error writing {record.get('country')}/{record.get('indicator')}: {str(e)}")


def publish_event(indicators_updated: int, timestamp: str) -> None:
    """Publish EventBridge event."""
    try:
        event = {
            'Source': 'mvt.ingestion.sovereign',
            'DetailType': 'WorldBankUpdated',
            'EventBusName': EVENT_BUS_NAME,
            'Detail': json.dumps({
                'indicators_updated': indicators_updated,
                'timestamp': timestamp
            })
        }
        events.put_events(Entries=[event])
        logger.info(f"Published WorldBankUpdated: {indicators_updated} indicators")
    except Exception as e:
        logger.error(f"Error publishing event: {str(e)}")


def handler(event, context):
    """Main Lambda handler."""
    logger.info("Starting World Bank data poller")

    try:
        data_records = []
        indicators_updated = 0
        timestamp = datetime.utcnow().isoformat()

        for country in COUNTRIES:
            for indicator_code, indicator_name in INDICATORS.items():
                try:
                    logger.info(f"Fetching {indicator_name} for {country}")
                    response = fetch_indicator_data(country, indicator_code)

                    extracted = extract_value(response)
                    if extracted['value'] is not None:
                        data_records.append({
                            'country': country,
                            'indicator': indicator_code,
                            'value': extracted['value'],
                            'date': extracted['date']
                        })
                        indicators_updated += 1
                        logger.info(f"Got {country}/{indicator_code}: {extracted['value']}")
                    else:
                        logger.warning(f"No data for {country}/{indicator_code}")

                except Exception as e:
                    logger.error(f"Error fetching {country}/{indicator_code}: {str(e)}")
                    continue

        if data_records:
            write_to_dynamodb(data_records)
            publish_event(indicators_updated, timestamp)

            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'World Bank polling completed',
                    'records_processed': len(data_records),
                    'indicators_updated': indicators_updated
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
