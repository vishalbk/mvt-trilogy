import json
import base64
import functions_framework
from google.cloud import bigquery
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bq_client = bigquery.Client()


@functions_framework.cloud_event
def write_analytics(cloud_event):
    """
    Routes Pub/Sub messages to appropriate BigQuery table based on event type.
    Performs schema validation before streaming insert.
    """
    try:
        # Decode Pub/Sub message
        pubsub_message = base64.b64decode(cloud_event.data["message"]["data"]).decode()
        event_data = json.loads(pubsub_message)

        event_type = event_data.get('event_type')
        logger.info(f"Writing analytics event: {event_type}")

        # Route to appropriate table based on event_type
        if event_type == 'inequality_signal':
            table_id = 'mvt_analytics.inequality_signals'
            row = {
                'date': event_data.get('date'),
                'source': event_data.get('source'),
                'metric': event_data.get('metric'),
                'value': float(event_data.get('value', 0)),
                'region': event_data.get('region'),
                'raw_json': json.dumps(event_data.get('raw_json', {})),
                'ingested_at': datetime.utcnow().isoformat()
            }
        elif event_type == 'sentiment_event':
            table_id = 'mvt_analytics.sentiment_events'
            row = {
                'date': event_data.get('date'),
                'event_type': event_data.get('event_subtype'),
                'severity': float(event_data.get('severity', 0)),
                'source': event_data.get('source'),
                'countries': event_data.get('countries', []),
                'raw_json': json.dumps(event_data.get('raw_json', {})),
                'ingested_at': datetime.utcnow().isoformat()
            }
        elif event_type == 'sovereign_indicator':
            table_id = 'mvt_analytics.sovereign_indicators'
            row = {
                'date': event_data.get('date'),
                'country': event_data.get('country'),
                'indicator': event_data.get('indicator'),
                'value': float(event_data.get('value', 0)),
                'risk_score': float(event_data.get('risk_score', 0)),
                'ingested_at': datetime.utcnow().isoformat()
            }
        elif event_type == 'gdelt_event':
            table_id = 'mvt_analytics.gdelt_events'
            row = {
                'date': event_data.get('date'),
                'event_code': event_data.get('event_code'),
                'event_root_code': event_data.get('event_root_code'),
                'actor1_country': event_data.get('actor1_country'),
                'actor2_country': event_data.get('actor2_country'),
                'goldstein_scale': float(event_data.get('goldstein_scale')) if event_data.get('goldstein_scale') else None,
                'num_mentions': int(event_data.get('num_mentions', 0)),
                'num_sources': int(event_data.get('num_sources', 0)),
                'num_articles': int(event_data.get('num_articles', 0)),
                'avg_tone': float(event_data.get('avg_tone')) if event_data.get('avg_tone') else None,
                'action_geo_country': event_data.get('action_geo_country'),
                'action_geo_lat': float(event_data.get('action_geo_lat')) if event_data.get('action_geo_lat') else None,
                'action_geo_long': float(event_data.get('action_geo_long')) if event_data.get('action_geo_long') else None,
                'ingested_at': datetime.utcnow().isoformat()
            }
        else:
            logger.warning(f"Unknown event_type: {event_type}")
            return {'status': 'error', 'message': f'Unknown event_type: {event_type}'}, 400

        # Streaming insert to BigQuery
        table = bq_client.get_table(table_id)
        errors = bq_client.insert_rows_json(table, [row])

        if errors:
            logger.error(f"BigQuery insert errors: {errors}")
            return {'status': 'error', 'message': f'BigQuery errors: {errors}'}, 500

        logger.info(f"Successfully inserted row into {table_id}")
        return {'status': 'success', 'table': table_id}, 200

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON: {str(e)}")
        return {'status': 'error', 'message': 'Invalid JSON'}, 400
    except ValueError as e:
        logger.error(f"Type conversion error: {str(e)}")
        return {'status': 'error', 'message': f'Type conversion error: {str(e)}'}, 400
    except Exception as e:
        logger.error(f"Error writing analytics: {str(e)}")
        return {'status': 'error', 'message': str(e)}, 500
