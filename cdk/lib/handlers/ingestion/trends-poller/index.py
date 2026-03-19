import json
import os
from datetime import datetime, timedelta
import boto3
import logging
from pytrends.request import TrendReq

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')
events = boto3.client('events')

SIGNALS_TABLE = os.environ.get('SIGNALS_TABLE')
EVENT_BUS_NAME = os.environ.get('EVENT_BUS_NAME')

DISTRESS_KEYWORDS = [
    "can't afford rent",
    "bankruptcy help",
    "debt relief",
    "food stamps application",
    "credit card default",
    "unemployment benefits",
    "cost of living crisis",
    "paycheck to paycheck",
    "eviction notice",
    "medical debt",
    "student loan default",
    "wage garnishment"
]


def fetch_trends_data(keywords: list, max_retries: int = 3) -> dict:
    """Fetch Google Trends data using pytrends."""
    for attempt in range(max_retries):
        try:
            pytrends = TrendReq(hl='en-US', tz=360)

            # Fetch interest over time for all keywords
            pytrends.build_payload(keywords, cat=0, timeframe='today 12-m', geo='US')
            interest_over_time = pytrends.interest_over_time()

            # Fetch interest by region
            pytrends.build_payload(keywords, cat=0, timeframe='today 12-m', geo='US')
            interest_by_region = pytrends.interest_by_region()

            return {
                'interest_over_time': interest_over_time.to_dict(),
                'interest_by_region': interest_by_region.to_dict(),
                'success': True
            }
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1} failed to fetch trends: {str(e)}")
            if attempt < max_retries - 1:
                continue
            logger.error(f"Failed to fetch trends after {max_retries} attempts: {str(e)}")
            return {'success': False, 'error': str(e)}


def write_to_dynamodb(keywords_data: dict) -> None:
    """Write trends data to DynamoDB."""
    table = dynamodb.Table(SIGNALS_TABLE)
    ttl_timestamp = int((datetime.utcnow() + timedelta(days=30)).timestamp())
    timestamp = datetime.utcnow().isoformat()

    with table.batch_writer(batch_size=25) as batch:
        for keyword in DISTRESS_KEYWORDS:
            try:
                item = {
                    'dashboard': 'inequality_pulse',
                    'sort_key': f"trends#{keyword}#{timestamp}",
                    'source': 'google_trends',
                    'keyword': keyword,
                    'raw_data': json.dumps(keywords_data),
                    'ttl': ttl_timestamp,
                    'timestamp': timestamp
                }
                batch.put_item(Item=item)
            except Exception as e:
                logger.error(f"Error writing trends data for {keyword}: {str(e)}")


def publish_event(timestamp: str) -> None:
    """Publish EventBridge event."""
    try:
        event = {
            'Source': 'mvt.ingestion.inequality',
            'DetailType': 'TrendsDataUpdated',
            'EventBusName': EVENT_BUS_NAME,
            'Detail': json.dumps({
                'keywords_count': len(DISTRESS_KEYWORDS),
                'timestamp': timestamp
            })
        }
        events.put_events(Entries=[event])
        logger.info("Published TrendsDataUpdated event")
    except Exception as e:
        logger.error(f"Error publishing event: {str(e)}")
        raise


def lambda_handler(event, context):
    """Main Lambda handler."""
    logger.info("Starting Google Trends data poller")

    try:
        logger.info(f"Fetching trends for {len(DISTRESS_KEYWORDS)} keywords")
        trends_data = fetch_trends_data(DISTRESS_KEYWORDS)

        if trends_data.get('success'):
            write_to_dynamodb(trends_data)
            timestamp = datetime.utcnow().isoformat()
            publish_event(timestamp)

            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'Trends polling completed successfully',
                    'keywords_processed': len(DISTRESS_KEYWORDS)
                })
            }
        else:
            logger.error(f"Trends fetch failed: {trends_data.get('error')}")
            return {
                'statusCode': 500,
                'body': json.dumps({'error': trends_data.get('error')})
            }

    except Exception as e:
        logger.error(f"Unhandled error in lambda_handler: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
