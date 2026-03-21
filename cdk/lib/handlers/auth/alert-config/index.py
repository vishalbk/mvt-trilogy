"""
MVT Personalized Alert Configuration (S7-03)
Per-user alert thresholds integrated with the alert-manager Lambda.
Reads user preferences to determine which alerts to fire for which users.

Trigger: EventBridge event from alert-manager or direct invocation
Output: Personalized alert routing to Telegram/email per user settings
"""

import json
import os
import logging
from datetime import datetime, timedelta
from decimal import Decimal
import boto3
from boto3.dynamodb.conditions import Key, Attr

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')

PREFERENCES_TABLE = os.environ.get('PREFERENCES_TABLE', 'mvt-user-preferences')
DASHBOARD_STATE_TABLE = os.environ.get('DASHBOARD_STATE_TABLE', 'mvt-dashboard-state')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')

# Default global thresholds (used when user has no custom settings)
DEFAULT_THRESHOLDS = {
    'distress_index': 70,
    'trigger_probability': 60,
    'contagion_risk': 65,
    'vix_level': 30,
}

# KPI to threshold key mapping
KPI_THRESHOLD_MAP = {
    'distress_index': 'distress_index_threshold',
    'trigger_probability': 'trigger_probability_threshold',
    'contagion_risk': 'contagion_risk_threshold',
    'vix_level': 'vix_level_threshold',
}

# Alert cooldown per user per KPI (minutes)
ALERT_COOLDOWN_MINUTES = 60

# In-memory cooldown cache (reset per invocation)
_cooldown_cache = {}


def get_all_user_preferences(table):
    """Scan preferences table for all users with alerts enabled."""
    try:
        response = table.scan(
            FilterExpression=Attr('preferences.alerts.enabled').eq(True),
            Limit=1000
        )
        return response.get('Items', [])
    except Exception as e:
        logger.warning(f"Failed to scan preferences: {e}")
        return []


def get_current_kpi_values(state_table):
    """Fetch latest KPI values from dashboard-state."""
    kpi_values = {}
    kpi_components = {
        'distress_index': ('overview', 'distress_index'),
        'trigger_probability': ('overview', 'trigger_probability'),
        'contagion_risk': ('overview', 'contagion_risk'),
        'vix_level': ('overview', 'vix_level'),
    }

    for kpi, (dashboard, component) in kpi_components.items():
        try:
            response = state_table.get_item(Key={
                'dashboard': dashboard,
                'component': component,
            })
            item = response.get('Item')
            if item:
                payload = item.get('payload', {})
                if isinstance(payload, str):
                    payload = json.loads(payload)
                val = payload.get('value') or payload.get('current_value')
                if val is not None:
                    kpi_values[kpi] = float(val)
        except Exception as e:
            logger.warning(f"Failed to get {kpi} value: {e}")

    return kpi_values


def check_quiet_hours(quiet_hours_config):
    """Check if current time is within user's quiet hours."""
    if not quiet_hours_config or not quiet_hours_config.get('enabled'):
        return False

    try:
        now = datetime.utcnow()
        start_parts = quiet_hours_config.get('start', '22:00').split(':')
        end_parts = quiet_hours_config.get('end', '07:00').split(':')

        start_hour, start_min = int(start_parts[0]), int(start_parts[1])
        end_hour, end_min = int(end_parts[0]), int(end_parts[1])

        current_minutes = now.hour * 60 + now.minute
        start_minutes = start_hour * 60 + start_min
        end_minutes = end_hour * 60 + end_min

        if start_minutes <= end_minutes:
            return start_minutes <= current_minutes <= end_minutes
        else:
            # Crosses midnight
            return current_minutes >= start_minutes or current_minutes <= end_minutes

    except (ValueError, TypeError):
        return False


def check_cooldown(user_id, kpi):
    """Check if alert for this user+KPI is on cooldown."""
    key = f"{user_id}#{kpi}"
    last_alert = _cooldown_cache.get(key)
    if last_alert:
        elapsed = (datetime.utcnow() - last_alert).total_seconds() / 60
        if elapsed < ALERT_COOLDOWN_MINUTES:
            return True
    return False


def set_cooldown(user_id, kpi):
    """Set cooldown for user+KPI."""
    key = f"{user_id}#{kpi}"
    _cooldown_cache[key] = datetime.utcnow()


def send_telegram_alert(chat_id, message):
    """Send alert via Telegram."""
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        return False

    try:
        import urllib.request
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = json.dumps({
            'chat_id': chat_id,
            'text': message,
            'parse_mode': 'HTML',
        }).encode()

        req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        logger.warning(f"Telegram alert failed for chat {chat_id}: {e}")
        return False


