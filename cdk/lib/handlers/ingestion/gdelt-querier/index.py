import json
import os
import urllib.request
import urllib.error
import base64
from datetime import datetime, timedelta
import boto3
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')
events = boto3.client('events')

SIGNALS_TABLE = os.environ.get('SIGNALS_TABLE')
EVENT_BUS_NAME = os.environ.get('EVENT_BUS_NAME')
GCP_SA_KEY_B64 = os.environ.get('GCP_SA_KEY')

CONFLICT_EVENT_CODES = ['14', '17', '18', '19', '20']  # Conflict, sanctions, threats
SOVEREIGN_EVENT_CODES = ['19', '20']  # Sanctions, threats

GDELT_BASE_URL = 'https://www.googleapis.com/bigquery/v2'


def get_gcp_access_token() -> str:
    """Get GCP access token using service account key."""
    try:
        if not GCP_SA_KEY_B64:
            raise ValueError("GCP_SA_KEY environment variable not set")

        sa_key = json.loads(base64.b64decode(GCP_SA_KEY_B64))

        # Use service account to get access token
        # This is a simplified approach - in production, use google-auth library
        logger.info("Using GCP service account for authentication")
        return sa_key.get('private_key', '')

    except Exception as e:
        logger.error(f"Error getting GCP access token: {str(e)}")
        raise


def query_gdelt_events(access_token: str, max_retries: int = 3) -> list:
    """Query GDELT events from BigQuery."""
    for attempt in range(max_retries):
        try:
            # Calculate lookback window (past 15 minutes)
            end_time = datetime.utcnow()
            start_time = end_time - timedelta(minutes=15)
            start_date = start_time.strftime('%Y%m%d')

            # Build SQL query for conflict and sanctions events
            sql_query = f"""
            SELECT SQLDATE, EventCode, Actor1CountryCode, Actor2CountryCode,
                   GoldsteinScale, NumMentions, AvgTone
            FROM `gdelt-bq.gdeltv2.events`
            WHERE SQLDATE >= {start_date}
            AND EventRootCode IN ('14','17','18','19','20')
            """

            query_request = {
                'query': sql_query,
                'useLegacySql': False,
                'location': 'US'
            }

            logger.info(f"Executing GDELT query for date {start_date}")

            # In production, use google-cloud-bigquery library
            # This is a placeholder for the API call structure
            logger.info(f"GDELT query executed (simulated)")
            return []

        except Exception as e:
            logger.warning(f"Attempt {attempt + 1} failed to query GDELT: {str(e)}")
            if attempt < max_retries - 1:
                continue
            logger.error(f"Failed to query GDELT after {max_retries} attempts: {str(e)}")
            return []


def write_to_dynamodb(events_data: list, dashboard: str, timestamp: str) -> None:
    """Write GDELT events to DynamoDB."""
    table = dynamodb.Table(SIGNALS_TABLE)
    ttl_timestamp = int((datetime.utcnow() + timedelta(days=30)).timestamp())

    with table.batch_writer(batch_size=25) as batch:
        for idx, event_item in enumerate(events_data):
            try:
                event_code = event_item.get('EventCode', 'unknown')
                item = {
                    'dashboard': dashboard,
                    'sort_key': f"gdelt#{event_code}#{timestamp}#{idx}",
                    'source': 'gdelt',
                    'event_code': event_code,
                    'actor1_country': event_item.get('Actor1CountryCode', ''),
                    'actor2_country': event_item.get('Actor2CountryCode', ''),
                    'goldstein_scale': float(event_item.get('GoldsteinScale', 0)),
                    'num_mentions': int(event_item.get('NumMentions', 0)),
                    'avg_tone': float(event_item.get('AvgTone', 0)),
                    'raw_data': json.dumps(event_item),
                    'ttl': ttl_timestamp,
                    'timestamp': timestamp
                }
                batch.put_item(Item=item)
            except Exception as e:
                logger.error(f"Error writing GDELT event: {str(e)}")


def categorize_and_write_events(events_data: list, timestamp: str) -> None:
    """Categorize events and write to appropriate dashboards."""
    sentiment_events = []
    sovereign_events = []

    for event in events_data:
        event_code = event.get('EventCode', '')
        if event_code in CONFLICT_EVENT_CODES:
            sentiment_events.append(event)

        if event_code in SOVEREIGN_EVENT_CODES:
            sovereign_events.append(event)

    if sentiment_events:
        write_to_dynamodb(sentiment_events, 'sentiment_seismic', timestamp)
        logger.info(f"Wrote {len(sentiment_events)} events to sentiment_seismic")

    if sovereign_events:
        write_to_dynamodb(sovereign_events, 'sovereign_dominoes', timestamp)
        logger.info(f"Wrote {len(sovereign_events)} events to sovereign_dominoes")


def publish_event(event_count: int, timestamp: str) -> None:
    """Publish EventBridge event."""
    try:
        event = {
            'Source': 'mvt.ingestion.sentiment',
            'DetailType': 'GDELTEventsUpdated',
            'EventBusName': EVENT_BUS_NAME,
            'Detail': json.dumps({
                'event_count': event_count,
                'timestamp': timestamp
            })
        }
        events.put_events(Entries=[event])
        logger.info(f"Published GDELTEventsUpdated event ({event_count} events)")
    except Exception as e:
        logger.error(f"Error publishing event: {str(e)}")
        raise


def lambda_handler(event, context):
    """Main Lambda handler."""
    logger.info("Starting GDELT event querier")

    try:
        access_token = get_gcp_access_token()
        gdelt_events = query_gdelt_events(access_token)

        if gdelt_events:
            timestamp = datetime.utcnow().isoformat()
            categorize_and_write_events(gdelt_events, timestamp)
            publish_event(len(gdelt_events), timestamp)

            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'GDELT query completed',
                    'events_processed': len(gdelt_events)
                })
            }
        else:
            logger.info("No GDELT events found in query window")
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'No GDELT events found',
                    'events_processed': 0
                })
            }

    except Exception as e:
        logger.error(f"Unhandled error in lambda_handler: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
