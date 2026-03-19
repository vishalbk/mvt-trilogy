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
    'gdelt_conflicts': 0.25,
    'sanctions': 0.20
}


def get_latest_sentiment_signals() -> dict:
    """Fetch latest sentiment signals."""
    table = dynamodb.Table(SIGNALS_TABLE)
    signals = {
        'vix': None,
        'finnhub_sentiment': None,
        'gdelt_events': []
    }

    try:
        response = table.query(
            KeyConditionExpression='dashboard = :dashboard',
            ExpressionAttributeValues={':dashboard': 'sentiment_seismic'},
            ScanIndexForward=False,
            Limit=100
        )

        for item in response.get('Items', []):
            sort_key = item.get('sort_key', '')

            if '^VIX' in sort_key and signals['vix'] is None:
                signals['vix'] = float(item.get('current_price', 0))

            elif 'finnhub' in sort_key and signals['finnhub_sentiment'] is None:
                sentiment_data = item.get('aggregate_sentiment', {})
                signals['finnhub_sentiment'] = sentiment_data

            elif 'gdelt' in sort_key:
                signals['gdelt_events'].append(item)

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


def compute_vix_contribution(vix: float) -> float:
    """Compute VIX contribution to trigger probability (15-45 range → 0-100)."""
    if vix is None:
        return 0.0

    # Normalize VIX from 15-45 range to 0-100
    vix_normalized = normalize_value(vix, 15, 45)
    return vix_normalized


def compute_bearish_contribution(sentiment: dict) -> float:
    """Compute Finnhub bearish percentage contribution."""
    if not sentiment:
        return 0.0

    bearish_pct = sentiment.get('bearish_pct', 0)
    return float(bearish_pct)


def count_gdelt_conflicts(events: list) -> int:
    """Count GDELT conflict/sanctions events."""
    conflict_codes = ['14', '17', '18', '19', '20']
    conflict_count = 0

    for event in events:
        event_code = event.get('event_code', '')
        if event_code in conflict_codes:
            conflict_count += 1

    return conflict_count


def count_sanctions_activity(events: list) -> int:
    """Count new sanctions entities."""
    sanctions_count = 0
    seen_entities = set()

    for event in events:
        event_code = event.get('event_code', '')
        if event_code in ['19', '20']:  # Sanctions/Threats
            actor1 = event.get('actor1_country', '')
            actor2 = event.get('actor2_country', '')
            key = f"{actor1}|{actor2}"

            if key not in seen_entities:
                sanctions_count += 1
                seen_entities.add(key)

    return sanctions_count


def compute_trigger_probability(signals: dict) -> float:
    """Compute trigger probability (0-100%)."""
    probability = 0.0

    # VIX contribution (15-45 range → 0-100)
    vix_score = compute_vix_contribution(signals['vix'])
    probability += vix_score * WEIGHTS['vix']
    logger.info(f"VIX contribution: {vix_score * WEIGHTS['vix']:.2f}")

    # Finnhub bearish contribution
    finnhub_score = compute_bearish_contribution(signals['finnhub_sentiment'])
    probability += finnhub_score * WEIGHTS['finnhub_bearish']
    logger.info(f"Finnhub bearish contribution: {finnhub_score * WEIGHTS['finnhub_bearish']:.2f}")

    # GDELT conflict count (0-200 events → 0-100)
    conflict_count = count_gdelt_conflicts(signals['gdelt_events'])
    conflict_score = normalize_value(float(conflict_count), 0, 200)
    probability += conflict_score * WEIGHTS['gdelt_conflicts']
    logger.info(f"GDELT conflicts contribution: {conflict_score * WEIGHTS['gdelt_conflicts']:.2f}")

    # Sanctions activity (count unique pairs)
    sanctions_count = count_sanctions_activity(signals['gdelt_events'])
    sanctions_score = normalize_value(float(sanctions_count), 0, 100)
    probability += sanctions_score * WEIGHTS['sanctions']
    logger.info(f"Sanctions activity contribution: {sanctions_score * WEIGHTS['sanctions']:.2f}")

    return min(100, max(0, probability))


def write_to_state_table(probability: float, timestamp: str) -> None:
    """Write trigger probability to dashboard state table."""
    table = dynamodb.Table(DASHBOARD_STATE_TABLE)

    try:
        item = {
            'dashboard': 'sentiment_seismic',
            'panel': 'trigger_probability',
            'probability': Decimal(str(round(probability, 2))),
            'timestamp': timestamp,
            'last_updated': datetime.utcnow().isoformat()
        }
        table.put_item(Item=item)
        logger.info(f"Wrote trigger probability {probability:.2f} to dashboard state table")
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

        # Write to state table
        write_to_state_table(trigger_probability, timestamp)

        # Publish event
        publish_event(trigger_probability, timestamp)

        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Sentiment aggregation completed',
                'trigger_probability': round(trigger_probability, 2)
            })
        }

    except Exception as e:
        logger.error(f"Unhandled error in lambda_handler: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
