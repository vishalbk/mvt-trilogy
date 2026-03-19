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
FINNHUB_API_KEY = os.environ.get('FINNHUB_API_KEY')

FINNHUB_BASE_URL = 'https://finnhub.io/api/v1'
TRACKED_SYMBOLS = ['AAPL', 'MSFT', 'AMZN', 'GOOGL', 'NVDA', 'JPM', 'GS']


def fetch_news_sentiment(max_retries: int = 3) -> dict:
    """Fetch news sentiment for tracked symbols."""
    for attempt in range(max_retries):
        try:
            symbols_param = ','.join(TRACKED_SYMBOLS)
            url = f"{FINNHUB_BASE_URL}/news-sentiment?symbol={symbols_param}&token={FINNHUB_API_KEY}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                return data
        except urllib.error.URLError as e:
            logger.warning(f"Attempt {attempt + 1} failed to fetch news sentiment: {str(e)}")
            if attempt < max_retries - 1:
                continue
            raise
    return {}


def fetch_general_news(max_retries: int = 3) -> dict:
    """Fetch general market news."""
    for attempt in range(max_retries):
        try:
            url = f"{FINNHUB_BASE_URL}/news?category=general&token={FINNHUB_API_KEY}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                return data
        except urllib.error.URLError as e:
            logger.warning(f"Attempt {attempt + 1} failed to fetch general news: {str(e)}")
            if attempt < max_retries - 1:
                continue
            raise
    return {}


def compute_aggregate_sentiment(sentiment_data: dict) -> dict:
    """Compute aggregate sentiment metrics."""
    if not sentiment_data or 'data' not in sentiment_data:
        logger.warning("No sentiment data available")
        return {
            'bullish_pct': 0,
            'bearish_pct': 0,
            'neutral_pct': 0,
            'symbol_count': 0
        }

    sentiments = []
    for symbol_data in sentiment_data.get('data', []):
        if 'sentiment' in symbol_data:
            sentiments.append(symbol_data['sentiment'])

    if not sentiments:
        return {
            'bullish_pct': 0,
            'bearish_pct': 0,
            'neutral_pct': 0,
            'symbol_count': 0
        }

    bullish_count = sum(1 for s in sentiments if s > 0.5)
    bearish_count = sum(1 for s in sentiments if s < 0.5)
    neutral_count = len(sentiments) - bullish_count - bearish_count

    total = len(sentiments)
    return {
        'bullish_pct': (bullish_count / total) * 100 if total > 0 else 0,
        'bearish_pct': (bearish_count / total) * 100 if total > 0 else 0,
        'neutral_pct': (neutral_count / total) * 100 if total > 0 else 0,
        'symbol_count': total
    }


def write_to_dynamodb(sentiment_data: dict, timestamp: str) -> None:
    """Write sentiment data to DynamoDB."""
    table = dynamodb.Table(SIGNALS_TABLE)
    ttl_timestamp = int((datetime.utcnow() + timedelta(days=30)).timestamp())

    try:
        item = {
            'dashboard': 'sentiment_seismic',
            'sort_key': f"finnhub#sentiment#{timestamp}",
            'source': 'finnhub',
            'aggregate_sentiment': sentiment_data,
            'raw_data': json.dumps(sentiment_data),
            'ttl': ttl_timestamp,
            'timestamp': timestamp
        }
        table.put_item(Item=item)
        logger.info("Sentiment data written to DynamoDB")
    except Exception as e:
        logger.error(f"Error writing to DynamoDB: {str(e)}")
        raise


def publish_event(sentiment_data: dict, timestamp: str) -> None:
    """Publish EventBridge event."""
    try:
        event = {
            'Source': 'mvt.ingestion.sentiment',
            'DetailType': 'SentimentUpdated',
            'EventBusName': EVENT_BUS_NAME,
            'Detail': json.dumps({
                'bullish_pct': sentiment_data.get('bullish_pct', 0),
                'bearish_pct': sentiment_data.get('bearish_pct', 0),
                'neutral_pct': sentiment_data.get('neutral_pct', 0),
                'timestamp': timestamp
            })
        }
        events.put_events(Entries=[event])
        logger.info("Published SentimentUpdated event")
    except Exception as e:
        logger.error(f"Error publishing event: {str(e)}")
        raise


def lambda_handler(event, context):
    """Main Lambda handler."""
    logger.info("Starting Finnhub sentiment connector")

    try:
        logger.info("Fetching news sentiment data")
        sentiment_data = fetch_news_sentiment()

        logger.info("Fetching general market news")
        news_data = fetch_general_news()

        timestamp = datetime.utcnow().isoformat()
        aggregate_sentiment = compute_aggregate_sentiment(sentiment_data)

        write_to_dynamodb(aggregate_sentiment, timestamp)
        publish_event(aggregate_sentiment, timestamp)

        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Finnhub connector completed successfully',
                'aggregate_sentiment': aggregate_sentiment
            })
        }

    except Exception as e:
        logger.error(f"Unhandled error in lambda_handler: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
