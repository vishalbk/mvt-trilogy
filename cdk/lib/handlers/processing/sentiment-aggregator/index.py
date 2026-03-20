import json
import os
from datetime import datetime
import boto3
import logging
from decimal import Decimal

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')
events = boto3.client('events')

SIGNALS_TABLE = os.environ.get('SIGNALS_TABLE')
DASHBOARD_STATE_TABLE = os.environ.get('DASHBOARD_STATE_TABLE')
EVENT_BUS_NAME = os.environ.get('EVENT_BUS_NAME')

# Trigger probability weights
WEIGHTS = {
    'vix': 0.30,
    'finnhub_bearish': 0.25,
    'gdelt_crisis': 0.25,
    'gdelt_severity': 0.20
}


def get_latest_sentiment_signals() -> dict:
    """Fetch latest sentiment signals from signals table.

    Pollers write to dashboard='sentiment_seismic' with signalId_timestamp as sort key:
    - yfinance: signalId_timestamp='yahoo#VIX#<ts>', value=<normalized>, raw_data.price=<actual VIX price>
    - finnhub:  signalId_timestamp='finnhub#<ticker>#<ts>', value=<sentiment_score 0-100>, raw_data.sentiment_score
    - gdelt:    signalId_timestamp='gdelt#<date>#<idx>', value=<sentiment_score>, raw_data.sentiment_score
    """
    table = dynamodb.Table(SIGNALS_TABLE)
    signals = {
        'vix_price': None,
        'finnhub_articles': [],
        'gdelt_articles': []
    }

    try:
        response = table.query(
            KeyConditionExpression='dashboard = :dashboard',
            ExpressionAttributeValues={':dashboard': 'sentiment_seismic'},
            ScanIndexForward=False,
            Limit=100
        )

        for item in response.get('Items', []):
            sk = item.get('signalId_timestamp', '')

            if 'yahoo#VIX' in sk and signals['vix_price'] is None:
                # Get actual VIX price from raw_data, fall back to value
                raw_data = item.get('raw_data', {})
                price = raw_data.get('price')
                if price is not None:
                    signals['vix_price'] = float(price)
                else:
                    signals['vix_price'] = float(item.get('value', 0))
                logger.info(f"Found VIX price: {signals['vix_price']}")

            elif sk.startswith('finnhub#'):
                score = float(item.get('value', 50))
                signals['finnhub_articles'].append({
                    'signalId': sk,
                    'sentiment_score': score
                })

            elif sk.startswith('gdelt#'):
                raw_data = item.get('raw_data', {})
                score = float(raw_data.get('sentiment_score', item.get('value', 50)))
                signals['gdelt_articles'].append({
                    'signalId': sk,
                    'sentiment_score': score
                })

        logger.info(f"Signals found: VIX={'yes' if signals['vix_price'] else 'no'}, "
                    f"finnhub_articles={len(signals['finnhub_articles'])}, "
                    f"gdelt_articles={len(signals['gdelt_articles'])}")
        return signals

    except Exception as e:
        logger.error(f"Error fetching sentiment signals: {str(e)}")
        return signals


def normalize_value(value: float, min_val: float, max_val: float) -> float:
    """Normalize a value to 0-100 scale."""
    if value is None or max_val == min_val:
        return 0.0

    normalized = ((value - min_val) / (max_val - min_val)) * 100
    return max(0, min(100, normalized))


def compute_vix_contribution(vix_price: float) -> float:
    """Compute VIX contribution to trigger probability (15-45 range → 0-100)."""
    if vix_price is None:
        return 0.0

    # Normalize VIX from 15-45 range to 0-100
    vix_normalized = normalize_value(vix_price, 15, 45)
    return vix_normalized


def compute_bearish_contribution(articles: list) -> float:
    """Compute bearish percentage from Finnhub article sentiments.

    Articles have sentiment_score 0-100 where:
    - < 40 = bearish
    - 40-60 = neutral
    - > 60 = bullish
    """
    if not articles:
        return 0.0

    bearish_count = sum(1 for a in articles if a['sentiment_score'] < 40)
    bearish_pct = (bearish_count / len(articles)) * 100
    logger.info(f"Finnhub bearish: {bearish_count}/{len(articles)} = {bearish_pct:.1f}%")
    return bearish_pct


def compute_gdelt_crisis_score(articles: list) -> float:
    """Compute crisis score from GDELT articles.

    Articles have sentiment_score where higher = more crisis-related.
    Score > 50 indicates significant crisis content.
    """
    if not articles:
        return 0.0

    crisis_articles = sum(1 for a in articles if a['sentiment_score'] > 50)
    # Normalize: 0-10 crisis articles → 0-100
    crisis_score = normalize_value(float(crisis_articles), 0, 10)
    logger.info(f"GDELT crisis articles: {crisis_articles}/{len(articles)}, score: {crisis_score:.1f}")
    return crisis_score


