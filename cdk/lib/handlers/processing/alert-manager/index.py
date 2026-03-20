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
DASHBOARD_STATE_TABLE = os.environ.get('DASHBOARD_STATE_TABLE', 'mvt-dashboard-state')

# Alert thresholds from environment variables
DISTRESS_THRESHOLD = float(os.environ.get('DISTRESS_THRESHOLD', '70'))
TRIGGER_THRESHOLD = float(os.environ.get('TRIGGER_THRESHOLD', '60'))
CONTAGION_THRESHOLD = float(os.environ.get('CONTAGION_THRESHOLD', '80'))
VIX_THRESHOLD = float(os.environ.get('VIX_THRESHOLD', '35'))

# Notification settings
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

# Optional GCP Pub/Sub settings
GCP_PROJECT_ID = os.environ.get('GCP_PROJECT_ID')
GCP_PUBSUB_TOPIC = os.environ.get('GCP_PUBSUB_TOPIC', 'mvt-alerts')
GCP_SA_KEY_JSON = os.environ.get('GCP_SA_KEY_JSON')

PUBSUB_API_URL = f"https://pubsub.googleapis.com/v1/projects/{GCP_PROJECT_ID}/topics/{GCP_PUBSUB_TOPIC}:publish" if GCP_PROJECT_ID else None


class DecimalEncoder(json.JSONEncoder):
    """Custom JSON encoder to handle Decimal objects from DynamoDB."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def get_last_alert_time(alert_key):
    """
    Get the last alert time for a specific threshold.

    Args:
        alert_key: Alert identifier (e.g., 'distress_threshold_breach')

    Returns:
        datetime object or None if no previous alert
    """
    try:
        table = dynamodb.Table(DASHBOARD_STATE_TABLE)

        # Query with the alert state key
        response = table.get_item(
            Key={
                'dashboard': 'mvt-dashboard-state',
                'panel': f'#{alert_key}'
            }
        )

        item = response.get('Item')
        if item and 'last_alert_time' in item:
            return datetime.fromisoformat(item['last_alert_time'])

        return None

    except Exception as e:
        logger.warning(f"Error retrieving last alert time for {alert_key}: {str(e)}")
        return None


def set_last_alert_time(alert_key, alert_time):
    """
    Update the last alert time for a specific threshold.

    Args:
        alert_key: Alert identifier
        alert_time: datetime object
    """
    try:
        table = dynamodb.Table(DASHBOARD_STATE_TABLE)

        table.put_item(
            Item={
                'dashboard': 'mvt-dashboard-state',
                'panel': f'#{alert_key}',
                'last_alert_time': alert_time.isoformat(),
                'timestamp': datetime.utcnow().isoformat()
            }
        )

        logger.info(f"Updated last alert time for {alert_key}")

    except Exception as e:
        logger.error(f"Error setting last alert time for {alert_key}: {str(e)}")


def should_alert(alert_key):
    """
    Check if enough time has elapsed since the last alert (1 hour minimum).

    Args:
        alert_key: Alert identifier

    Returns:
        True if alert should be sent, False otherwise
    """
    last_alert = get_last_alert_time(alert_key)

    if last_alert is None:
        return True

    time_since_alert = datetime.utcnow() - last_alert
    return time_since_alert >= timedelta(hours=1)


def send_telegram_notification(title, message, value):
    """
    Send alert notification via Telegram.

    Args:
        title: Alert title
        message: Alert message
        value: Current metric value

    Returns:
        True if successful, False otherwise
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not configured")
        return False

    try:
        text = f"🚨 *{title}*\n{message}\n\n📊 Value: {value}"

        telegram_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

        payload = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': text,
            'parse_mode': 'Markdown'
        }

        req = urllib.request.Request(
            telegram_url,
            data=json.dumps(payload).encode(),
            headers={'Content-Type': 'application/json'},
            method='POST'
        )

        with urllib.request.urlopen(req, timeout=5) as response:
            result = json.loads(response.read().decode())
            if result.get('ok'):
                logger.info(f"Telegram notification sent for {title}")
                return True
            else:
                logger.error(f"Telegram error: {result.get('description')}")
                return False

    except urllib.error.HTTPError as e:
        logger.error(f"HTTP error sending Telegram notification: {e.code} {e.reason}")
        return False
    except Exception as e:
        logger.error(f"Error sending Telegram notification: {str(e)}")
        return False


