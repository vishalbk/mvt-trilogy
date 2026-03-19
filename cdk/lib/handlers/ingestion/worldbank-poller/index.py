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

WORLD_BANK_BASE_URL = 'http://api.worldbank.org/v2'

COUNTRIES = ['ARG', 'TUR', 'EGY', 'PAK', 'NGA', 'BRA', 'ZAF', 'MEX', 'IDN', 'IND']

INDICATORS = {
    'FI.RES.TOTL.CD': 'Total Reserves',
    'DT.DOD.DECT.GN.ZS': 'External Debt % GNI',
    'NY.GDP.MKTP.KD.ZG': 'GDP Growth Rate'
}


def fetch_indicator_data(country_code: str, indicator: str, max_retries: int = 3) -> dict:
    """Fetch indicator data from World Bank API."""
    for attempt in range(max_retries):
        try:
            url = f"{WORLD_BANK_BASE_URL}/country/{country_code}/indicator/{indicator}?format=json"
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


def extract_latest_value(response_data: list) -> tuple:
    """Extract latest value and date from World Bank response."""
    if not response_data or len(response_data) < 2:
        return None, None

    # First element is metadata, second is data array
    data_array = response_data[1]
    if not data_array:
        return None, None

    # Find latest non-null value
    for item in data_array:
        if item.get('value') is not None:
            return float(item['value']), item.get('date')

    return None, None


def write_to_dynamodb(data_records: list) -> None:
    """Write World Bank data to DynamoDB."""
    table = dynamodb.Table(SIGNALS_TABLE)
    ttl_timestamp = int((datetime.utcnow() + timedelta(days=30)).timestamp())
    timestamp = datetime.utcnow().isoformat()

    with table.batch_writer(batch_size=25) as batch:
        for record in data_records:
            try:
                item = {
                    'dashboard': 'sovereign_dominoes',
                    'sort_key': f"worldbank#{record['country']}#{record['indicator']}#{record['date']}",
                    'source': 'worldbank',
                    'country': record['country'],
                    'indicator': record['indicator'],
                    'indicator_name': record['indicator_name'],
                    'value': record['value'],
                    'date': record['date'],
                    'ttl': ttl_timestamp,
                    'timestamp': timestamp
                }
                batch.put_item(Item=item)
            except Exception as e:
                logger.error(f"Error writing World Bank record for {record.get('country')}/{record.get('indicator')}: {str(e)}")


def publish_event(countries_updated: list) -> None:
    """Publish EventBridge event."""
    try:
        event = {
            'Source': 'mvt.ingestion.sovereign',
            'DetailType': 'WorldBankUpdated',
            'EventBusName': EVENT_BUS_NAME,
            'Detail': json.dumps({
                'countries_updated': countries_updated,
                'timestamp': datetime.utcnow().isoformat()
            })
        }
        events.put_events(Entries=[event])
        logger.info(f"Published WorldBankUpdated event for countries: {countries_updated}")
    except Exception as e:
        logger.error(f"Error publishing event: {str(e)}")
        raise


def lambda_handler(event, context):
    """Main Lambda handler."""
    logger.info("Starting World Bank data poller")

    try:
        data_records = []
        countries_updated = []

        for country in COUNTRIES:
            for indicator_code, indicator_name in INDICATORS.items():
                try:
                    logger.info(f"Fetching {indicator_name} for {country}")
                    response = fetch_indicator_data(country, indicator_code)

                    value, date = extract_latest_value(response)
                    if value is not None and date is not None:
                        data_records.append({
                            'country': country,
                            'indicator': indicator_code,
                            'indicator_name': indicator_name,
                            'value': value,
                            'date': date
                        })
                        if country not in countries_updated:
                            countries_updated.append(country)
                        logger.info(f"Found value {value} for {country}/{indicator_code} on {date}")
                    else:
                        logger.warning(f"No data found for {country}/{indicator_code}")

                except Exception as e:
                    logger.error(f"Error fetching {country}/{indicator_code}: {str(e)}")
                    continue

        if data_records:
            write_to_dynamodb(data_records)
            publish_event(countries_updated)

            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'World Bank polling completed',
                    'records_processed': len(data_records),
                    'countries_updated': countries_updated
                })
            }
        else:
            logger.warning("No World Bank data records processed")
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'No data processed',
                    'records_processed': 0
                })
            }

    except Exception as e:
        logger.error(f"Unhandled error in lambda_handler: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
