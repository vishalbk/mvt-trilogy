"""
MVT Anomaly Classifier (S6-04)
Classifies detected anomalies by type: structural break, volatility spike,
mean reversion, regime change, or outlier. Enriches alert messages with
classification context and recommended actions.

Trigger: EventBridge event from cost-anomaly or direct invocation
Output: dashboard-state entries under dashboard='predictions', component='anomaly_classifications'
        Enhanced Telegram alerts with classification context
"""

import json
import os
import logging
import math
from datetime import datetime, timedelta
from decimal import Decimal
import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')

DASHBOARD_STATE_TABLE = os.environ.get('DASHBOARD_STATE_TABLE', 'mvt-dashboard-state')
SIGNALS_TABLE = os.environ.get('SIGNALS_TABLE', 'mvt-signals')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')

# Anomaly type definitions
ANOMALY_TYPES = {
    'structural_break': {
        'label': 'Structural Break',
        'emoji': '🔴',
        'description': 'Fundamental shift in the data-generating process',
        'severity': 'critical',
        'action': 'Investigate root cause; consider model recalibration',
    },
    'volatility_spike': {
        'label': 'Volatility Spike',
        'emoji': '🟡',
        'description': 'Sudden increase in variance without mean shift',
        'severity': 'warning',
        'action': 'Monitor for persistence; may revert within 24-48h',
    },
    'mean_reversion': {
        'label': 'Mean Reversion',
        'emoji': '🟢',
        'description': 'Value returning to long-term average after deviation',
        'severity': 'info',
        'action': 'Expected behavior; no action needed',
    },
    'regime_change': {
        'label': 'Regime Change',
        'emoji': '🟠',
        'description': 'Transition between stable states (e.g., bull to bear)',
        'severity': 'high',
        'action': 'Update model parameters; alert stakeholders',
    },
    'outlier': {
        'label': 'Outlier',
        'emoji': '⚪',
        'description': 'Single data point far from distribution',
        'severity': 'low',
        'action': 'Verify data quality; likely transient',
    },
}


def compute_statistics(values):
    """
    Compute descriptive statistics for a value series.
    """
    if not values or len(values) < 2:
        return None

    n = len(values)
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    std = math.sqrt(variance) if variance > 0 else 0

    # Compute rolling statistics for regime detection
    half = n // 2
    first_half = values[:half]
    second_half = values[half:]

    first_mean = sum(first_half) / len(first_half) if first_half else mean
    second_mean = sum(second_half) / len(second_half) if second_half else mean

    first_var = sum((v - first_mean) ** 2 for v in first_half) / max(len(first_half) - 1, 1)
    second_var = sum((v - second_mean) ** 2 for v in second_half) / max(len(second_half) - 1, 1)

    return {
        'mean': mean,
        'std': std,
        'variance': variance,
        'first_half_mean': first_mean,
        'second_half_mean': second_mean,
        'first_half_var': first_var,
        'second_half_var': second_var,
        'min': min(values),
        'max': max(values),
        'range': max(values) - min(values),
    }


def detect_structural_break(values, stats):
    """
    Detect structural break using CUSUM-like approach.
    Returns confidence score 0-1.
    """
    if stats is None or stats['std'] == 0:
        return 0

    mean_shift = abs(stats['second_half_mean'] - stats['first_half_mean'])
    normalized_shift = mean_shift / stats['std']

    # Strong structural break: mean shift > 2 std deviations
    if normalized_shift > 2.0:
        return min(1.0, normalized_shift / 4.0)
    return normalized_shift / 4.0


def detect_volatility_spike(values, stats, window=7):
    """
    Detect volatility spike by comparing recent variance to historical.
    """
    if stats is None or len(values) < window * 2:
        return 0

    recent = values[-window:]
    historical = values[:-window]

    recent_var = sum((v - sum(recent) / len(recent)) ** 2 for v in recent) / max(len(recent) - 1, 1)
    hist_var = sum((v - sum(historical) / len(historical)) ** 2 for v in historical) / max(len(historical) - 1, 1)

    if hist_var == 0:
        return 0

    ratio = recent_var / hist_var

    # Volatility spike: recent variance > 3x historical
    if ratio > 3.0:
        return min(1.0, ratio / 6.0)
    return ratio / 6.0


def detect_mean_reversion(values, stats, window=7):
    """
    Detect mean reversion — recent values moving toward long-term mean.
    """
    if stats is None or len(values) < window + 5:
        return 0

    recent = values[-window:]
    long_term_mean = stats['mean']

    # Check if recent values are converging toward mean
    deviations = [abs(v - long_term_mean) for v in recent]
    earlier_deviations = [abs(v - long_term_mean) for v in values[-(window * 2):-window]]

    if not earlier_deviations:
        return 0

    recent_avg_dev = sum(deviations) / len(deviations)
    earlier_avg_dev = sum(earlier_deviations) / len(earlier_deviations)

    if earlier_avg_dev == 0:
        return 0

    # Mean reversion: deviations decreasing
    reversion_ratio = 1 - (recent_avg_dev / earlier_avg_dev)

    if reversion_ratio > 0.3:
        return min(1.0, reversion_ratio)
    return max(0, reversion_ratio)