def get_gcp_access_token():
    """
    Get GCP access token using service account key JSON.
    Uses OAuth2 JWT flow without external dependencies.
    """
    if not GCP_SA_KEY_JSON:
        return None

    try:
        import time
        import hmac
        import hashlib
        import base64

        # Parse service account key
        sa_key = json.loads(GCP_SA_KEY_JSON)

        # Create JWT header
        header = {
            "alg": "RS256",
            "typ": "JWT",
            "kid": sa_key.get("private_key_id")
        }

        # Create JWT payload
        now = int(time.time())
        payload = {
            "iss": sa_key["client_email"],
            "scope": "https://www.googleapis.com/auth/pubsub",
            "aud": "https://oauth2.googleapis.com/token",
            "exp": now + 3600,
            "iat": now
        }

        # Encode header and payload
        header_b64 = base64.urlsafe_b64encode(
            json.dumps(header).encode()
        ).decode().rstrip("=")
        payload_b64 = base64.urlsafe_b64encode(
            json.dumps(payload).encode()
        ).decode().rstrip("=")

        message = f"{header_b64}.{payload_b64}".encode()

        # Sign with private key (PKCS#8 format)
        from cryptography.hazmat.primitives import serialization, hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.backends import default_backend

        private_key_pem = sa_key["private_key"].encode()
        private_key = serialization.load_pem_private_key(
            private_key_pem,
            password=None,
            backend=default_backend()
        )

        signature = private_key.sign(
            message,
            padding.PKCS1v15(),
            hashes.SHA256()
        )

        signature_b64 = base64.urlsafe_b64encode(signature).decode().rstrip("=")
        jwt_token = f"{message.decode()}.{signature_b64}"

        # Exchange JWT for access token
        token_request_body = {
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": jwt_token
        }

        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=json.dumps(token_request_body).encode(),
            headers={"Content-Type": "application/json"}
        )

        with urllib.request.urlopen(req, timeout=5) as response:
            token_response = json.loads(response.read().decode())
            access_token = token_response["access_token"]
            logger.info("Successfully obtained GCP access token")
            return access_token

    except Exception as e:
        logger.error(f"Error obtaining GCP access token: {str(e)}")
        return None


def publish_to_pubsub(alert_data):
    """
    Publish alert to GCP Pub/Sub topic.

    Args:
        alert_data: Dictionary with alert information

    Returns:
        True if successful, False otherwise
    """
    if not PUBSUB_API_URL or not GCP_SA_KEY_JSON:
        logger.warning("GCP Pub/Sub not configured")
        return False

    try:
        # Get access token
        access_token = get_gcp_access_token()
        if not access_token:
            return False

        # Encode message data as base64
        import base64
        message_json = json.dumps(alert_data, cls=DecimalEncoder)
        message_b64 = base64.b64encode(message_json.encode()).decode()

        # Create Pub/Sub API request body
        request_body = {
            "messages": [
                {
                    "data": message_b64
                }
            ]
        }

        # Make HTTP POST request to Pub/Sub API
        req = urllib.request.Request(
            PUBSUB_API_URL,
            data=json.dumps(request_body).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {access_token}"
            },
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=5) as response:
            result = json.loads(response.read().decode())
            message_ids = result.get("messageIds", [])
            logger.info(f"Successfully published to Pub/Sub: {message_ids}")
            return True

    except urllib.error.HTTPError as e:
        logger.error(f"HTTP error publishing to Pub/Sub: {e.code} {e.reason}")
        return False
    except Exception as e:
        logger.error(f"Error publishing to Pub/Sub: {str(e)}")
        return False


def check_threshold_and_alert(metric_name, value, threshold):
    """
    Check if metric exceeds threshold and send alert if needed.

    Args:
        metric_name: Name of the metric
        value: Current metric value
        threshold: Threshold value

    Returns:
        True if alert was sent, False otherwise
    """
    try:
        if value is None:
            return False

        # Convert to float for comparison
        value_float = float(value)
        threshold_float = float(threshold)

        # Check if threshold is breached
        if value_float <= threshold_float:
            return False

        # Check if we should alert (avoid duplicate alerts within 1 hour)
        alert_key = f"{metric_name}_threshold_breach"
        if not should_alert(alert_key):
            logger.info(f"Alert for {metric_name} suppressed (within 1 hour window)")
            return False

        # Create alert message
        title = f"{metric_name.replace('_', ' ').title()} Alert"
        message = f"Threshold breached: {value_float:.2f} > {threshold_float}"

        # Send Telegram notification
        telegram_sent = send_telegram_notification(title, message, value_float)

        # Publish to Pub/Sub if configured
        pubsub_sent = False
        if GCP_PROJECT_ID and GCP_SA_KEY_JSON:
            alert_data = {
                'timestamp': datetime.utcnow().isoformat(),
                'metric': metric_name,
                'threshold': threshold_float,
                'value': value_float,
                'title': title,
                'message': message
            }
            pubsub_sent = publish_to_pubsub(alert_data)

        # Update last alert time
        set_last_alert_time(alert_key, datetime.utcnow())

        logger.info(
            f"Alert triggered for {metric_name}: {value_float} > {threshold_float} "
            f"(telegram={telegram_sent}, pubsub={pubsub_sent})"
        )

        return telegram_sent or pubsub_sent

    except Exception as e:
        logger.error(f"Error checking threshold for {metric_name}: {str(e)}")
        return False


