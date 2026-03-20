import json
import os
import base64
import logging
from decimal import Decimal
from datetime import datetime
import urllib.request
import urllib.error
import time
import hmac
import hashlib

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
GCP_PROJECT_ID = os.environ.get('GCP_PROJECT_ID')
GCP_PUBSUB_TOPIC = os.environ.get('GCP_PUBSUB_TOPIC', 'mvt-analytics')
GCP_SA_KEY_JSON = os.environ.get('GCP_SA_KEY_JSON')

# GCP Pub/Sub API endpoint
PUBSUB_API_URL = f"https://pubsub.googleapis.com/v1/projects/{GCP_PROJECT_ID}/topics/{GCP_PUBSUB_TOPIC}:publish"


class DecimalEncoder(json.JSONEncoder):
    """Custom JSON encoder to handle Decimal objects from DynamoDB."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            # Convert Decimal to float for JSON serialization
            return float(obj)
        return super().default(obj)


def get_gcp_access_token():
    """
    Get GCP access token using service account key JSON.
    Uses OAuth2 JWT flow without external dependencies.
    """
    try:
        from cryptography.hazmat.primitives import serialization, hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.backends import default_backend

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

        with urllib.request.urlopen(req) as response:
            token_response = json.loads(response.read().decode())
            access_token = token_response["access_token"]
            logger.info("Successfully obtained GCP access token")
            return access_token

    except Exception as e:
        logger.error(f"Error obtaining GCP access token: {str(e)}")
        raise


def publish_to_gcp_pubsub(message_data):
    """
    Publish a message to GCP Pub/Sub topic.

    Args:
        message_data: Dictionary to be published as JSON

    Returns:
        True if successful, False otherwise
    """
    try:
        # Get access token
        access_token = get_gcp_access_token()

        # Encode message data as base64
        message_json = json.dumps(message_data, cls=DecimalEncoder)
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

        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode())
            message_ids = result.get("messageIds", [])
            logger.info(f"Successfully published to Pub/Sub: {message_ids}")
            return True

    except urllib.error.HTTPError as e:
        logger.error(f"HTTP error publishing to Pub/Sub: {e.code} {e.reason}")
        error_body = e.read().decode()
        logger.error(f"Error details: {error_body}")
        return False
    except Exception as e:
        logger.error(f"Error publishing to Pub/Sub: {str(e)}")
        return False


def extract_lambda_output(event_detail):
    """
    Extract Lambda output from EventBridge event detail.
    This receives the output from processing Lambda functions
    (inequality-scorer, sentiment-aggregator, contagion-modeler).

    Args:
        event_detail: EventBridge event detail containing Lambda output

    Returns:
        Dictionary with formatted analytics data or None
    """
    try:
        # EventBridge custom events contain the Lambda output in the detail
        output = event_detail.get('output') or event_detail.get('result') or event_detail

        if not output:
            logger.warning("No output found in event detail")
            return None

        # Parse if it's a JSON string
        if isinstance(output, str):
            output = json.loads(output)

        logger.info(f"Extracted Lambda output: {list(output.keys())}")
        return output

    except Exception as e:
        logger.error(f"Error extracting Lambda output: {str(e)}")
        return None


def format_inequality_signal(output_data):
    """
    Format inequality-scorer output as inequality_signal event.

    Args:
        output_data: Output from inequality-scorer Lambda

    Returns:
        Dictionary with formatted event or None
    """
    try:
        message = {
            "event_type": "inequality_signal",
            "date": output_data.get('date') or datetime.utcnow().isoformat(),
            "source": output_data.get('source', 'inequality-scorer'),
            "metric": output_data.get('metric', 'gini_coefficient'),
            "value": float(output_data.get('value') or output_data.get('score', 0)),
            "region": output_data.get('region', 'global'),
            "raw_json": json.dumps(output_data),
            "relay_timestamp": datetime.utcnow().isoformat()
        }
        logger.info(f"Formatted inequality_signal for region: {message['region']}")
        return message
    except Exception as e:
        logger.error(f"Error formatting inequality signal: {str(e)}")
        return None


def format_sentiment_event(output_data):
    """
    Format sentiment-aggregator output as sentiment_event.

    Args:
        output_data: Output from sentiment-aggregator Lambda

    Returns:
        Dictionary with formatted event or None
    """
    try:
        message = {
            "event_type": "sentiment_event",
            "date": output_data.get('date') or datetime.utcnow().isoformat(),
            "event_subtype": output_data.get('event_subtype', 'aggregate_sentiment'),
            "severity": float(output_data.get('severity') or output_data.get('score', 0)),
            "source": output_data.get('source', 'sentiment-aggregator'),
            "countries": output_data.get('countries', []),
            "raw_json": json.dumps(output_data),
            "relay_timestamp": datetime.utcnow().isoformat()
        }
        logger.info(f"Formatted sentiment_event with severity: {message['severity']}")
        return message
    except Exception as e:
        logger.error(f"Error formatting sentiment event: {str(e)}")
        return None


def format_sovereign_indicator(output_data):
    """
    Format contagion-modeler output as sovereign_indicator event.

    Args:
        output_data: Output from contagion-modeler Lambda

    Returns:
        Dictionary with formatted event or None
    """
    try:
        message = {
            "event_type": "sovereign_indicator",
            "date": output_data.get('date') or datetime.utcnow().isoformat(),
            "country": output_data.get('country', 'unknown'),
            "indicator": output_data.get('indicator', 'contagion_risk'),
            "value": float(output_data.get('value') or output_data.get('score', 0)),
            "risk_score": float(output_data.get('risk_score') or output_data.get('score', 0)),
            "relay_timestamp": datetime.utcnow().isoformat()
        }
        logger.info(f"Formatted sovereign_indicator for country: {message['country']}")
        return message
    except Exception as e:
        logger.error(f"Error formatting sovereign indicator: {str(e)}")
        return None


def determine_event_type(output_data):
    """
    Determine event type from Lambda output data.

    Args:
        output_data: Output data from Lambda

    Returns:
        String indicating event type: 'inequality', 'sentiment', 'sovereign', or None
    """
    # Check for explicit event type
    event_type = output_data.get('event_type') or output_data.get('type')

    if event_type:
        event_type_lower = event_type.lower()
        if 'inequality' in event_type_lower or 'gini' in event_type_lower:
            return 'inequality'
        elif 'sentiment' in event_type_lower:
            return 'sentiment'
        elif 'sovereign' in event_type_lower or 'contagion' in event_type_lower:
            return 'sovereign'

    # Check for field presence patterns
    if output_data.get('gini_coefficient') or output_data.get('metric') == 'gini_coefficient':
        return 'inequality'
    elif output_data.get('event_subtype') or 'countries' in output_data:
        return 'sentiment'
    elif output_data.get('contagion_risk') or output_data.get('country'):
        return 'sovereign'

    logger.warning(f"Unable to determine event type from output: {list(output_data.keys())}")
    return None


def process_analytics_event(event_detail):
    """
    Process analytics event from EventBridge and publish to GCP Pub/Sub.

    Args:
        event_detail: EventBridge event detail

    Returns:
        Tuple of (success: bool, message: str)
    """
    try:
        # Extract Lambda output
        output_data = extract_lambda_output(event_detail)
        if not output_data:
            logger.warning("Failed to extract Lambda output")
            return False, "Failed to extract Lambda output"

        # Determine event type
        event_type = determine_event_type(output_data)
        if not event_type:
            logger.warning(f"Unknown event type from output: {list(output_data.keys())}")
            return False, "Unable to determine event type"

        # Format based on event type
        if event_type == 'inequality':
            message = format_inequality_signal(output_data)
        elif event_type == 'sentiment':
            message = format_sentiment_event(output_data)
        elif event_type == 'sovereign':
            message = format_sovereign_indicator(output_data)
        else:
            logger.warning(f"Unhandled event type: {event_type}")
            return False, f"Unhandled event type: {event_type}"

        if not message:
            logger.warning("Failed to format analytics event")
            return False, "Failed to format analytics event"

        # Publish to GCP Pub/Sub
        success = publish_to_gcp_pubsub(message)
        if success:
            logger.info(f"Successfully published {event_type} to analytics topic")
            return True, f"Published {event_type}"
        else:
            logger.warning(f"Failed to publish {event_type} to Pub/Sub")
            return False, "Failed to publish to Pub/Sub"

    except Exception as e:
        logger.error(f"Error processing analytics event: {str(e)}")
        return False, str(e)


def handler(event, context):
    """
    Lambda handler for EventBridge events from processing Lambda functions.
    Routes analytics events to GCP Pub/Sub analytics topic.

    Args:
        event: EventBridge event
        context: Lambda context

    Returns:
        Dictionary with statusCode and body
    """
    logger.info("Analytics relay handler triggered")
    logger.info(f"Received event: {json.dumps(event, default=str)}")

    try:
        # Validate environment variables
        if not GCP_PROJECT_ID or not GCP_SA_KEY_JSON:
            logger.error("Missing required environment variables")
            return {
                "statusCode": 500,
                "body": json.dumps({"error": "Missing GCP configuration"})
            }

        # Get EventBridge detail
        event_detail = event.get('detail', {})
        if not event_detail:
            logger.warning("No detail found in EventBridge event")
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Missing event detail"})
            }

        # Process analytics event
        success, message = process_analytics_event(event_detail)

        if success:
            logger.info(f"Analytics relay completed successfully: {message}")
            return {
                "statusCode": 200,
                "body": json.dumps({
                    "message": "Analytics event relayed successfully",
                    "result": message
                })
            }
        else:
            logger.warning(f"Analytics relay failed: {message}")
            return {
                "statusCode": 400,
                "body": json.dumps({
                    "error": "Analytics relay failed",
                    "message": message
                })
            }

    except Exception as e:
        logger.error(f"Unhandled error in handler: {str(e)}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }
