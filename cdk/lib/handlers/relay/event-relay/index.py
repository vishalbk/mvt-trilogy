import json
import os
import base64
import logging
from decimal import Decimal
from datetime import datetime
import urllib.request
import urllib.error

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
GCP_PROJECT_ID = os.environ.get('GCP_PROJECT_ID')
GCP_PUBSUB_TOPIC = os.environ.get('GCP_PUBSUB_TOPIC', 'mvt-signals')
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
        import json
        import base64
        import time
        import hmac
        import hashlib

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


def extract_signal_data(dynamodb_record):
    """
    Extract signal data from DynamoDB Stream record (NewImage).

    Args:
        dynamodb_record: DynamoDB stream record

    Returns:
        Dictionary with formatted signal data or None
    """
    try:
        # Get NewImage from the record
        new_image = dynamodb_record.get("dynamodb", {}).get("NewImage")

        if not new_image:
            logger.warning("No NewImage found in record")
            return None

        # Convert DynamoDB format to Python dict
        # DynamoDB format: {"S": "value"}, {"N": "123"}, etc.
        signal_data = {}

        for key, value_dict in new_image.items():
            if "S" in value_dict:  # String
                signal_data[key] = value_dict["S"]
            elif "N" in value_dict:  # Number
                signal_data[key] = Decimal(value_dict["N"])
            elif "BOOL" in value_dict:  # Boolean
                signal_data[key] = value_dict["BOOL"]
            elif "M" in value_dict:  # Map
                signal_data[key] = value_dict["M"]
            elif "L" in value_dict:  # List
                signal_data[key] = value_dict["L"]
            else:
                signal_data[key] = value_dict

        logger.info(f"Extracted signal data: {list(signal_data.keys())}")
        return signal_data

    except Exception as e:
        logger.error(f"Error extracting signal data: {str(e)}")
        return None


def format_event_message(signal_data):
    """
    Format signal data as a cross-cloud event message for Pub/Sub.

    Args:
        signal_data: Dictionary with signal information

    Returns:
        Dictionary with formatted event message
    """
    try:
        # Extract relevant fields
        dashboard_id = signal_data.get("dashboard", "unknown")
        panel_id = signal_data.get("panel", "unknown")
        event_type = signal_data.get("eventType") or signal_data.get("event_type", "signal_update")
        severity = signal_data.get("severity", "info")
        value = signal_data.get("value") or signal_data.get("score")
        timestamp = signal_data.get("timestamp") or datetime.utcnow().isoformat()

        # Generate signal ID if not present
        signal_id = signal_data.get("signal_id") or signal_data.get("signalId") or f"{dashboard_id}#{panel_id}"

        # Format the message
        message = {
            "source": "aws.mvt.dynamodb",
            "cloud_region": os.environ.get("AWS_REGION", "us-east-1"),
            "dashboard_id": dashboard_id,
            "panel_id": panel_id,
            "signal_id": signal_id,
            "event_type": event_type,
            "severity": severity,
            "value": value,
            "timestamp": timestamp,
            "relay_timestamp": datetime.utcnow().isoformat(),
            "raw_data": signal_data  # Include all original data for context
        }

        logger.info(f"Formatted event message for dashboard: {dashboard_id}")
        return message

    except Exception as e:
        logger.error(f"Error formatting event message: {str(e)}")
        return None


def process_dynamodb_record(record):
    """
    Process a single DynamoDB Stream record.

    Args:
        record: DynamoDB stream record

    Returns:
        True if successfully published, False otherwise
    """
    try:
        event_name = record.get("eventName")

        # Only process INSERT and MODIFY events
        if event_name not in ["INSERT", "MODIFY"]:
            logger.info(f"Skipping {event_name} event")
            return True

        # Extract signal data
        signal_data = extract_signal_data(record)
        if not signal_data:
            logger.warning("Failed to extract signal data, skipping record")
            return False

        # Format as cross-cloud event message
        event_message = format_event_message(signal_data)
        if not event_message:
            logger.warning("Failed to format event message, skipping record")
            return False

        # Publish to GCP Pub/Sub
        success = publish_to_gcp_pubsub(event_message)
        return success

    except Exception as e:
        logger.error(f"Error processing record: {str(e)}")
        return False


def handler(event, context):
    """
    Lambda handler for DynamoDB Stream to GCP Pub/Sub relay.

    Args:
        event: DynamoDB Stream event
        context: Lambda context

    Returns:
        Dictionary with statusCode and body
    """
    logger.info("Event relay handler triggered")
    logger.info(f"Received event: {json.dumps(event, default=str)}")

    try:
        # Validate environment variables
        if not GCP_PROJECT_ID or not GCP_SA_KEY_JSON:
            logger.error("Missing required environment variables")
            return {
                "statusCode": 500,
                "body": json.dumps({"error": "Missing GCP configuration"})
            }

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

        logger.info(f"Relay completed: {successful_records} successful, {failed_records} failed")

        # Return response
        return {
            "statusCode": 200 if failed_records == 0 else 206,  # 206 = Partial Success
            "body": json.dumps({
                "message": "Event relay processing completed",
                "total_records": len(records),
                "successful": successful_records,
                "failed": failed_records
            })
        }

    except Exception as e:
        logger.error(f"Unhandled error in handler: {str(e)}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }
