"""
EV1-02: mvt-history-writer Lambda
Triggered by DynamoDB Stream on mvt-dashboard-state table.
Copies dashboard-state writes to mvt-kpi-history for time-series storage.
Deduplicates by rounding timestamps to 1-minute granularity.
"""

import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal

import boto3

HISTORY_TABLE = os.environ.get('HISTORY_TABLE', 'mvt-kpi-history')
TTL_SECONDS = int(os.environ.get('TTL_SECONDS', '7776000'))  # 90 days

# Lazy init for testability
_history_table = None

def _get_history_table():
    global _history_table
    if _history_table is None:
        dynamodb = boto3.resource('dynamodb', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
        _history_table = dynamodb.Table(HISTORY_TABLE)
    return _history_table

# Panels that contain numeric KPI values worth tracking historically
TRACKABLE_PANELS = {
    # Overview tab
    'composite': {
        'vulnerability_score': {'metric': 'distress_index', 'value_key': 'vulnerability_score'},
    },
    'sentiment_seismic': {
        'trigger_probability': {'metric': 'trigger_probability', 'value_key': 'probability'},
        'market_metrics': {'metric_multi': True},  # SPY, QQQ, etc.
        'sector_sentiment': {'metric': 'sector_sentiment', 'value_key': 'sectors', 'is_map': True},
    },
    'sovereign_dominoes': {
        'risk_scores': {'metric': 'contagion_risk', 'value_key': 'country_scores', 'is_map': True},
    },
    'inequality_pulse': {
        'composite_score': {'metric': 'inequality_score', 'value_key': 'score'},
        'fred_metrics': {'metric_multi': True},  # Individual FRED metrics
    },
    'sre_observatory': {
        'health_status': {'metric': 'sre_health', 'value_key': 'health_score'},
        'cost_summary': {'metric': 'sre_cost', 'value_key': 'total_cost_24h'},
    },
    'predictions': {
        'kpi_forecasts': {'metric': 'prediction_health', 'value_key': 'model_health'},
    },
}


def handler(event, context):
    """Process DynamoDB Stream events from mvt-dashboard-state."""
    records_written = 0
    records_skipped = 0

    for record in event.get('Records', []):
        event_name = record.get('eventName', '')
        if event_name not in ('INSERT', 'MODIFY'):
            continue

        new_image = record.get('dynamodb', {}).get('NewImage', {})
        if not new_image:
            continue

        # Extract dashboard and panel from the stream record
        dashboard = _extract_string(new_image.get('dashboard', {}))
        panel = _extract_string(new_image.get('panel', {}))

        if not dashboard or not panel:
            records_skipped += 1
            continue

        # Round timestamp to nearest minute for dedup
        now = datetime.now(timezone.utc)
        minute_key = now.strftime('%Y-%m-%dT%H:%M:00Z')

        # Try to extract numeric values from the record
        try:
            items_to_write = _extract_history_items(dashboard, panel, new_image, minute_key, now)
            for item in items_to_write:
                _get_history_table().put_item(Item=item)
                records_written += 1
        except Exception as e:
            print(f"Error processing {dashboard}/{panel}: {e}")
            records_skipped += 1

    print(f"History writer: {records_written} written, {records_skipped} skipped")
    return {
        'statusCode': 200,
        'records_written': records_written,
        'records_skipped': records_skipped,
    }


def _extract_history_items(dashboard, panel, new_image, minute_key, now):
    """Extract history items from a dashboard-state stream record."""
    items = []
    ttl_value = int(now.timestamp()) + TTL_SECONDS

    # Try to extract all numeric fields from the record
    numeric_fields = {}
    for key, value_obj in new_image.items():
        if key in ('dashboard', 'panel', 'timestamp', 'ttl', 'updated_at'):
            continue
        val = _extract_number(value_obj)
        if val is not None:
            numeric_fields[key] = val

    # Also check for nested maps (like component_scores, country_scores)
    for key, value_obj in new_image.items():
        if 'M' in value_obj:
            map_data = value_obj['M']
            for sub_key, sub_val in map_data.items():
                val = _extract_number(sub_val)
                if val is not None:
                    numeric_fields[f"{key}.{sub_key}"] = val

    # Write each numeric field as a separate history entry
    for field_name, value in numeric_fields.items():
        pk = f"{dashboard}#{field_name}"
        item = {
            'pk': pk,
            'sk': minute_key,
            'timestamp': minute_key,
            'dashboard': dashboard,
            'metric': field_name,
            'value': Decimal(str(round(float(value), 6))),
            'ttl': ttl_value,
        }
        items.append(item)

    # If no individual fields found, try to write the whole panel as one entry
    if not items and numeric_fields:
        pk = f"{dashboard}#{panel}"
        item = {
            'pk': pk,
            'sk': minute_key,
            'timestamp': minute_key,
            'dashboard': dashboard,
            'metric': panel,
            'value': Decimal(str(list(numeric_fields.values())[0])),
            'ttl': ttl_value,
        }
        items.append(item)

    return items


def _extract_string(dynamo_value):
    """Extract string from DynamoDB stream format."""
    if isinstance(dynamo_value, dict):
        return dynamo_value.get('S', '')
    return str(dynamo_value) if dynamo_value else ''


def _extract_number(dynamo_value):
    """Extract numeric value from DynamoDB stream format."""
    if isinstance(dynamo_value, dict):
        if 'N' in dynamo_value:
            try:
                return float(dynamo_value['N'])
            except (ValueError, TypeError):
                return None
        if 'S' in dynamo_value:
            try:
                return float(dynamo_value['S'])
            except (ValueError, TypeError):
                return None
    return None