def evaluate_alerts_for_user(user_prefs, kpi_values):
    """Evaluate which alerts should fire for a specific user."""
    user_id = user_prefs.get('user_id', '')
    prefs = user_prefs.get('preferences', {})
    alert_config = prefs.get('alerts', {})

    if not alert_config.get('enabled', True):
        return []

    # Check quiet hours
    if check_quiet_hours(alert_config.get('quiet_hours', {})):
        logger.info(f"User {user_id}: in quiet hours, skipping alerts")
        return []

    triggered = []

    for kpi, current_value in kpi_values.items():
        threshold_key = KPI_THRESHOLD_MAP.get(kpi)
        if not threshold_key:
            continue

        threshold = alert_config.get(threshold_key, DEFAULT_THRESHOLDS.get(kpi, 100))

        # Check if value exceeds user's threshold
        if current_value >= float(threshold):
            if not check_cooldown(user_id, kpi):
                triggered.append({
                    'kpi': kpi,
                    'value': current_value,
                    'threshold': float(threshold),
                    'severity': 'critical' if current_value >= float(threshold) * 1.2 else 'warning',
                })
                set_cooldown(user_id, kpi)

    return triggered


def format_alert_message(alerts, user_name='User'):
    """Format alert message for delivery."""
    lines = [f"<b>MVT Alert for {user_name}</b>\n"]

    for alert in alerts:
        emoji = '🔴' if alert['severity'] == 'critical' else '🟡'
        kpi_label = alert['kpi'].replace('_', ' ').title()
        lines.append(
            f"{emoji} <b>{kpi_label}</b>: {alert['value']:.1f} "
            f"(threshold: {alert['threshold']:.0f})"
        )

    lines.append(f"\n<i>{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</i>")
    return '\n'.join(lines)


def handler(event, context):
    """Main handler — evaluate personalized alerts for all users."""
    logger.info("Starting personalized alert evaluation")

    prefs_table = dynamodb.Table(PREFERENCES_TABLE)
    state_table = dynamodb.Table(DASHBOARD_STATE_TABLE)

    # Get current KPI values
    kpi_values = get_current_kpi_values(state_table)
    if not kpi_values:
        logger.info("No KPI values available, skipping alert evaluation")
        return {'statusCode': 200, 'body': json.dumps({'alerts_sent': 0, 'reason': 'no_data'})}

    # Get all users with alerts enabled
    user_prefs_list = get_all_user_preferences(prefs_table)
    logger.info(f"Evaluating alerts for {len(user_prefs_list)} users")

    total_alerts = 0
    alerts_by_channel = {'dashboard': 0, 'telegram': 0, 'email': 0}

    for user_prefs in user_prefs_list:
        user_id = user_prefs.get('user_id', '')
        prefs = user_prefs.get('preferences', {})

        triggered = evaluate_alerts_for_user(user_prefs, kpi_values)
        if not triggered:
            continue

        channels = prefs.get('alerts', {}).get('channels', ['dashboard'])
        user_name = user_id.split('#')[0] if '#' in user_id else user_id[:20]
        message = format_alert_message(triggered, user_name)

        for channel in channels:
            if channel == 'telegram':
                chat_id = prefs.get('notifications', {}).get('telegram_chat_id', '')
                if chat_id:
                    send_telegram_alert(chat_id, message)
                    alerts_by_channel['telegram'] += 1

            elif channel == 'dashboard':
                # Write alert to dashboard-state for frontend display
                try:
                    alert_payload = json.loads(
                        json.dumps({
                            'user_id': user_id,
                            'alerts': triggered,
                            'timestamp': datetime.utcnow().isoformat() + 'Z',
                        }),
                        parse_float=lambda x: Decimal(str(x))
                    )
                    state_table.put_item(Item={
                        'dashboard': 'user_alerts',
                        'component': f'alert_{user_id}',
                        'payload': alert_payload,
                        'updated_at': datetime.utcnow().isoformat() + 'Z',
                        'ttl': int((datetime.utcnow() + timedelta(days=7)).timestamp()),
                    })
                    alerts_by_channel['dashboard'] += 1
                except Exception as e:
                    logger.error(f"Failed to write dashboard alert for {user_id}: {e}")

        total_alerts += len(triggered)

    summary = {
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'users_evaluated': len(user_prefs_list),
        'total_alerts': total_alerts,
        'by_channel': alerts_by_channel,
        'kpi_values': kpi_values,
    }

    logger.info(f"Alert evaluation complete: {json.dumps(summary, default=str)}")

    return {
        'statusCode': 200,
        'body': json.dumps(summary, default=str),
    }