def extract_metric_value(item, field_names):
    """
    Extract numeric value from item, trying multiple field names.

    Args:
        item: DynamoDB item
        field_names: List of field names to try

    Returns:
        Numeric value or None
    """
    for field in field_names:
        if field in item:
            value = item[field]
            if value is not None:
                try:
                    return float(value)
                except (ValueError, TypeError):
                    pass
    return None


def process_dynamodb_record(record):
    """
    Process a single DynamoDB Stream record.

    Args:
        record: DynamoDB stream record

    Returns:
        True if successfully processed, False otherwise
    """
    try:
        event_name = record.get("eventName")

        # Only process INSERT and MODIFY events
        if event_name not in ["INSERT", "MODIFY"]:
            logger.info(f"Skipping {event_name} event")
            return True

        # Get NewImage from the record
        new_image = record.get("dynamodb", {}).get("NewImage")
        if not new_image:
            logger.warning("No NewImage found in record")
            return False

        # Convert DynamoDB format to Python dict
        item = {}
        for key, value_dict in new_image.items():
            if "S" in value_dict:
                item[key] = value_dict["S"]
            elif "N" in value_dict:
                item[key] = Decimal(value_dict["N"])
            elif "BOOL" in value_dict:
                item[key] = value_dict["BOOL"]
            elif "M" in value_dict:
                item[key] = value_dict["M"]
            elif "L" in value_dict:
                item[key] = value_dict["L"]
            else:
                item[key] = value_dict

        # Get dashboard and panel
        dashboard = item.get("dashboard")
        panel = item.get("panel")

        logger.info(f"Processing alert check for {dashboard}/{panel}")

        # Check against thresholds based on metric type
        alerts_sent = 0

        # Distress index threshold (composite)
        if dashboard == "composite" and panel == "vulnerability_score":
            value = extract_metric_value(item, ["vulnerability_score"])
            if value is not None and check_threshold_and_alert("distress_index", value, DISTRESS_THRESHOLD):
                alerts_sent += 1

        # Trigger probability threshold (sentiment)
        if dashboard == "sentiment_seismic" and panel == "trigger_probability":
            value = extract_metric_value(item, ["probability"])
            if value is not None and check_threshold_and_alert("trigger_probability", value, TRIGGER_THRESHOLD):
                alerts_sent += 1

        # Contagion risk threshold (sovereign)
        if dashboard == "sovereign_dominoes" and panel == "risk_scores":
            # Extract max risk from country_scores
            country_scores = item.get("country_scores", {})
            if country_scores:
                max_score = None
                try:
                    scores = [float(v) for v in country_scores.values() if v is not None]
                    if scores:
                        max_score = max(scores)
                except (ValueError, TypeError):
                    pass

                if max_score is not None and check_threshold_and_alert("contagion_risk", max_score, CONTAGION_THRESHOLD):
                    alerts_sent += 1

        # VIX threshold (sentiment)
        if dashboard == "sentiment_seismic" and panel == "trigger_probability":
            # VIX is included in trigger_probability panel
            value = extract_metric_value(item, ["vix_price"])
            if value is not None and check_threshold_and_alert("vix", value, VIX_THRESHOLD):
                alerts_sent += 1

        if alerts_sent > 0:
            logger.info(f"Sent {alerts_sent} alert(s) for {dashboard}/{panel}")

        return True

    except Exception as e:
        logger.error(f"Error processing record: {str(e)}")
        return False


def handler(event, context):
    """
    Lambda handler for alert threshold checking.

    Args:
        event: DynamoDB Stream event
        context: Lambda context

    Returns:
        Dictionary with statusCode and body
    """
    logger.info("Alert manager handler triggered")
    logger.info(f"Received event: {json.dumps(event, default=str)}")

    try:
        # Process DynamoDB Stream records
        records = event.get("Records", [])
        logger.info(f"Processing {len(records)} records")

        successful_records = 0
        failed_records = 0

        for record in records:
            if process_dynamodb_record(record):
                successful_records += 1
            else:
                failed_records += 1

        logger.info(
            f"Alert checking completed: {successful_records} successful, {failed_records} failed"
        )

        # Return response
        return {
            "statusCode": 200 if failed_records == 0 else 206,
            "body": json.dumps(
                {
                    "message": "Alert checking completed",
                    "total_records": len(records),
                    "successful": successful_records,
                    "failed": failed_records,
                },
                cls=DecimalEncoder,
            ),
        }

    except Exception as e:
        logger.error(f"Unhandled error in handler: {str(e)}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
