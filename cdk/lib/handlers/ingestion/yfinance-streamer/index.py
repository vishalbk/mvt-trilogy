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

# Tickers to fetch
TICKERS = {
    '%5EVIX': {'dashboard': 'sentiment_seismic', 'name': 'VIX'},
    'GC%3DF': {'dashboard': 'sentiment_seismic', 'name': 'Gold'},
    'VWOB': {'dashboard': 'sovereign_dominoes', 'name': 'EM ETF'}
}


def fetch_price_data(ticker_encoded: str, ticker_name: str, max_retries: int = 3) -> dict:
    """Fetch latest price from Yahoo Finance v8 API."""
    for attempt in range(max_retries):
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_encoded}?range=1d&interval=1m"
            req = urllib.request.Request(
                url,
                headers={'User-Agent': 'Mozilla/5.0'}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))

                if data.get('chart', {}).get('result'):
                    result = data['chart']['result'][0]
                    quotes = result.get('timestamp', [])
                    closes = result.get('indicators', {}).get('quote', [{}])[0].get('close', [])

                    # Get latest valid close price
                    for i in range(len(closes) - 1, -1, -1):
                        if closes[i] is not None:
                            return {
                                'success': True,
                                'price': float(closes[i]),
                                'timestamp': quotes[i] if i < len(quotes) else 0
                            }

                logger.warning(f"No valid price data for {ticker_name}")
                return {'success': False}

        except urllib.error.URLError as e:
            logger.warning(f"Attempt {attempt + 1} failed for {ticker_name}: {str(e)}")
            if attempt < max_retries - 1:
                continue
            return {'success': False, 'error': str(e)}

    return {'success': False}


def price_to_signal_value(ticker_name: str, price: float) -> float:
    """
    Convert raw price to 0-100 signal value.
    VIX: scale 10-30 to 0-100
    Gold: scale 1500-2100 to 0-100
    EM ETF: scale 40-60 to 0-100
    """
    if ticker_name == 'VIX':
        # VIX 10-30 normalized to 0-100
        return max(0, min(100, (price - 10) / 20.0 * 100))
    elif ticker_name == 'Gold':
        # Gold 1500-2100 normalized to 0-100
        return max(0, min(100, (price - 1500) / 600.0 * 100))
    elif ticker_name == 'EM ETF':
        # EM ETF 40-60 normalized to 0-100
        return max(0, min(100, (price - 40) / 20.0 * 100))
    else:
        # Default: raw price as percentage (0-100)
        return max(0, min(100, price))


def write_to_dynamodb(ticker_name: str, price: float, signal_value: float, dashboard: str) -> None:
    """Write price data to DynamoDB."""
    table = dynamodb.Table(SIGNALS_TABLE)
    ttl_timestamp = int(time.time()) + 30*86400
    timestamp = datetime.utcnow().isoformat()

    try:
        signal_id = f"yahoo#{ticker_name}#{int(time.time())}"
        item = {
            'dashboard': dashboard,
            'signalId_timestamp': signal_id,
            'source': 'yahoo',
            'value': signal_value,
            'raw_data': {
                'ticker': ticker_name,
                'price': price,
                'signal_value': signal_value
            },
            'ttl': ttl_timestamp
        }
        table.put_item(Item=item)
        logger.info(f"Wrote {ticker_name}: price={price}, signal={signal_value}")
    except Exception as e:
        logger.error(f"Error writing {ticker_name} to DynamoDB: {str(e)}")


def publish_event(tickers_processed: list, timestamp: str) -> None:
    """Publish EventBridge event."""
    try:
        event = {
            'Source': 'mvt.ingestion.sentiment',
            'DetailType': 'PriceDataUpdated',
            'EventBusName': EVENT_BUS_NAME,
            'Detail': json.dumps({
                'tickers_processed': tickers_processed,
                'count': len(tickers_processed),
                'timestamp': timestamp
            })
        }
        events.put_events(Entries=[event])
        logger.info(f"Published PriceDataUpdated for {len(tickers_processed)} tickers")
    except Exception as e:
        logger.error(f"Error publishing event: {str(e)}")


def handler(event, context):
    """Main Lambda handler."""
    logger.info("Starting Yahoo Finance data streamer")

    try:
        timestamp = datetime.utcnow().isoformat()
        tickers_processed = []

        for ticker_encoded, config in TICKERS.items():
            try:
                logger.info(f"Fetching {config['name']}")
                price_data = fetch_price_data(ticker_encoded, config['name'])

                if price_data.get('success'):
                    price = price_data['price']
                    signal_value = price_to_signal_value(config['name'], price)
                    write_to_dynamodb(config['name'], price, signal_value, config['dashboard'])
                    tickers_processed.append(config['name'])
                else:
                    logger.warning(f"Failed to fetch {config['name']}")
            except Exception as e:
                logger.error(f"Error processing {config['name']}: {str(e)}")

        if tickers_processed:
            publish_event(tickers_processed, timestamp)
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'Yahoo Finance streaming completed',
                    'tickers_processed': tickers_processed
                })
            }
        else:
            logger.warning("No tickers processed")
            return {
                'statusCode': 200,
                'body': json.dumps({'message': 'No tickers processed', 'count': 0})
            }

    except Exception as e:
        logger.error(f"Unhandled error: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
