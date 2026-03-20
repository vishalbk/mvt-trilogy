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
FINNHUB_API_KEY = os.environ.get('FINNHUB_API_KEY')

FINNHUB_BASE_URL = 'https://finnhub.io/api/v1'

POSITIVE_KEYWORDS = ['bull', 'gain', 'surge', 'rally', 'jump', 'rise', 'spike', 'strong', 'good', 'bullish']
NEGATIVE_KEYWORDS = ['bear', 'loss', 'crash', 'drop', 'plunge', 'fall', 'decline', 'weak', 'bad', 'bearish']

TICKERS = ['AAPL', 'MSFT', 'TSLA', 'JPM', 'GLD', 'SPY', 'QQQ']


def fetch_general_news(max_retries: int = 3) -> list:
    """Fetch general market news articles."""
    for attempt in range(max_retries):
        try:
            url = f"{FINNHUB_BASE_URL}/news?category=general&token={FINNHUB_API_KEY}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                return data if isinstance(data, list) else []
        except urllib.error.URLError as e:
            logger.warning(f"Attempt {attempt + 1} failed: {str(e)}")
            if attempt < max_retries - 1:
                continue
            raise
    return []


def fetch_company_news(symbol: str, max_retries: int = 3) -> list:
    """Fetch company-specific news articles for a given ticker symbol."""
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime('%Y-%m-%d')
    today = datetime.utcnow().strftime('%Y-%m-%d')

    for attempt in range(max_retries):
        try:
            url = f"{FINNHUB_BASE_URL}/company-news?symbol={symbol}&from={yesterday}&to={today}&token={FINNHUB_API_KEY}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                return data if isinstance(data, list) else []
        except urllib.error.URLError as e:
            logger.warning(f"Attempt {attempt + 1} failed for {symbol}: {str(e)}")
            if attempt < max_retries - 1:
                continue
            return []
    return []


def compute_sentiment_score(headline: str) -> float:
    """
    Simple sentiment scoring: count positive vs negative keywords.
    Returns 0-100 score where 50 is neutral.
    """
    headline_lower = headline.lower()
    pos_count = sum(1 for kw in POSITIVE_KEYWORDS if kw in headline_lower)
    neg_count = sum(1 for kw in NEGATIVE_KEYWORDS if kw in headline_lower)

    if pos_count + neg_count == 0:
        return 50.0

    total = pos_count + neg_count
    # Map to 0-100: more positive = higher score
    return (pos_count / total) * 100


def classify_sentiment(score: float) -> str:
    """Classify sentiment score into categories."""
    if score > 60:
        return "bullish"
    elif score < 40:
        return "bearish"
    else:
        return "neutral"


def write_to_dynamodb(articles: list) -> None:
    """Write news articles to DynamoDB."""
    table = dynamodb.Table(SIGNALS_TABLE)
    ttl_timestamp = int(time.time()) + 30*86400
    timestamp = datetime.utcnow().isoformat()

    with table.batch_writer() as batch:
        for idx, article in enumerate(articles[:10]):
            try:
                headline = article.get('headline', 'Unknown')
                source = article.get('source', 'unknown')
                url = article.get('url', '')
                dt = article.get('datetime', 0)

                sentiment_score = compute_sentiment_score(headline)
                signal_id = f"finnhub#{source}#{dt}#{idx}"

                item = {
                    'dashboard': 'sentiment_seismic',
                    'signalId_timestamp': signal_id,
                    'source': 'finnhub',
                    'value': Decimal(str(sentiment_score)),
                    'raw_data': {
                        'headline': headline,
                        'source': source,
                        'url': url,
                        'datetime': dt,
                        'sentiment_score': Decimal(str(sentiment_score))
                    },
                    'ttl': ttl_timestamp
                }
                batch.put_item(Item=item)
                logger.info(f"Wrote article: {headline[:50]}... (sentiment={sentiment_score})")
            except Exception as e:
                logger.error(f"Error processing article {idx}: {str(e)}")


def write_ticker_sentiment_to_dynamodb(ticker: str, sentiment_score: float, article_count: int) -> None:
    """Write per-ticker sentiment to DynamoDB."""
    table = dynamodb.Table(SIGNALS_TABLE)
    ttl_timestamp = int(time.time()) + 30*86400
    timestamp = int(time.time())

    try:
        sentiment_classification = classify_sentiment(sentiment_score)
        signal_id = f"finnhub#{ticker}#{timestamp}"

        item = {
            'dashboard': 'sentiment_seismic',
            'signalId_timestamp': signal_id,
            'source': 'finnhub',
            'value': Decimal(str(sentiment_score)),
            'raw_data': {
                'ticker': ticker,
                'sentiment_score': Decimal(str(sentiment_score)),
                'sentiment_class': sentiment_classification,
                'article_count': article_count
            },
            'ttl': ttl_timestamp
        }
        table.put_item(Item=item)
        logger.info(f"Wrote {ticker} sentiment: score={sentiment_score}, class={sentiment_classification}, articles={article_count}")
    except Exception as e:
        logger.error(f"Error writing {ticker} sentiment to DynamoDB: {str(e)}")


def compute_aggregate_sentiment(articles: list) -> float:
    """Compute average sentiment across articles."""
    if not articles:
        return 50.0

    scores = [compute_sentiment_score(a.get('headline', '')) for a in articles[:10]]
    return sum(scores) / len(scores) if scores else 50.0


def publish_event(sentiment_score: float, article_count: int, timestamp: str) -> None:
    """Publish EventBridge event."""
    try:
        event = {
            'Source': 'mvt.ingestion.sentiment',
            'DetailType': 'SentimentUpdated',
            'EventBusName': EVENT_BUS_NAME,
            'Detail': json.dumps({
                'sentiment_score': sentiment_score,
                'article_count': article_count,
                'timestamp': timestamp
            })
        }
        events.put_events(Entries=[event])
        logger.info(f"Published SentimentUpdated: score={sentiment_score}, articles={article_count}")
    except Exception as e:
        logger.error(f"Error publishing event: {str(e)}")


def handler(event, context):
    """Main Lambda handler."""
    logger.info("Starting Finnhub sentiment connector")

    try:
        logger.info("Fetching general market news")
        articles = fetch_general_news()

        if articles:
            write_to_dynamodb(articles)
            timestamp = datetime.utcnow().isoformat()
            sentiment_score = compute_aggregate_sentiment(articles)
            publish_event(sentiment_score, len(articles), timestamp)

        # Fetch and process per-ticker sentiment
        logger.info(f"Fetching company news for {len(TICKERS)} tickers")
        ticker_sentiments = {}

        for ticker in TICKERS:
            try:
                logger.info(f"Fetching company news for {ticker}")
                company_articles = fetch_company_news(ticker)

                if company_articles:
                    ticker_sentiment = compute_aggregate_sentiment(company_articles)
                    ticker_sentiments[ticker] = ticker_sentiment
                    write_ticker_sentiment_to_dynamodb(ticker, ticker_sentiment, len(company_articles))
                else:
                    logger.warning(f"No articles fetched for {ticker}")
            except Exception as e:
                logger.error(f"Error processing {ticker}: {str(e)}")

        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Finnhub connector completed',
                'general_sentiment_score': sentiment_score if articles else None,
                'general_article_count': len(articles) if articles else 0,
                'ticker_sentiments': ticker_sentiments
            })
        }

    except Exception as e:
        logger.error(f"Unhandled error: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