def compute_gdelt_severity(articles: list) -> float:
    """Compute average severity from GDELT articles.

    Higher average sentiment_score = more severe crisis sentiment.
    """
    if not articles:
        return 0.0

    avg_score = sum(a['sentiment_score'] for a in articles) / len(articles)
    # Scores are already 0-100, use directly
    logger.info(f"GDELT avg severity: {avg_score:.1f}")
    return avg_score


def compute_trigger_probability(signals: dict) -> float:
    """Compute trigger probability (0-100%)."""
    probability = 0.0

    # VIX contribution (15-45 range → 0-100)
    vix_score = compute_vix_contribution(signals['vix_price'])
    probability += vix_score * WEIGHTS['vix']
    logger.info(f"VIX contribution: {vix_score:.2f} * {WEIGHTS['vix']} = {vix_score * WEIGHTS['vix']:.2f}")

    # Finnhub bearish contribution
    finnhub_score = compute_bearish_contribution(signals['finnhub_articles'])
    probability += finnhub_score * WEIGHTS['finnhub_bearish']
    logger.info(f"Finnhub bearish contribution: {finnhub_score:.2f} * {WEIGHTS['finnhub_bearish']} = {finnhub_score * WEIGHTS['finnhub_bearish']:.2f}")

    # GDELT crisis article count (0-10 articles → 0-100)
    gdelt_crisis = compute_gdelt_crisis_score(signals['gdelt_articles'])
    probability += gdelt_crisis * WEIGHTS['gdelt_crisis']
    logger.info(f"GDELT crisis contribution: {gdelt_crisis:.2f} * {WEIGHTS['gdelt_crisis']} = {gdelt_crisis * WEIGHTS['gdelt_crisis']:.2f}")

    # GDELT severity (average sentiment score)
    gdelt_severity = compute_gdelt_severity(signals['gdelt_articles'])
    probability += gdelt_severity * WEIGHTS['gdelt_severity']
    logger.info(f"GDELT severity contribution: {gdelt_severity:.2f} * {WEIGHTS['gdelt_severity']} = {gdelt_severity * WEIGHTS['gdelt_severity']:.2f}")

    return min(100, max(0, probability))


def write_to_state_table(probability: float, vix_price: float, timestamp: str) -> None:
    """Write trigger probability and VIX price to dashboard state table."""
    table = dynamodb.Table(DASHBOARD_STATE_TABLE)

    try:
        item = {
            'dashboard': 'sentiment_seismic',
            'panel': 'trigger_probability',
            'probability': Decimal(str(round(probability, 2))),
            'timestamp': timestamp,
            'last_updated': datetime.utcnow().isoformat()
        }
        if vix_price is not None:
            item['vix_price'] = Decimal(str(round(vix_price, 2)))
        table.put_item(Item=item)
        logger.info(f"Wrote trigger probability {probability:.2f} (VIX: {vix_price}) to dashboard state table")
    except Exception as e:
        logger.error(f"Error writing to state table: {str(e)}")
        raise


def publish_event(probability: float, timestamp: str) -> None:
    """Publish EventBridge event."""
    try:
        event = {
            'Source': 'mvt.processing.score',
            'DetailType': 'SentimentScoreUpdated',
            'EventBusName': EVENT_BUS_NAME,
            'Detail': json.dumps({
                'trigger_probability': round(probability, 2),
                'timestamp': timestamp
            })
        }
        events.put_events(Entries=[event])
        logger.info(f"Published SentimentScoreUpdated event with probability {probability:.2f}%")
    except Exception as e:
        logger.error(f"Error publishing event: {str(e)}")
        raise


def get_sector_signals() -> dict:
    """Fetch signals by sector from signals table.

    Returns a dict mapping sector names to lists of signal values.
    Sectors: financial, energy, technology, commodities, sovereign
    """
    table = dynamodb.Table(SIGNALS_TABLE)
    sectors = {
        'financial': [],
        'energy': [],
        'technology': [],
        'commodities': [],
        'sovereign': []
    }

    try:
        response = table.query(
            KeyConditionExpression='dashboard = :dashboard',
            ExpressionAttributeValues={':dashboard': 'sentiment_seismic'},
            ScanIndexForward=False,
            Limit=100
        )

        for item in response.get('Items', []):
            sk = item.get('signalId_timestamp', '').lower()
            value = float(item.get('value', 50))

            # Classify signals by sector based on signal type
            # Financial: VIX, credit spreads, bank sentiment
            if any(x in sk for x in ['vix', 'credit', 'financial', 'bank', 'insurance']):
                sectors['financial'].append(value)
            # Energy: Oil, gas, utilities, commodity prices
            elif any(x in sk for x in ['oil', 'gas', 'energy', 'utility', 'wti', 'brent']):
                sectors['energy'].append(value)
            # Technology: NASDAQ, tech stocks, crypto sentiment
            elif any(x in sk for x in ['nasdaq', 'tech', 'qqq', 'crypto', 'semiconductor']):
                sectors['technology'].append(value)
            # Commodities: Gold, agriculture, metals
            elif any(x in sk for x in ['gold', 'silver', 'commodity', 'agriculture', 'wheat', 'corn']):
                sectors['commodities'].append(value)
            # Sovereign: Government bonds, FX, sovereign risk
            elif any(x in sk for x in ['sovereign', 'bond', 'treasury', 'fx', 'currency', 'yield']):
                sectors['sovereign'].append(value)

        logger.info(f"Sector signals: {', '.join(f'{k}={len(v)}' for k, v in sectors.items())}")
        return sectors

    except Exception as e:
        logger.error(f"Error fetching sector signals: {str(e)}")
        return sectors


