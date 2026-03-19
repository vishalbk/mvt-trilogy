import json
import os
from datetime import datetime, timedelta
import boto3
import yfinance as yf
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')
events = boto3.client('events')

SIGNALS_TABLE = os.environ.get('SIGNALS_TABLE')
EVENT_BUS_NAME = os.environ.get('EVENT_BUS_NAME')

# Risk gauge tickers
RISK_TICKERS = {
    '^VIX': 'fear_gauge',
    'GLD': 'gold_etf',
    'TLT': 'treasury_etf',
    'VWOB': 'em_bond_etf',
    'EMB': 'em_bond_etf'
}

# Currency pairs
FX_PAIRS = {
    'USDBRL=X': 'USD/BRL',
    'USDTRY=X': 'USD/TRY',
    'USDARS=X': 'USD/ARS',
    'USDEGP=X': 'USD/EGP',
    'USDPKR=X': 'USD/PKR',
    'USDNGN=X': 'USD/NGN',
    'USDZAR=X': 'USD/ZAR',
    'USDMXN=X': 'USD/MXN',
    'USDIDR=X': 'USD/IDR',
    'USDINR=X': 'USD/INR'
}


def fetch_price_data(ticker: str, max_retries: int = 3) -> dict:
    """Fetch price data for a ticker using yfinance."""
    for attempt in range(max_retries):
        try:
            data = yf.Ticker(ticker)
            hist = data.history(period='30d')

            if len(hist) < 2:
                logger.warning(f"Insufficient data for {ticker}")
                return {}

            current_price = hist['Close'].iloc[-1]
            prev_price = hist['Close'].iloc[-2]
            price_30d_ago = hist['Close'].iloc[0]

            daily_change_pct = ((current_price - prev_price) / prev_price * 100) if prev_price != 0 else 0
            monthly_change_pct = ((current_price - price_30d_ago) / price_30d_ago * 100) if price_30d_ago != 0 else 0

            return {
                'success': True,
                'current_price': float(current_price),
                'daily_change_pct': float(daily_change_pct),
                'monthly_change_pct': float(monthly_change_pct)
            }

        except Exception as e:
            logger.warning(f"Attempt {attempt + 1} failed for {ticker}: {str(e)}")
            if attempt < max_retries - 1:
                continue
            logger.error(f"Failed to fetch data for {ticker} after {max_retries} attempts")
            return {'success': False, 'error': str(e)}


def write_to_dynamodb(ticker: str, price_data: dict, dashboard: str, timestamp: str) -> None:
    """Write price data to DynamoDB."""
    if not price_data.get('success'):
        logger.warning(f"Skipping write for {ticker} due to fetch failure")
        return

    table = dynamodb.Table(SIGNALS_TABLE)
    ttl_timestamp = int((datetime.utcnow() + timedelta(days=30)).timestamp())

    try:
        item = {
            'dashboard': dashboard,
            'sort_key': f"yfinance#{ticker}#{timestamp}",
            'source': 'yfinance',
            'ticker': ticker,
            'current_price': price_data['current_price'],
            'daily_change_pct': price_data['daily_change_pct'],
            'monthly_change_pct': price_data['monthly_change_pct'],
            'raw_data': json.dumps(price_data),
            'ttl': ttl_timestamp,
            'timestamp': timestamp
        }
        table.put_item(Item=item)
        logger.info(f"Wrote data for {ticker} to {dashboard}")
    except Exception as e:
        logger.error(f"Error writing {ticker} to DynamoDB: {str(e)}")


def publish_event(tickers_processed: list, timestamp: str) -> None:
    """Publish EventBridge event."""
    try:
        event = {
            'Source': 'mvt.ingestion.sentiment',
            'DetailType': 'PriceDataUpdated',
            'EventBusName': EVENT_BUS_NAME,
            'Detail': json.dumps({
                'tickers_processed': tickers_processed,
                'timestamp': timestamp
            })
        }
        events.put_events(Entries=[event])
        logger.info(f"Published PriceDataUpdated event for {len(tickers_processed)} tickers")
    except Exception as e:
        logger.error(f"Error publishing event: {str(e)}")
        raise


def lambda_handler(event, context):
    """Main Lambda handler."""
    logger.info("Starting yfinance market data streamer")

    try:
        timestamp = datetime.utcnow().isoformat()
        tickers_processed = []

        # Process risk gauge tickers (sentiment_seismic dashboard)
        logger.info("Processing risk gauge tickers")
        for ticker, ticker_type in RISK_TICKERS.items():
            price_data = fetch_price_data(ticker)
            if price_data.get('success'):
                write_to_dynamodb(ticker, price_data, 'sentiment_seismic', timestamp)
                tickers_processed.append(ticker)
            else:
                logger.error(f"Failed to fetch {ticker}")

        # Process FX pairs (sovereign_dominoes dashboard)
        logger.info("Processing currency pairs")
        for ticker, pair_name in FX_PAIRS.items():
            price_data = fetch_price_data(ticker)
            if price_data.get('success'):
                write_to_dynamodb(ticker, price_data, 'sovereign_dominoes', timestamp)
                tickers_processed.append(ticker)
            else:
                logger.error(f"Failed to fetch {ticker}")

        if tickers_processed:
            publish_event(tickers_processed, timestamp)

            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'yfinance streaming completed',
                    'tickers_processed': tickers_processed
                })
            }
        else:
            logger.error("No tickers processed successfully")
            return {
                'statusCode': 500,
                'body': json.dumps({'error': 'No tickers processed'})
            }

    except Exception as e:
        logger.error(f"Unhandled error in lambda_handler: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
