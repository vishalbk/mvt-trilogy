import json
import os
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
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

GDELT_BASE_URL = 'http://api.gdeltproject.org/api/v2/doc/doc'

CRISIS_KEYWORDS = ['economic crisis', 'financial crisis', 'debt crisis', 'recession', 'collapse', 'default', 'sanctions']


def fetch_gdelt_events(max_retries: int = 3) -> list:
    """Fetch GDELT GKG events using public API."""
    for attempt in range(max_retries):
        try:
            url = f"{GDELT_BASE_URL}?query=economic%20crisis&mode=artlist&maxrecords=10&format=json"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                articles = data.get('articles', []) if isinstance(data, dict) else []
                return articles[:10]
        except urllib.error.URLError as e:
            logger.warning(f"Attempt {attempt + 1} failed: {str(e)}")
            if attempt < max_retries - 1:
                continue
            return []
    return []


def compute_event_sentiment(article: dict) -> float:
    """
    Compute sentiment score (0-100) based on article content.
    Check for crisis keywords in title and description.
    """
    title = article.get('title', '').lower()
    description = article.get('description', '').lower()
    content = title + ' ' + description

    crisis_count = sum(1 for kw in CRISIS_KEYWORDS if kw in content)

    # More crisis keywords = higher score (worse sentiment)
    if crisis_count == 0:
        return 30.0
    elif crisis_count == 1:
        return 55.0
    elif crisis_count == 2:
        return 75.0
    else:
        return 90.0


def write_to_dynamodb(articles: list) -> None:
    """Write GDELT articles to DynamoDB."""
    table = dynamodb.Table(SIGNALS_TABLE)
    ttl_timestamp = int(time.time()) + 30*86400
    timestamp = datetime.utcnow().isoformat()

    with table.batch_writer() as batch:
        for idx, article in enumerate(articles):
            try:
                title = article.get('title', 'Unknown')
                url = article.get('url', '')
                publish_date = article.get('publishdate', '0')

                sentiment_score = compute_event_sentiment(article)
                signal_id = f"gdelt#{publish_date}#{idx}"

                item = {
                    'dashboard': 'sentiment_seismic',
                    'signalId_timestamp': signal_id,
                    'source': 'gdelt',
                    'value': Decimal(str(sentiment_score)),
                    'raw_data': {
                        'title': title,
                        'url': url,
                        'publish_date': publish_date,
                        'sentiment_score': Decimal(str(sentiment_score))
                    },
                    'ttl': ttl_timestamp
                }
                batch.put_item(Item=item)
                logger.info(f"Wrote GDELT article: {title[:50]}... (sentiment={sentiment_score})")
            except Exception as e:
                logger.error(f"Error writing article {idx}: {str(e)}")


def compute_aggregate_sentiment(articles: list) -> float:
    """Compute average sentiment across articles."""
    if not articles:
        return 50.0

    scores = [compute_event_sentiment(a) for a in articles]
    return sum(scores) / len(scores) if scores else 50.0


def publish_event(event_count: int, sentiment_score: float, timestamp: str) -> None:
    """Publish EventBridge event."""
    try:
        event = {
            'Source': 'mvt.ingestion.sentiment',
            'DetailType': 'GDELTEventsUpdated',
            'EventBusName': EVENT_BUS_NAME,
            'Detail': json.dumps({
                'event_count': event_count,
                'sentiment_score': sentiment_score,
                'timestamp': timestamp
            })
        }
        events.put_events(Entries=[event])
        logger.info(f"Published GDELTEventsUpdated: {event_count} events, sentiment={sentiment_score}")
    except Exception as e:
        logger.error(f"Error publishing event: {str(e)}")


def handler(event, context):
    """Main Lambda handler."""
    logger.info("Starting GDELT event querier")

    try:
        logger.info("Fetching GDELT economic crisis events")
        articles = fetch_gdelt_events()

        if articles:
            write_to_dynamodb(articles)
            timestamp = datetime.utcnow().isoformat()
            sentiment_score = compute_aggregate_sentiment(articles)
            publish_event(len(articles), sentiment_score, timestamp)

            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'GDELT query completed',
                    'events_processed': len(articles),
                    'sentiment_score': sentiment_score
                })
            }
        else:
            logger.info("No GDELT events found")
            return {
                'statusCode': 200,
                'body': json.dumps({'message': 'No events found', 'events_processed': 0})
            }

    except Exception as e:
        logger.error(f"Unhandled error: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