def compute_sector_sentiment_scores(sectors: dict) -> dict:
    """Compute weighted average sentiment score for each sector.

    Returns a dict mapping sector names to dicts with:
    - value: 0-100 sentiment score
    - trend: 'rising', 'falling', or 'stable'
    - change_24h: estimated 24h change
    """
    sector_scores = {}

    for sector_name, values in sectors.items():
        if not values:
            # If no data, return neutral
            sector_scores[sector_name] = {
                'value': 50.0,
                'trend': 'stable',
                'change_24h': 0.0
            }
            logger.info(f"Sector {sector_name}: No signals, defaulting to 50")
        else:
            # Compute weighted average (more recent values weighted higher)
            total = sum(values)
            avg_score = total / len(values)

            # Determine trend based on distribution of scores
            high_scores = sum(1 for v in values if v > 60)
            low_scores = sum(1 for v in values if v < 40)
            high_pct = (high_scores / len(values)) * 100 if values else 0

            if high_pct > 60:
                trend = 'rising'
            elif low_scores / len(values) > 0.6 if values else False:
                trend = 'falling'
            else:
                trend = 'stable'

            # Estimated 24h change (simplified)
            change_24h = (high_pct - 50) * 0.2  # Scale bullish/bearish bias

            sector_scores[sector_name] = {
                'value': round(avg_score, 2),
                'trend': trend,
                'change_24h': round(change_24h, 2)
            }
            logger.info(f"Sector {sector_name}: avg={avg_score:.2f}, trend={trend}, change_24h={change_24h:.2f}")

    return sector_scores


def write_sector_sentiment_to_state(sector_scores: dict, timestamp: str) -> None:
    """Write sector sentiment scores to dashboard state table.

    For each sector, creates an entry with:
    dashboard='sentiment_seismic', metric='sector_{name}', value, trend, change_24h
    """
    table = dynamodb.Table(DASHBOARD_STATE_TABLE)

    try:
        for sector_name, scores in sector_scores.items():
            item = {
                'dashboard': 'sentiment_seismic',
                'metric': f'sector_{sector_name}',
                'value': Decimal(str(scores['value'])),
                'trend': scores['trend'],
                'change_24h': Decimal(str(scores['change_24h'])),
                'timestamp': timestamp,
                'last_updated': datetime.utcnow().isoformat()
            }
            table.put_item(Item=item)
            logger.info(f"Wrote sector {sector_name}: {scores['value']} ({scores['trend']}) to dashboard state")

    except Exception as e:
        logger.error(f"Error writing sector sentiment to state table: {str(e)}")
        raise


def lambda_handler(event, context):
    """Main Lambda handler."""
    logger.info("Starting sentiment aggregator")

    try:
        timestamp = datetime.utcnow().isoformat()

        # Fetch latest sentiment signals
        logger.info("Fetching latest sentiment signals")
        signals = get_latest_sentiment_signals()

        # Compute trigger probability
        trigger_probability = compute_trigger_probability(signals)
        logger.info(f"Computed trigger probability: {trigger_probability:.2f}%")

        # Write trigger probability to state table (include VIX price for frontend display)
        write_to_state_table(trigger_probability, signals['vix_price'], timestamp)

        # Compute sector-level sentiment scores
        logger.info("Computing sector-level sentiment scores")
        sector_signals = get_sector_signals()
        sector_scores = compute_sector_sentiment_scores(sector_signals)
        write_sector_sentiment_to_state(sector_scores, timestamp)
        logger.info(f"Wrote sector sentiment scores to dashboard state")

        # Publish event
        publish_event(trigger_probability, timestamp)

        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Sentiment aggregation completed',
                'trigger_probability': round(trigger_probability, 2),
                'sector_scores': {k: {'value': float(v['value']), 'trend': v['trend']} for k, v in sector_scores.items()}
            })
        }

    except Exception as e:
        logger.error(f"Unhandled error in lambda_handler: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }


# Alias for Lambda handler configuration
handler = lambda_handler
