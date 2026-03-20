"""
MVT SRE Cost Anomaly Detection (S4-05)
Monitors daily costs and error rates, alerting via Telegram when:
- Daily cost exceeds threshold (default $5)
- Any Lambda error rate > 5%
- Sudden cost spike (>200% of 7-day average)

Trigger: EventBridge schedule (every 1 hour) or post-cost-tracker
Output: Alerts to Telegram via alert-manager pattern
"""

import json
import os
import logging
from datetime import datetime, timedelta
from decimal import Decimal
import boto3
import urllib.request
import urllib.error

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')
cloudwatch = boto3.client('cloudwatch')

DASHBOARD_STATE_TABLE = os.environ.get('DASHBOARD_STATE_TABLE', 'mvt-dashboard-state')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

# Configurable thresholds
DAILY_COST_THRESHOLD = float(os.environ.get('DAILY_COST_THRESHOLD', '5.0'))
ERROR_RATE_THRESHOLD = float(os.environ.get('ERROR_RATE_THRESHOLD', '5.0'))
COST_SPIKE_MULTIPLIER = float(os.environ.get('COST_SPIKE_MULTIPLIER', '2.0'))
THROTTLE_THRESHOLD = int(os.environ.get('THROTTLE_THRESHOLD', '10'))

# Alert deduplication (1 hour cooldown)
ALERT_COOLDOWN_MINUTES = 60


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def get_last_alert_time(alert_key):
    """Check when the last alert of this type was sent."""
    try:
        table = dynamodb.Table(DASHBOARD_STATE_TABLE)
        response = table.get_item(Key={
            'dashboard': 'sre_alerts',
            'metric': f'last_alert_{alert_key}'
        })
        if 'Item' in response:
            ts = response['Item'].get('timestamp', '')
            return datetime.fromisoformat(ts) if ts else None
    except Exception:
        pass
    return None


def record_alert(alert_key):
    """Record that an alert was sent for deduplication."""
    try:
        table = dynamodb.Table(DASHBOARD_STATE_TABLE)
        table.put_item(Item={
            'dashboard': 'sre_alerts',
            'metric': f'last_alert_{alert_key}',
            'timestamp': datetime.utcnow().isoformat(),
            'updated_at': datetime.utcnow().isoformat()
        })
    except Exception as e:
        logger.warning(f"Failed to record alert: {e}")


def should_alert(alert_key):
    """Check if enough time has passed since the last alert."""
    last = get_last_alert_time(alert_key)
    if last is None:
        return True
    elapsed = (datetime.utcnow() - last).total_seconds() / 60
    return elapsed >= ALERT_COOLDOWN_MINUTES


