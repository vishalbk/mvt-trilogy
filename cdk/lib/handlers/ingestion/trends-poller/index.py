import json
import os
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
import time
from datetime import datetime, timedelta
from decimal import Decimal
import boto3
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')
events = boto3.client('events')

SIGNALS_TABLE = os.environ.get('SIGNALS_TABLE')
EVENT_BUS_NAME = os.environ.get('EVENT_BUS_NAME')

GOOGLE_TRENDS_RSS_URL = 'https://trends.google.com/trends/trendingsearches/daily/rss?geo=US'

DISTRESS_KEYWORDS = [
    'afford rent',
    'bankruptcy',
    'debt relief',
    'food stamps',
    'credit card debt',
    'unemployment',
    'cost of living',
    'paycheck',
    'eviction',
    'medical debt',
    'student loan',
    'wage'
]


def fetch_trends_feed(max_retries: int = 3) -> list:
    """Fetch Google Trends RSS feed."""
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(
                GOOGLE_TRENDS_RSS_URL,
                headers={'User-Agent': 'Mozilla/5.0'}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                xml_data = response.read().decode('utf-8')
                root = ET.fromstring(xml_data)

                # Parse RSS items
                items = []
                for item in root.findall('.//item'):
                    title_elem = item.find('title')
                    traffic_elem = item.find('ht:approxTraffic')

                    if title_elem is not None:
                        items.append({
                            'title': title_elem.text or '',
                            'traffic': traffic_elem.text if traffic_elem is not None else '0'
                        })

                return items[:20]
        except (urllib.error.URLError, ET.ParseError) as e:
            logger.warning(f"Attempt {attempt + 1} failed: {str(e)}")
            if attempt < max_retries - 1:
                continue
            return []
    return []


def compute_distress_score(trending_searches: list) -> float:
    """
    Compute inequality distress score (0-100).
    Count how many trending searches match distress keywords.
    """
    distress_count = 0
    total_count = len(trending_searches)

    for search in trending_searches:
        title = search.get('title', '').lower()
        for keyword in DISTRESS_KEYWORDS:
            if keyword in title:
                distress_count += 1
                break

    if total_count == 0:
        return 50.0

    # Scale distress_count to 0-100
    return (distress_count / total_count) * 100


def write_to_dynamodb(trends_data: list) -> None:
    """Write trending searches to DynamoDB."""
    table = dynamodb.Table(SIGNALS_TABLE)
    ttl_timestamp = int(time.time()) + 30*86400
    timestamp = datetime.utcnow().isoformat()

    with table.batch_writer() as batch:
        for idx, trend in enumerate(trends_data):
            try:
                title = trend.get('title', 'Unknown')
                traffic = int(trend.get('traffic', '0').replace('+', '').replace('K', '000'))

                # Check if it's a distress-related search
                is_distress = any(kw in title.lower() for kw in DISTRESS_KEYWORDS)
                signal_value = 75.0 if is_distress else 25.0

                signal_id = f"trends#{title}#{int(time.time())}"

                item = {
                    'dashboard': 'inequality_pulse',
                    'signalId_timestamp': signal_id,
                    'source': 'trends',
                    'value': Decimal(str(signal_value)),
                    'raw_data': {
                        'title': title,
                        'traffic': traffic,
                        'is_distress': is_distress,
                        'signal_value': Decimal(str(signal_value))
                    },
                    'ttl': ttl_timestamp
                }
                batch.put_item(Item=item)
                logger.info(f"Wrote trend: {title} (distress={is_distress}, signal={signal_value})")
            except Exception as e:
                logger.error(f"Error writing trend {idx}: {str(e)}")


def publish_event(distress_score: float, search_count: int, timestamp: str) -> None:
    """Publish EventBridge event."""
    try:
        event = {
            'Source': 'mvt.ingestion.inequality',
            'DetailType': 'TrendsDataUpdated',
            'EventBusName': EVENT_BUS_NAME,
            'Detail': json.dumps({
                'distress_score': distress_score,
                'search_count': search_count,
                'timestamp': timestamp
            })
        }
        events.put_events(Entries=[event])
        logger.info(f"Published TrendsDataUpdated: score={distress_score}, searches={search_count}")
    except Exception as e:
        logger.error(f"Error publishing event: {str(e)}")


def handler(event, context):
    """Main Lambda handler."""
    logger.info("Starting Google Trends data poller")

    try:
        logger.info("Fetching Google Trends RSS")
        trends = fetch_trends_feed()

        if trends:
            write_to_dynamodb(trends)
            timestamp = datetime.utcnow().isoformat()
            distress_score = compute_distress_score(trends)
            publish_event(distress_score, len(trends), timestamp)

            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'Trends polling completed',
                    'searches_processed': len(trends),
                    'distress_score': distress_score
                })
            }
        else:
            logger.warning("No trends fetched")
            return {
                'statusCode': 200,
                'body': json.dumps({'message': 'No trends', 'searches_processed': 0})
            }

    except Exception as e:
        logger.error(f"Unhandled error: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
