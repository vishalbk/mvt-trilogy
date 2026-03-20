import json
import os
import logging
from datetime import datetime, timedelta
from decimal import Decimal
import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')
HISTORY_TABLE = os.environ.get('HISTORY_TABLE', 'mvt-history')


class DecimalEncoder(json.JSONEncoder):
    """Custom JSON encoder to handle Decimal objects from DynamoDB."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def extract_metric_from_record(record):
    """
    Extract metric data from DynamoDB Stream record (NewImage).

    Args:
        record: DynamoDB stream record

    Returns:
        Tuple of (dashboard, metric_name, value, timestamp) or None
    """
    try:
        # Get NewImage from the record
        new_image = record.get("dynamodb", {}).get("NewImage")

        if not new_image:
            logger.warning("No NewImage found in record")
            return None

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

        # Extract dashboard and metric identifiers
        dashboard = item.get("dashboard")
        panel = item.get("panel")

        # For composite metrics, use panel as metric name
        # For signal metrics, use a combination or raw metric identifier
        metric_name = panel or item.get("signalId_timestamp", "unknown")

        # Extract numeric value from various possible locations
        value = None
        if "value" in item:
            value = item["value"]
        elif "score" in item:
            value = item["score"]
        elif "probability" in item:
            value = item["probability"]
        elif "vulnerability_score" in item:
            value = item["vulnerability_score"]
        else:
            # Try to find first numeric field
            for k, v in item.items():
                if isinstance(v, (int, float, Decimal)):
                    value = v
                    break

        if value is None or dashboard is None or metric_name is None:
            logger.warning(
                f"Skipping record - missing critical fields: "
                f"dashboard={dashboard}, metric={metric_name}, value={value}"
            )
            return None

        # Get timestamp
        timestamp = item.get("timestamp", datetime.utcnow().isoformat())

        return (dashboard, metric_name, value, timestamp)

    except Exception as e:
        logger.error(f"Error extracting metric from record: {str(e)}")
        return None


def write_history(dashboard, metric_name, value, timestamp):
    """
    Write a metric value to history table.

    Args:
        dashboard: Dashboard identifier
        metric_name: Metric/panel name
        value: Numeric value
        timestamp: ISO format timestamp
    """
    try:
        table = dynamodb.Table(HISTORY_TABLE)

        # Partition key: {dashboard}#{metric}
        pk = f"{dashboard}#{metric_name}"

        # Sort key: ISO timestamp
        sk = timestamp

        # Calculate TTL: 30 days from now
        ttl_seconds = int((datetime.utcnow() + timedelta(days=30)).timestamp())

        # Store with Decimal for numeric precision
        if isinstance(value, Decimal):
            decimal_value = value
        else:
            decimal_value = Decimal(str(value))

        item = {
            "dashboard_metric": pk,
            "timestamp": sk,
            "value": decimal_value,
            "expiry_epoch": ttl_seconds,
        }

        table.put_item(Item=item)
        logger.info(
            f"Wrote history: {pk} @ {sk} = {decimal_value}"
        )
        return True

    except Exception as e:
        logger.error(f"Error writing to history table: {str(e)}")
        return False


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

        # Extract metric data
        result = extract_metric_from_record(record)
        if not result:
            return False

        dashboard, metric_name, value, timestamp = result

        # Write to history
        success = write_history(dashboard, metric_name, value, timestamp)
        return success

    except Exception as e:
        logger.error(f"Error processing record: {str(e)}")
        return False


def handler(event, context):
    """
    Lambda handler for DynamoDB Stream to history table.

    Args:
        event: DynamoDB Stream event
        context: Lambda context

    Returns:
        Dictionary with statusCode and body
    """
    logger.info("History writer handler triggered")
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
            f"History writing completed: {successful_records} successful, {failed_records} failed"
        )

        # Return response
        return {
            "statusCode": 200 if failed_records == 0 else 206,
            "body": json.dumps(
                {
                    "message": "History writing completed",
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