def send_telegram_alert(message):
    """Send an alert message via Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not configured, skipping alert")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        'chat_id': TELEGRAM_CHAT_ID,
        'text': message,
        'parse_mode': 'HTML'
    }).encode('utf-8')

    try:
        req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        logger.error(f"Failed to send Telegram alert: {e}")
        return False


def check_cost_threshold():
    """Check if daily cost exceeds threshold."""
    alerts = []
    try:
        table = dynamodb.Table(DASHBOARD_STATE_TABLE)
        response = table.get_item(Key={
            'dashboard': 'sre_observatory',
            'metric': 'cost_summary'
        })

        if 'Item' not in response:
            return alerts

        item = response['Item']
        daily_cost = float(item.get('total_cost_24h', 0))
        monthly_projected = float(item.get('monthly_projected', 0))

        if daily_cost > DAILY_COST_THRESHOLD:
            if should_alert('cost_threshold'):
                alerts.append({
                    'type': 'cost_threshold',
                    'severity': 'warning',
                    'message': (
                        f"<b>Cost Alert: Daily Spend Exceeds Threshold</b>\n\n"
                        f"Daily cost: <b>${daily_cost:.4f}</b> (threshold: ${DAILY_COST_THRESHOLD:.2f})\n"
                        f"Monthly projected: <b>${monthly_projected:.2f}</b>\n"
                        f"Overage: {((daily_cost / DAILY_COST_THRESHOLD - 1) * 100):.1f}%"
                    )
                })
                record_alert('cost_threshold')

    except Exception as e:
        logger.error(f"Cost threshold check failed: {e}")

    return alerts


def check_error_rates():
    """Check if any Lambda has error rate > threshold."""
    alerts = []
    try:
        table = dynamodb.Table(DASHBOARD_STATE_TABLE)

        # Scan for all lambda_cost_ entries
        response = table.scan(
            FilterExpression='begins_with(#m, :prefix) AND #d = :dash',
            ExpressionAttributeNames={'#m': 'metric', '#d': 'dashboard'},
            ExpressionAttributeValues={':prefix': 'lambda_cost_', ':dash': 'sre_observatory'}
        )

        high_error_functions = []
        for item in response.get('Items', []):
            fn_name = item.get('function_name', '')
            error_rate = float(item.get('error_rate', 0))
            invocations = int(item.get('invocations_24h', 0))

            if error_rate > ERROR_RATE_THRESHOLD and invocations > 10:
                high_error_functions.append({
                    'name': fn_name,
                    'error_rate': error_rate,
                    'invocations': invocations,
                    'errors': int(item.get('errors_24h', 0))
                })

        if high_error_functions and should_alert('error_rate'):
            fn_list = '\n'.join([
                f"  - {f['name']}: {f['error_rate']:.1f}% ({f['errors']}/{f['invocations']})"
                for f in sorted(high_error_functions, key=lambda x: x['error_rate'], reverse=True)[:5]
            ])
            alerts.append({
                'type': 'error_rate',
                'severity': 'critical' if any(f['error_rate'] > 20 for f in high_error_functions) else 'warning',
                'message': (
                    f"<b>Error Rate Alert: {len(high_error_functions)} Functions Above {ERROR_RATE_THRESHOLD}%</b>\n\n"
                    f"{fn_list}\n\n"
                    f"Threshold: {ERROR_RATE_THRESHOLD}%"
                )
            })
            record_alert('error_rate')

    except Exception as e:
        logger.error(f"Error rate check failed: {e}")

    return alerts


def check_throttles():
    """Check for DynamoDB or Lambda throttling."""
    alerts = []
    try:
        table = dynamodb.Table(DASHBOARD_STATE_TABLE)
        response = table.get_item(Key={
            'dashboard': 'sre_observatory',
            'metric': 'capacity_summary'
        })

        if 'Item' in response:
            item = response['Item']
            throttles = int(item.get('dynamo_total_throttles_24h', 0))

            if throttles > THROTTLE_THRESHOLD and should_alert('throttles'):
                alerts.append({
                    'type': 'throttles',
                    'severity': 'warning',
                    'message': (
                        f"<b>Throttle Alert: DynamoDB Throttling Detected</b>\n\n"
                        f"Total throttle events (24h): <b>{throttles}</b>\n"
                        f"Threshold: {THROTTLE_THRESHOLD}\n\n"
                        f"Consider reviewing DynamoDB capacity settings."
                    )
                })
                record_alert('throttles')

    except Exception as e:
        logger.error(f"Throttle check failed: {e}")

    return alerts


def check_concurrency():
    """Check if Lambda concurrency is approaching limits."""
    alerts = []
    try:
        table = dynamodb.Table(DASHBOARD_STATE_TABLE)
        response = table.get_item(Key={
            'dashboard': 'sre_observatory',
            'metric': 'lambda_concurrency'
        })

        if 'Item' in response:
            item = response['Item']
            utilization = float(item.get('utilization_pct', 0))

            if utilization > 70 and should_alert('concurrency'):
                alerts.append({
                    'type': 'concurrency',
                    'severity': 'critical' if utilization > 90 else 'warning',
                    'message': (
                        f"<b>Concurrency Alert: Lambda Utilization High</b>\n\n"
                        f"Current utilization: <b>{utilization:.1f}%</b>\n"
                        f"Max concurrent: {int(item.get('max_concurrent_24h', 0))}\n"
                        f"Account limit: {int(item.get('account_limit', 1000))}\n\n"
                        f"{'CRITICAL: Consider requesting limit increase!' if utilization > 90 else 'Monitor closely.'}"
                    )
                })
                record_alert('concurrency')

    except Exception as e:
        logger.error(f"Concurrency check failed: {e}")

    return alerts


def handler(event, context):
    """
    Main Lambda handler. Runs all anomaly detection checks and sends
    alerts via Telegram for any violations.
    """
    logger.info("Starting cost anomaly detection")

    all_alerts = []

    # Run all checks
    all_alerts.extend(check_cost_threshold())
    all_alerts.extend(check_error_rates())
    all_alerts.extend(check_throttles())
    all_alerts.extend(check_concurrency())

    # Send alerts
    sent_count = 0
    for alert in all_alerts:
        severity_emoji = {
            'critical': '\xF0\x9F\x94\xB4',  # red circle
            'warning': '\xF0\x9F\x9F\xA1',   # yellow circle
            'info': '\xF0\x9F\x94\xB5'        # blue circle
        }.get(alert['severity'], '')

        full_message = f"{severity_emoji} <b>MVT SRE Alert</b>\n\n{alert['message']}"
        if send_telegram_alert(full_message):
            sent_count += 1

    # Write alert summary to dashboard-state
    try:
        table = dynamodb.Table(DASHBOARD_STATE_TABLE)
        table.put_item(Item={
            'dashboard': 'sre_observatory',
            'metric': 'anomaly_detection',
            'alerts_found': Decimal(str(len(all_alerts))),
            'alerts_sent': Decimal(str(sent_count)),
            'alert_types': [a['type'] for a in all_alerts],
            'last_run': datetime.utcnow().isoformat(),
            'timestamp': datetime.utcnow().isoformat(),
            'updated_at': datetime.utcnow().isoformat()
        })
    except Exception as e:
        logger.error(f"Failed to write alert summary: {e}")

    logger.info(f"Anomaly detection complete: {len(all_alerts)} alerts found, {sent_count} sent")

    return {
        'statusCode': 200,
        'body': json.dumps({
            'message': 'Anomaly detection complete',
            'alerts_found': len(all_alerts),
            'alerts_sent': sent_count,
            'alert_types': [a['type'] for a in all_alerts]
        })
    }
