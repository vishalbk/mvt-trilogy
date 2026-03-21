"""
MVT Backtesting Engine (S6-02)
Evaluates historical accuracy of prediction models by comparing
past forecasts against actual observed values.

Computes MAE, RMSE, MAPE, and directional accuracy for each KPI
across all prediction horizons.

Trigger: EventBridge schedule (every 24 hours)
Output: dashboard-state entries under dashboard='predictions', component='backtest_results'
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

FORECAST_KPIS = [
    'distress_index',
    'trigger_probability',
    'contagion_risk',
    'vix_level',
]

HORIZONS = [7, 14, 30]


def get_past_predictions(table, kpi, lookback_days=60):
    """
    Retrieve historical prediction records from dashboard-state.
    Returns list of prediction entries with timestamps.
    """
    try:
        response = table.query(
            KeyConditionExpression=Key('dashboard').eq('predictions') & Key('panel').eq(f'forecast_{kpi}'),
            ScanIndexForward=True,
            Limit=500
        )
        items = response.get('Items', [])
        return items
    except Exception as e:
        logger.warning(f"Failed to fetch past predictions for {kpi}: {e}")
        return []


def get_actual_value_at_date(table, kpi, target_date):
    """
    Get the actual KPI value closest to a target date.
    Searches dashboard-state for the overview dashboard entries.
    """
    try:
        response = table.query(
            KeyConditionExpression=Key('dashboard').eq('overview') & Key('panel').begins_with(kpi),
            ScanIndexForward=False,
            Limit=100
        )
        items = response.get('Items', [])

        # Find closest match to target_date
        target_ts = target_date.isoformat()
        best_item = None
        best_diff = float('inf')

        for item in items:
            item_ts = item.get('updated_at', '')
            if item_ts:
                try:
                    item_dt = datetime.fromisoformat(item_ts.replace('Z', '+00:00').replace('+00:00', ''))
                    diff = abs((item_dt - target_date).total_seconds())
                    if diff < best_diff:
                        best_diff = diff
                        best_item = item
                except (ValueError, TypeError):
                    continue

        if best_item and best_diff < 86400 * 2:  # Within 2 days
            payload = best_item.get('payload', {})
            if isinstance(payload, str):
                payload = json.loads(payload)
            val = payload.get('value') or payload.get('current_value')
            if val is not None:
                return float(val)

        return None
    except Exception as e:
        logger.warning(f"Failed to get actual value for {kpi} at {target_date}: {e}")
        return None


def compute_accuracy_metrics(predictions_actuals):
    """
    Compute accuracy metrics from list of (predicted, actual) tuples.

    Returns dict with MAE, RMSE, MAPE, directional_accuracy, count.
    """
    if not predictions_actuals:
        return None

    n = len(predictions_actuals)
    errors = []
    abs_errors = []
    pct_errors = []
    direction_correct = 0
    prev_actual = None

    for pred, actual in predictions_actuals:
        error = pred - actual
        errors.append(error)
        abs_errors.append(abs(error))

        if actual != 0:
            pct_errors.append(abs(error) / abs(actual) * 100)

        # Directional accuracy: did we predict the right direction?
        if prev_actual is not None:
            pred_direction = 'up' if pred > prev_actual else 'down'
            actual_direction = 'up' if actual > prev_actual else 'down'
            if pred_direction == actual_direction:
                direction_correct += 1

        prev_actual = actual

    mae = sum(abs_errors) / n
    rmse = math.sqrt(sum(e ** 2 for e in errors) / n)
    mape = sum(pct_errors) / len(pct_errors) if pct_errors else 0
    dir_accuracy = (direction_correct / (n - 1) * 100) if n > 1 else 0

    # Bias: systematic over/under prediction
    mean_error = sum(errors) / n
    bias = 'over' if mean_error > 0 else 'under' if mean_error < 0 else 'neutral'

    return {
        'count': n,
        'mae': round(mae, 4),
        'rmse': round(rmse, 4),
        'mape': round(mape, 2),
        'directional_accuracy_pct': round(dir_accuracy, 1),
        'mean_error': round(mean_error, 4),
        'bias': bias,
    }


def grade_accuracy(metrics):
    """
    Assign a letter grade based on MAPE.
    """
    if metrics is None:
        return 'N/A'

    mape = metrics.get('mape', 100)
    if mape < 5:
        return 'A'
    elif mape < 10:
        return 'B'
    elif mape < 20:
        return 'C'
    elif mape < 30:
        return 'D'
    else:
        return 'F'


def backtest_kpi(state_table, kpi):
    """
    Run backtesting for a single KPI across all horizons.
    """
    past_predictions = get_past_predictions(state_table, kpi)

    if not past_predictions:
        logger.info(f"No past predictions found for {kpi}")
        return {
            'status': 'no_predictions',
            'horizons': {},
        }

    results_by_horizon = {}

    for horizon in HORIZONS:
        horizon_key = f'{horizon}d'
        pairs = []

        for pred_item in past_predictions:
            payload = pred_item.get('payload', {})
            if isinstance(payload, str):
                payload = json.loads(payload)

            forecasts = payload.get('forecasts', {})
            horizon_data = forecasts.get(horizon_key, {})
            point_estimate = horizon_data.get('point_estimate')

            pred_timestamp = payload.get('timestamp') or pred_item.get('updated_at', '')

            if point_estimate is None or not pred_timestamp:
                continue

            # Calculate the date the prediction was for
            try:
                pred_dt = datetime.fromisoformat(pred_timestamp.replace('Z', '+00:00').replace('+00:00', ''))
                target_dt = pred_dt + timedelta(days=horizon)
            except (ValueError, TypeError):
                continue

            # Only backtest if the target date is in the past
            if target_dt > datetime.utcnow():
                continue

            # Get actual value at target date
            actual = get_actual_value_at_date(state_table, kpi, target_dt)
            if actual is not None:
                pairs.append((float(point_estimate), actual))

        metrics = compute_accuracy_metrics(pairs)
        grade = grade_accuracy(metrics)

        results_by_horizon[horizon_key] = {
            'metrics': metrics,
            'grade': grade,
            'pairs_evaluated': len(pairs),
        }

    return {
        'status': 'ok',
        'horizons': results_by_horizon,
    }


def compute_model_health_score(all_results):
    """
    Compute an overall model health score (0-100) across all KPIs and horizons.
    Based on weighted average of MAPE scores.
    """
    scores = []
    weights = {'7d': 3, '14d': 2, '30d': 1}  # Short-term accuracy matters more

    for kpi, result in all_results.items():
        if result.get('status') != 'ok':
            continue

        for horizon_key, horizon_result in result.get('horizons', {}).items():
            metrics = horizon_result.get('metrics')
            if metrics and metrics.get('count', 0) > 0:
                mape = metrics.get('mape', 100)
                # Convert MAPE to 0-100 score (lower MAPE = higher score)
                score = max(0, 100 - mape)
                weight = weights.get(horizon_key, 1)
                scores.append((score, weight))

    if not scores:
        return 0

    weighted_sum = sum(s * w for s, w in scores)
    total_weight = sum(w for _, w in scores)

    return round(weighted_sum / total_weight, 1)


def handler(event, context):
    """
    Main Lambda handler. Runs backtesting for all KPIs.
    """
    logger.info("Starting backtesting engine run")

    state_table = dynamodb.Table(DASHBOARD_STATE_TABLE)
    timestamp = datetime.utcnow().isoformat() + 'Z'

    all_results = {}

    for kpi in FORECAST_KPIS:
        logger.info(f"Backtesting {kpi}")
        all_results[kpi] = backtest_kpi(state_table, kpi)

    # Compute overall model health
    model_health = compute_model_health_score(all_results)

    # Build output payload
    backtest_payload = {
        'timestamp': timestamp,
        'model_health_score': model_health,
        'kpis': all_results,
    }

    # Write to dashboard-state
    payload_safe = json.loads(
        json.dumps(backtest_payload, default=str),
        parse_float=lambda x: Decimal(str(x))
    )

    try:
        state_table.put_item(Item={
            'dashboard': 'predictions',
            'panel': 'backtest_results',
            'payload': payload_safe,
            'updated_at': timestamp,
            'ttl': int((datetime.utcnow() + timedelta(days=90)).timestamp()),
        })
        logger.info("Backtest results written to dashboard-state")
    except Exception as e:
        logger.error(f"Failed to write backtest results: {e}")

    # Summary
    evaluated = sum(
        1 for r in all_results.values()
        if r.get('status') == 'ok' and any(
            h.get('pairs_evaluated', 0) > 0
            for h in r.get('horizons', {}).values()
        )
    )

    summary = {
        'timestamp': timestamp,
        'kpis_evaluated': evaluated,
        'model_health_score': model_health,
    }

    logger.info(f"Backtesting complete: {json.dumps(summary)}")

    return {
        'statusCode': 200,
        'body': json.dumps(summary, default=str),
    }
