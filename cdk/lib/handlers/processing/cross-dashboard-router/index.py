import json
import os
from datetime import datetime
import boto3
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')
events = boto3.client('events')

DASHBOARD_STATE_TABLE = os.environ.get('DASHBOARD_STATE_TABLE')
EVENT_BUS_NAME = os.environ.get('EVENT_BUS_NAME')

# Threshold rules for cross-dashboard routing
INEQUALITY_THRESHOLD = 70
SENTIMENT_THRESHOLD = 60
CONTAGION_THRESHOLD = 65
VIX_THRESHOLD = 30


def get_current_scores() -> dict:
    """Fetch current scores from all dashboards."""
    table = dynamodb.Table(DASHBOARD_STATE_TABLE)
    scores = {
        'inequality': None,
        'sentiment': None,
        'contagion': None,
        'vix': None
    }

    try:
        # Get inequality score
        response = table.get_item(
            Key={
                'dashboard': 'inequality_pulse',
                'panel': 'composite_score'
            }
        )
        if 'Item' in response:
            scores['inequality'] = float(response['Item'].get('score', 0))

        # Get sentiment trigger probability
        response = table.get_item(
            Key={
                'dashboard': 'sentiment_seismic',
                'panel': 'trigger_probability'
            }
        )
        if 'Item' in response:
            scores['sentiment'] = float(response['Item'].get('probability', 0))

        # Get contagion risk (max country score)
        response = table.get_item(
            Key={
                'dashboard': 'sovereign_dominoes',
                'panel': 'risk_scores'
            }
        )
        if 'Item' in response:
            country_scores = response['Item'].get('country_scores', {})
            if country_scores:
                scores['contagion'] = max(float(v) for v in country_scores.values())

        # Get VIX level (latest sentiment_seismic data)
        # This would typically come from the most recent yfinance data
        # For now, we'll query the signals table
        from boto3.dynamodb.conditions import Key
        signals_table = dynamodb.Table(os.environ.get('SIGNALS_TABLE'))
        response = signals_table.query(
            KeyConditionExpression='dashboard = :dashboard AND begins_with(sort_key, :vix)',
            ExpressionAttributeValues={
                ':dashboard': 'sentiment_seismic',
                ':vix': 'yfinance#^VIX'
            },
            ScanIndexForward=False,
            Limit=1
        )
        if response.get('Items'):
            scores['vix'] = float(response['Items'][0].get('current_price', 0))

        return scores

    except Exception as e:
        logger.error(f"Error fetching scores: {str(e)}")
        return scores


def publish_cross_dashboard_signal(source_dashboard: str, target_dashboard: str,
                                   signal_type: str, details: dict, timestamp: str) -> None:
    """Publish cross-dashboard signal via EventBridge."""
    try:
        event = {
            'Source': 'mvt.processing.routing',
            'DetailType': f'CrossDashboardSignal_{signal_type}',
            'EventBusName': EVENT_BUS_NAME,
            'Detail': json.dumps({
                'source_dashboard': source_dashboard,
                'target_dashboard': target_dashboard,
                'signal_type': signal_type,
                'details': details,
                'timestamp': timestamp
            })
        }
        events.put_events(Entries=[event])
        logger.info(f"Published cross-dashboard signal: {source_dashboard} -> {target_dashboard}")
    except Exception as e:
        logger.error(f"Error publishing cross-dashboard signal: {str(e)}")
        raise


def route_inequality_to_sentiment(inequality_score: float, timestamp: str) -> None:
    """Route high inequality to sentiment_seismic dashboard."""
    if inequality_score >= INEQUALITY_THRESHOLD:
        logger.info(f"Inequality score {inequality_score:.2f} exceeds threshold {INEQUALITY_THRESHOLD}")
        publish_cross_dashboard_signal(
            source_dashboard='inequality_pulse',
            target_dashboard='sentiment_seismic',
            signal_type='HighDistress',
            details={
                'inequality_score': round(inequality_score, 2),
                'threshold': INEQUALITY_THRESHOLD,
                'message': 'High consumer distress detected - monitoring sentiment for acceleration'
            },
            timestamp=timestamp
        )


