import json
import functions_framework
from google.cloud import bigquery
from google.cloud import pubsub_v1
import requests
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bq_client = bigquery.Client()
publisher_client = pubsub_v1.PublisherClient()


@functions_framework.http
def extract_gdelt(request):
    """
    Runs GDELT extraction query and publishes results to Pub/Sub.
    Tracks query bytes processed for free tier monitoring.
    """
    try:
        logger.info("Starting GDELT extraction")

        # Run GDELT extraction query
        query = """
        SELECT
          PARSE_DATE('%Y%m%d', CAST(SQLDATE AS STRING)) as event_date,
          EventCode,
          EventRootCode,
          Actor1CountryCode as actor1_country,
          Actor2CountryCode as actor2_country,
          GoldsteinScale as goldstein_scale,
          NumMentions as num_mentions,
          NumSources as num_sources,
          NumArticles as num_articles,
          AvgTone as avg_tone,
          ActionGeo_CountryCode as action_geo_country,
          ActionGeo_Lat as action_geo_lat,
          ActionGeo_Long as action_geo_long
        FROM `gdelt-bq.gdeltv2.events`
        WHERE _PARTITIONTIME >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 DAY)
          AND EventRootCode IN ('14','17','18','19','20')
          AND NumMentions > 5
        ORDER BY NumMentions DESC
        LIMIT 1000
        """

        job_config = bigquery.QueryJobConfig()
        query_job = bq_client.query(query, job_config=job_config)
        results = query_job.result()

        bytes_processed = query_job.total_bytes_processed
        logger.info(f"Query processed {bytes_processed} bytes")

        # Publish each result to Pub/Sub for relay back to AWS
        topic_path = publisher_client.topic_path(bq_client.project, 'mvt-signals')
        published_count = 0

        for row in results:
            event_message = {
                'event_type': 'gdelt_event',
                'date': row['event_date'].isoformat() if row['event_date'] else None,
                'event_code': row['EventCode'],
                'event_root_code': row['EventRootCode'],
                'actor1_country': row['actor1_country'],
                'actor2_country': row['actor2_country'],
                'goldstein_scale': float(row['goldstein_scale']) if row['goldstein_scale'] else None,
                'num_mentions': int(row['num_mentions']),
                'num_sources': int(row['num_sources']),
                'num_articles': int(row['num_articles']),
                'avg_tone': float(row['avg_tone']) if row['avg_tone'] else None,
                'action_geo_country': row['action_geo_country'],
                'action_geo_lat': float(row['action_geo_lat']) if row['action_geo_lat'] else None,
                'action_geo_long': float(row['action_geo_long']) if row['action_geo_long'] else None,
                'extracted_at': datetime.utcnow().isoformat()
            }

            message_json = json.dumps(event_message).encode('utf-8')
            future = publisher_client.publish(topic_path, message_json)
            future.result()
            published_count += 1

        logger.info(f"Published {published_count} GDELT events to Pub/Sub")

        return {
            'status': 'success',
            'events_extracted': published_count,
            'bytes_processed': bytes_processed
        }, 200

    except Exception as e:
        logger.error(f"Error extracting GDELT data: {str(e)}")
        return {'status': 'error', 'message': str(e)}, 500
