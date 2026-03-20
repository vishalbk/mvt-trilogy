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

# Composite score weights
WEIGHTS = {
    'credit_delinquency': 0.25,
    'trends_distress': 0.25,
    'debt_service': 0.20,
    'financial_conditions': 0.15,
    'trend_velocity': 0.15
}


def get_latest_signals() -> dict:
    """Fetch latest signals from inequality_pulse dashboard."""
    table = dynamodb.Table(SIGNALS_TABLE)
    signals = {
        'credit_delinquency': None,
        'debt_service': None,
        'financial_conditions': None,
        'trends': []
    }

    try:
        # Query for FRED signals
        response = table.query(
            KeyConditionExpression='dashboard = :dashboard',
            ExpressionAttributeValues={':dashboard': 'inequality_pulse'},
            ScanIndexForward=False,
            Limit=50
        )

        for item in response.get('Items', []):
            sort_key = item.get('signalId_timestamp', '')

            if 'DRCCLACBS' in sort_key and signals['credit_delinquency'] is None:
                signals['credit_delinquency'] = float(item.get('value', 0))
            elif 'TDSP' in sort_key and signals['debt_service'] is None:
                signals['debt_service'] = float(item.get('value', 0))
            elif 'NFCIC' in sort_key and signals['financial_conditions'] is None:
                signals['financial_conditions'] = float(item.get('value', 0))
            elif 'trends#' in sort_key:
                signals['trends'].append(item)

        return signals

    except Exception as e:
        logger.error(f"Error fetching signals: {str(e)}")
        return signals


def normalize_value(value: float, min_val: float, max_val: float) -> float:
    """Normalize a value to 0-100 scale."""
    if value is None or max_val == min_val:
        return 0.0

    normalized = ((value - min_val) / (max_val - min_val)) * 100
    return max(0, min(100, normalized))


def compute_trends_distress_score(trends: list) -> float:
    """Compute composite distress score from trends."""
    if not trends:
        return 0.0

    # Average the trends as a proxy for distress composite
    # In production, extract actual values from Google Trends data
    return min(100, len(trends) * 5)  # Simplified: 5 points per keyword


def compute_trend_velocity(trends: list) -> float:
    """Compute rate of change in trend signals."""
    if len(trends) < 2:
        return 0.0

    # Simplified: compute change rate
    # In production, compare current vs previous period
    return min(100, len(trends) * 3)  # Simplified calculation


def compute_inequality_score(signals: dict) -> float:
    """Compute composite inequality distress score (0-100)."""
    score = 0.0

    # Credit card delinquency (0-20% range normalized to 0-100)
    if signals['credit_delinquency'] is not None:
        delinq_normalized = normalize_value(signals['credit_delinquency'], 0, 20)
        score += delinq_normalized * WEIGHTS['credit_delinquency']
        logger.info(f"Credit delinquency contribution: {delinq_normalized * WEIGHTS['credit_delinquency']:.2f}")

    # Trends distress composite
    trends_score = compute_trends_distress_score(signals['trends'])
    score += trends_score * WEIGHTS['trends_distress']
    logger.info(f"Trends distress contribution: {trends_score * WEIGHTS['trends_distress']:.2f}")

    # Debt service ratio (0-20% range normalized to 0-100)
    if signals['debt_service'] is not None:
        debt_service_normalized = normalize_value(signals['debt_service'], 0, 20)
        score += debt_service_normalized * WEIGHTS['debt_service']
        logger.info(f"Debt service contribution: {debt_service_normalized * WEIGHTS['debt_service']:.2f}")

    # Financial conditions credit index (normalized)
    if signals['financial_conditions'] is not None:
        fc_normalized = normalize_value(signals['financial_conditions'], -100, 100)
        score += fc_normalized * WEIGHTS['financial_conditions']
        logger.info(f"Financial conditions contribution: {fc_normalized * WEIGHTS['financial_conditions']:.2f}")

    # Trend velocity
    velocity_score = compute_trend_velocity(signals['trends'])
    score += velocity_score * WEIGHTS['trend_velocity']
    logger.info(f"Trend velocity contribution: {velocity_score * WEIGHTS['trend_velocity']:.2f}")

    return min(100, max(0, score))


def write_to_state_table(score: float, timestamp: str) -> None:
    """Write composite score to dashboard state table."""
    table = dynamodb.Table(DASHBOARD_STATE_TABLE)

    try:
        item = {
            'dashboard': 'inequality_pulse',
            'panel': 'composite_score',
            'score': Decimal(str(round(score, 2))),
            'timestamp': timestamp,
            'last_updated': datetime.utcnow().isoformat()
        }
        table.put_item(Item=item)
        logger.info(f"Wrote inequality score {score:.2f} to dashboard state table")
    except Exception as e:
        logger.error(f"Error writing to state table: {str(e)}")
        raise


def publish_event(score: float, timestamp: str) -> None:
    """Publish EventBridge event."""
    try:
        event = {
            'Source': 'mvt.processing.score',
            'DetailType': 'InequalityScoreUpdated',
            'EventBusName': EVENT_BUS_NAME,
            'Detail': json.dumps({
                'score': round(score, 2),
                'timestamp': timestamp
            })
        }
        events.put_events(Entries=[event])
        logger.info(f"Published InequalityScoreUpdated event with score {score:.2f}")
    except Exception as e:
        logger.error(f"Error publishing event: {str(e)}")
        raise


def lambda_handler(event, context):
    """Main Lambda handler."""
    logger.info("Starting inequality scorer")

    try:
        timestamp = datetime.utcnow().isoformat()

        # Fetch latest signals
        logger.info("Fetching latest inequality signals")
        signals = get_latest_signals()

        # Compute composite score
        inequality_score = compute_inequality_score(signals)
        logger.info(f"Computed inequality score: {inequality_score:.2f}")

        # Write to state table
        write_to_state_table(inequality_score, timestamp)

        # Publish event
        publish_event(inequality_score, timestamp)

        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Inequality scoring completed',
                'score': round(inequality_score, 2)
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