def route_sentiment_to_contagion(sentiment_score: float, timestamp: str) -> None:
    """Route high sentiment trigger to sovereign_dominoes dashboard."""
    if sentiment_score >= SENTIMENT_THRESHOLD:
        logger.info(f"Sentiment trigger {sentiment_score:.2f}% exceeds threshold {SENTIMENT_THRESHOLD}%")
        publish_cross_dashboard_signal(
            source_dashboard='sentiment_seismic',
            target_dashboard='sovereign_dominoes',
            signal_type='TriggerActive',
            details={
                'trigger_probability': round(sentiment_score, 2),
                'threshold': SENTIMENT_THRESHOLD,
                'message': 'Market trigger probability elevated - assessing sovereign contagion risk'
            },
            timestamp=timestamp
        )


def route_contagion_to_inequality(contagion_score: float, timestamp: str) -> None:
    """Route high contagion risk back to inequality_pulse (feedback loop)."""
    if contagion_score >= CONTAGION_THRESHOLD:
        logger.info(f"Contagion risk {contagion_score:.2f} exceeds threshold {CONTAGION_THRESHOLD}")
        publish_cross_dashboard_signal(
            source_dashboard='sovereign_dominoes',
            target_dashboard='inequality_pulse',
            signal_type='ContagionFeedback',
            details={
                'contagion_risk': round(contagion_score, 2),
                'threshold': CONTAGION_THRESHOLD,
                'message': 'Sovereign stress could amplify domestic inequality dynamics'
            },
            timestamp=timestamp
        )


def broadcast_vix_alert(vix_level: float, timestamp: str) -> None:
    """Broadcast VIX alert to all dashboards."""
    if vix_level > VIX_THRESHOLD:
        logger.info(f"VIX level {vix_level:.2f} exceeds threshold {VIX_THRESHOLD}")

        for target_dashboard in ['inequality_pulse', 'sentiment_seismic', 'sovereign_dominoes']:
            publish_cross_dashboard_signal(
                source_dashboard='sentiment_seismic',
                target_dashboard=target_dashboard,
                signal_type='VIXAlert',
                details={
                    'vix_level': round(vix_level, 2),
                    'threshold': VIX_THRESHOLD,
                    'message': f'Market volatility elevated (VIX {vix_level:.2f}) - cross-dashboard alert'
                },
                timestamp=timestamp
            )


def lambda_handler(event, context):
    """Main Lambda handler."""
    logger.info("Starting cross-dashboard router")

    try:
        timestamp = datetime.utcnow().isoformat()

        # Fetch current scores
        logger.info("Fetching current dashboard scores")
        scores = get_current_scores()

        # Apply routing rules
        if scores['inequality'] is not None:
            route_inequality_to_sentiment(scores['inequality'], timestamp)

        if scores['sentiment'] is not None:
            route_sentiment_to_contagion(scores['sentiment'], timestamp)

        if scores['contagion'] is not None:
            route_contagion_to_inequality(scores['contagion'], timestamp)

        if scores['vix'] is not None:
            broadcast_vix_alert(scores['vix'], timestamp)

        # Count signals that triggered
        signals_triggered = sum([
            scores['inequality'] is not None and scores['inequality'] >= INEQUALITY_THRESHOLD,
            scores['sentiment'] is not None and scores['sentiment'] >= SENTIMENT_THRESHOLD,
            scores['contagion'] is not None and scores['contagion'] >= CONTAGION_THRESHOLD,
            scores['vix'] is not None and scores['vix'] > VIX_THRESHOLD
        ])

        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Cross-dashboard routing completed',
                'signals_triggered': signals_triggered,
                'scores': {
                    'inequality': round(scores['inequality'], 2) if scores['inequality'] else None,
                    'sentiment': round(scores['sentiment'], 2) if scores['sentiment'] else None,
                    'contagion': round(scores['contagion'], 2) if scores['contagion'] else None,
                    'vix': round(scores['vix'], 2) if scores['vix'] else None
                }
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