def detect_regime_change(values, stats):
    """
    Detect regime change using variance ratio test.
    """
    if stats is None:
        return 0

    var_ratio = stats['second_half_var'] / max(stats['first_half_var'], 1e-10)
    mean_shift = abs(stats['second_half_mean'] - stats['first_half_mean']) / max(stats['std'], 1e-10)

    # Regime change: both mean and variance shift moderately
    if mean_shift > 1.0 and (var_ratio > 2.0 or var_ratio < 0.5):
        score = min(1.0, (mean_shift * abs(math.log(max(var_ratio, 0.01)))) / 4.0)
        return score
    return 0


def detect_outlier(values, stats, threshold=3.0):
    """
    Detect if the most recent value is an outlier.
    """
    if stats is None or stats['std'] == 0:
        return 0

    latest = values[-1]
    z_score = abs(latest - stats['mean']) / stats['std']

    if z_score > threshold:
        return min(1.0, z_score / (threshold * 2))
    return z_score / (threshold * 2)


def classify_anomaly(values):
    """
    Classify an anomaly using all detection methods.
    Returns the most likely classification with confidence scores.
    """
    if len(values) < 10:
        return {
            'type': 'insufficient_data',
            'confidence': 0,
            'all_scores': {},
        }

    stats = compute_statistics(values)

    scores = {
        'structural_break': detect_structural_break(values, stats),
        'volatility_spike': detect_volatility_spike(values, stats),
        'mean_reversion': detect_mean_reversion(values, stats),
        'regime_change': detect_regime_change(values, stats),
        'outlier': detect_outlier(values, stats),
    }

    # Find the classification with highest confidence
    best_type = max(scores, key=scores.get)
    best_score = scores[best_type]

    # If no type has meaningful confidence, classify as outlier
    if best_score < 0.15:
        best_type = 'outlier'
        best_score = scores['outlier']

    return {
        'type': best_type,
        'confidence': round(best_score, 3),
        'all_scores': {k: round(v, 3) for k, v in scores.items()},
        'metadata': ANOMALY_TYPES.get(best_type, {}),
        'statistics': {
            'mean': round(stats['mean'], 4) if stats else None,
            'std': round(stats['std'], 4) if stats else None,
            'latest_value': values[-1] if values else None,
            'z_score': round(abs(values[-1] - stats['mean']) / max(stats['std'], 1e-10), 2) if stats else None,
        },
    }


def get_kpi_values(table, kpi, days=30):
    """
    Fetch recent KPI values for classification.
    """
    try:
        response = table.query(
            KeyConditionExpression=Key('dashboard').eq('overview') & Key('component').begins_with(kpi),
            ScanIndexForward=True,
            Limit=500
        )
        items = response.get('Items', [])
        values = []
        for item in items:
            payload = item.get('payload', {})
            if isinstance(payload, str):
                payload = json.loads(payload)
            val = payload.get('value') or payload.get('current_value')
            if val is not None:
                try:
                    values.append(float(val))
                except (ValueError, TypeError):
                    continue
        return values
    except Exception as e:
        logger.warning(f"Failed to fetch KPI values for {kpi}: {e}")
        return []


def handler(event, context):
    """
    Main Lambda handler. Classifies anomalies for all KPIs or
    processes a specific anomaly event.
    """
    logger.info(f"Starting anomaly classification: {json.dumps(event, default=str)}")

    state_table = dynamodb.Table(DASHBOARD_STATE_TABLE)
    timestamp = datetime.utcnow().isoformat() + 'Z'

    kpis = ['distress_index', 'trigger_probability', 'contagion_risk', 'vix_level']

    # Check if this is triggered by a specific anomaly event
    event_kpi = None
    if isinstance(event, dict):
        detail = event.get('detail', {})
        event_kpi = detail.get('kpi')
        if event_kpi:
            kpis = [event_kpi]

    classifications = {}

    for kpi in kpis:
        values = get_kpi_values(state_table, kpi)
        classification = classify_anomaly(values)
        classifications[kpi] = classification
        logger.info(f"{kpi}: {classification['type']} (confidence: {classification['confidence']})")

    # Write to dashboard-state
    payload = {
        'timestamp': timestamp,
        'classifications': classifications,
        'triggered_by': 'event' if event_kpi else 'schedule',
    }

    payload_safe = json.loads(
        json.dumps(payload, default=str),
        parse_float=lambda x: Decimal(str(x))
    )

    try:
        state_table.put_item(Item={
            'dashboard': 'predictions',
            'component': 'anomaly_classifications',
            'payload': payload_safe,
            'updated_at': timestamp,
            'ttl': int((datetime.utcnow() + timedelta(days=30)).timestamp()),
        })
        logger.info("Anomaly classifications written to dashboard-state")
    except Exception as e:
        logger.error(f"Failed to write classifications: {e}")

    # Count significant anomalies (non-outlier, non-mean-reversion with high confidence)
    significant = [
        (kpi, c) for kpi, c in classifications.items()
        if c['type'] not in ('outlier', 'mean_reversion', 'insufficient_data')
        and c['confidence'] > 0.5
    ]

    summary = {
        'timestamp': timestamp,
        'total_classified': len(classifications),
        'significant_anomalies': len(significant),
        'details': {kpi: c['type'] for kpi, c in classifications.items()},
    }

    logger.info(f"Classification complete: {json.dumps(summary)}")

    return {
        'statusCode': 200,
        'body': json.dumps(summary, default=str),
    }
