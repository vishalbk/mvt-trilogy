"""
MVT Distress Prediction Model (S6-01)
Time-series forecasting for Distress Index using exponential smoothing
and weighted moving averages on historical signals from DynamoDB.

Generates 7-day, 14-day, and 30-day forecasts with confidence intervals.

Trigger: EventBridge schedule (every 6 hours)
Output: dashboard-state entries under dashboard='predictions'
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

SIGNALS_TABLE = os.environ.get('SIGNALS_TABLE', 'mvt-signals')
DASHBOARD_STATE_TABLE = os.environ.get('DASHBOARD_STATE_TABLE', 'mvt-dashboard-state')

# Prediction horizons (days)
HORIZONS = [7, 14, 30]

# Exponential smoothing parameters
ALPHA = 0.3        # Level smoothing
BETA = 0.1         # Trend smoothing
SEASONAL_PERIOD = 7  # Weekly seasonality

# Confidence interval z-scores
CONFIDENCE_LEVELS = {
    '80': 1.282,
    '95': 1.960,
}

# KPIs to forecast
FORECAST_KPIS = [
    'distress_index',
    'trigger_probability',
    'contagion_risk',
    'vix_level',
]


def get_historical_values(table, kpi_name, days=90):
    """
    Fetch historical KPI values from dashboard-state table.
    Returns list of (timestamp, value) tuples sorted by time.
    """
    try:
        response = table.query(
            KeyConditionExpression=Key('dashboard').eq('overview') & Key('panel').begins_with(kpi_name),
            ScanIndexForward=True,
            Limit=1000
        )
        items = response.get('Items', [])

        values = []
        for item in items:
            payload = item.get('payload', {})
            if isinstance(payload, str):
                payload = json.loads(payload)

            val = payload.get('value') or payload.get('current_value')
            ts = payload.get('timestamp') or item.get('updated_at', '')

            if val is not None:
                try:
                    values.append((str(ts), float(val)))
                except (ValueError, TypeError):
                    continue

        return values
    except Exception as e:
        logger.warning(f"Failed to fetch history for {kpi_name}: {e}")
        return []


def get_signals_history(table, signal_type, days=90):
    """
    Fetch raw signal history from mvt-signals table.
    Falls back to this if dashboard-state doesn't have enough history.
    """
    try:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        response = table.query(
            KeyConditionExpression=Key('signalType').eq(signal_type),
            FilterExpression=Key('timestamp').gte(cutoff),
            ScanIndexForward=True,
            Limit=2000
        )
        items = response.get('Items', [])

        values = []
        for item in items:
            ts = item.get('timestamp', '')
            val = item.get('value')
            if val is not None:
                try:
                    values.append((str(ts), float(val)))
                except (ValueError, TypeError):
                    continue

        return values
    except Exception as e:
        logger.warning(f"Failed to fetch signals for {signal_type}: {e}")
        return []


def exponential_smoothing(values, alpha=ALPHA, beta=BETA):
    """
    Double exponential smoothing (Holt's method).
    Returns (level, trend, residuals, std_error).
    """
    if len(values) < 3:
        return None

    # Initialize
    level = values[0]
    trend = values[1] - values[0]
    residuals = []

    levels = [level]
    trends = [trend]

    for i in range(1, len(values)):
        prev_level = level
        prev_trend = trend

        # Update level and trend
        level = alpha * values[i] + (1 - alpha) * (prev_level + prev_trend)
        trend = beta * (level - prev_level) + (1 - beta) * prev_trend

        # Track residual
        predicted = prev_level + prev_trend
        residuals.append(values[i] - predicted)

        levels.append(level)
        trends.append(trend)

    # Compute standard error of residuals
    if len(residuals) > 1:
        mean_residual = sum(residuals) / len(residuals)
        variance = sum((r - mean_residual) ** 2 for r in residuals) / (len(residuals) - 1)
        std_error = math.sqrt(variance)
    else:
        std_error = abs(values[-1]) * 0.1  # Fallback: 10% of last value

    return {
        'level': level,
        'trend': trend,
        'std_error': std_error,
        'residuals': residuals,
    }


def weighted_moving_average(values, window=14):
    """
    Weighted moving average with linearly increasing weights.
    Returns the WMA of the last `window` values.
    """
    if len(values) < window:
        window = len(values)
    if window < 1:
        return None

    recent = values[-window:]
    weights = list(range(1, window + 1))
    total_weight = sum(weights)

    wma = sum(v * w for v, w in zip(recent, weights)) / total_weight
    return wma


def forecast_kpi(values, horizons=HORIZONS):
    """
    Generate forecasts for given horizons using ensemble of
    exponential smoothing and weighted moving average.

    Returns dict with predictions and confidence intervals.
    """
    if len(values) < 5:
        return None

    # Extract just the numeric values
    nums = [v[1] for v in values]

    # Method 1: Double exponential smoothing
    es_result = exponential_smoothing(nums)
    if es_result is None:
        return None

    # Method 2: Weighted moving average trend
    wma_current = weighted_moving_average(nums, 14)
    wma_short = weighted_moving_average(nums, 7)

    # Daily trend from WMA
    if wma_short is not None and wma_current is not None and wma_current != 0:
        wma_trend = (wma_short - wma_current) / 7
    else:
        wma_trend = 0

    forecasts = {}
    for horizon in horizons:
        # Exponential smoothing forecast
        es_forecast = es_result['level'] + es_result['trend'] * horizon

        # WMA forecast
        wma_forecast = (wma_current or nums[-1]) + wma_trend * horizon

        # Ensemble: weighted average (ES gets 60%, WMA gets 40%)
        ensemble = 0.6 * es_forecast + 0.4 * wma_forecast

        # Confidence intervals widen with horizon
        horizon_factor = math.sqrt(horizon)
        std_error = es_result['std_error']

        intervals = {}
        for conf_name, z_score in CONFIDENCE_LEVELS.items():
            margin = z_score * std_error * horizon_factor
            intervals[f'ci_{conf_name}_lower'] = max(0, ensemble - margin)
            intervals[f'ci_{conf_name}_upper'] = ensemble + margin

        forecasts[f'{horizon}d'] = {
            'point_estimate': round(ensemble, 4),
            'es_component': round(es_forecast, 4),
            'wma_component': round(wma_forecast, 4),
            **{k: round(v, 4) for k, v in intervals.items()},
            'std_error': round(std_error * horizon_factor, 4),
        }

    return forecasts


def compute_trend_direction(values, window=7):
    """
    Determine if the KPI is trending up, down, or stable.
    """
    if len(values) < window:
        return 'insufficient_data'

    recent = [v[1] for v in values[-window:]]
    earlier = [v[1] for v in values[-(window * 2):-window]] if len(values) >= window * 2 else [v[1] for v in values[:window]]

    recent_avg = sum(recent) / len(recent)
    earlier_avg = sum(earlier) / len(earlier) if earlier else recent_avg

    if earlier_avg == 0:
        return 'stable'

    change_pct = (recent_avg - earlier_avg) / abs(earlier_avg) * 100

    if change_pct > 5:
        return 'rising'
    elif change_pct < -5:
        return 'falling'
    else:
        return 'stable'


def handler(event, context):
    """
    Main Lambda handler. Generates forecasts for all 4 KPIs.
    """
    logger.info("Starting distress prediction model run")

    state_table = dynamodb.Table(DASHBOARD_STATE_TABLE)
    signals_table = dynamodb.Table(SIGNALS_TABLE)

    timestamp = datetime.utcnow().isoformat() + 'Z'
    results = {}

    # Signal type mapping for fallback data source
    signal_type_map = {
        'distress_index': 'composite_distress',
        'trigger_probability': 'trigger_prob',
        'contagion_risk': 'contagion_risk',
        'vix_level': 'market_vix',
    }

    for kpi in FORECAST_KPIS:
        logger.info(f"Forecasting {kpi}")

        # Try dashboard-state first, fall back to signals table
        values = get_historical_values(state_table, kpi)
        if len(values) < 10:
            signal_type = signal_type_map.get(kpi, kpi)
            values = get_signals_history(signals_table, signal_type)

        if len(values) < 5:
            logger.warning(f"Insufficient data for {kpi}: {len(values)} points")
            results[kpi] = {
                'status': 'insufficient_data',
                'data_points': len(values),
                'forecasts': None,
            }
            continue

        # Generate forecasts
        forecasts = forecast_kpi(values)
        trend = compute_trend_direction(values)

        # Current value stats
        recent_values = [v[1] for v in values[-7:]]
        current = values[-1][1]
        mean_7d = sum(recent_values) / len(recent_values) if recent_values else current
        min_7d = min(recent_values) if recent_values else current
        max_7d = max(recent_values) if recent_values else current

        results[kpi] = {
            'status': 'ok',
            'data_points': len(values),
            'current_value': current,
            'mean_7d': round(mean_7d, 4),
            'min_7d': round(min_7d, 4),
            'max_7d': round(max_7d, 4),
            'trend': trend,
            'forecasts': forecasts,
        }

    # Write predictions to dashboard-state
    prediction_payload = {
        'timestamp': timestamp,
        'model_version': '1.0.0',
        'method': 'ensemble_es_wma',
        'kpis': {},
    }

    for kpi, result in results.items():
        prediction_payload['kpis'][kpi] = result

    # Convert to DynamoDB-safe format
    payload_safe = json.loads(json.dumps(prediction_payload), parse_float=lambda x: Decimal(str(x)))

    try:
        state_table.put_item(Item={
            'dashboard': 'predictions',
            'panel': 'kpi_forecasts',
            'payload': payload_safe,
            'updated_at': timestamp,
            'ttl': int((datetime.utcnow() + timedelta(days=30)).timestamp()),
        })
        logger.info("Predictions written to dashboard-state")
    except Exception as e:
        logger.error(f"Failed to write predictions: {e}")

    # Also write per-KPI entries for granular frontend consumption
    for kpi, result in results.items():
        if result.get('forecasts'):
            kpi_payload = json.loads(json.dumps(result), parse_float=lambda x: Decimal(str(x)))
            try:
                state_table.put_item(Item={
                    'dashboard': 'predictions',
                    'panel': f'forecast_{kpi}',
                    'payload': kpi_payload,
                    'updated_at': timestamp,
                    'ttl': int((datetime.utcnow() + timedelta(days=30)).timestamp()),
                })
            except Exception as e:
                logger.error(f"Failed to write {kpi} forecast: {e}")

    # Summary
    forecasted_count = sum(1 for r in results.values() if r.get('forecasts'))
    summary = {
        'timestamp': timestamp,
        'kpis_forecasted': forecasted_count,
        'kpis_skipped': len(FORECAST_KPIS) - forecasted_count,
        'horizons': HORIZONS,
        'model': 'ensemble_es_wma_v1',
    }

    logger.info(f"Prediction run complete: {json.dumps(summary)}")

    return {
        'statusCode': 200,
        'body': json.dumps(summary, default=str),
    }
