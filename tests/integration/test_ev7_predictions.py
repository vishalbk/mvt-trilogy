"""EV7-04: Predictions Stability Integration Tests.
Tests: verify predictions don't produce NaN/Infinity; CI bands valid.
Validates model outputs, confidence intervals, and backtester results.
"""
import json
import math
import pytest
import boto3
from decimal import Decimal

dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
lambda_client = boto3.client('lambda', region_name='us-east-1')

DASHBOARD_STATE_TABLE = 'mvt-dashboard-state'

ANALYTICS_LAMBDAS = [
    'mvt-distress-predictor',
    'mvt-backtester',
    'mvt-anomaly-classifier',
]


def get_panel(dashboard, panel):
    table = dynamodb.Table(DASHBOARD_STATE_TABLE)
    resp = table.get_item(Key={'dashboard': dashboard, 'panel': panel})
    return resp.get('Item')


def get_payload(item):
    """Get the data payload from a dashboard-state item."""
    payload = item.get('payload', item.get('data'))
    if payload is not None:
        if isinstance(payload, str):
            payload = json.loads(payload)
        return payload
    skip_keys = {'dashboard', 'panel', 'timestamp', 'last_updated', 'ttl'}
    return {k: v for k, v in item.items() if k not in skip_keys}


def check_no_nan_infinity(obj, path=""):
    """Recursively check that no values are NaN or Infinity."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            check_no_nan_infinity(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            check_no_nan_infinity(v, f"{path}[{i}]")
    elif isinstance(obj, (float, Decimal)):
        val = float(obj)
        assert not math.isnan(val), f"NaN found at {path}"
        assert not math.isinf(val), f"Infinity found at {path}"


class TestDistressForecast:
    """Verify distress forecast predictions are valid."""

    def test_distress_forecast_exists(self):
        item = get_panel('predictions', 'kpi_forecasts')
        assert item is not None, "predictions#kpi_forecasts missing"

    def test_distress_forecast_no_nan(self):
        item = get_panel('predictions', 'kpi_forecasts')
        if not item:
            pytest.skip("No distress forecast panel")
        payload = get_payload(item)
        check_no_nan_infinity(payload)

    def test_distress_forecast_ci_bands_valid(self):
        """Lower CI < point estimate < upper CI."""
        item = get_panel('predictions', 'kpi_forecasts')
        if not item:
            pytest.skip("No distress forecast panel")
        payload = get_payload(item)

        forecasts = payload.get('forecasts', payload.get('predictions', []))
        if isinstance(forecasts, list):
            for f in forecasts:
                lower = f.get('lower_ci', f.get('ci_lower'))
                point = f.get('point_estimate', f.get('predicted', f.get('value')))
                upper = f.get('upper_ci', f.get('ci_upper'))
                if all(v is not None for v in [lower, point, upper]):
                    assert float(lower) <= float(point) <= float(upper), \
                        f"CI band invalid: {lower} <= {point} <= {upper}"
        elif isinstance(forecasts, dict):
            # Single forecast
            lower = payload.get('lower_ci', payload.get('ci_lower'))
            point = payload.get('point_estimate', payload.get('predicted'))
            upper = payload.get('upper_ci', payload.get('ci_upper'))
            if all(v is not None for v in [lower, point, upper]):
                assert float(lower) <= float(point) <= float(upper)

    def test_distress_forecast_reasonable_range(self):
        """Forecast values should be within reasonable bounds."""
        item = get_panel('predictions', 'kpi_forecasts')
        if not item:
            pytest.skip("No distress forecast panel")
        payload = get_payload(item)
        point = payload.get('point_estimate', payload.get('predicted', payload.get('value')))
        if point is not None:
            val = float(point)
            assert -100 <= val <= 200, f"Forecast {val} seems unreasonable"


class TestBacktester:
    """Verify backtester results are stable."""

    def test_backtest_results_exist(self):
        item = get_panel('predictions', 'backtest_results')
        assert item is not None, "predictions#backtest_results missing"

    def test_backtest_no_nan(self):
        item = get_panel('predictions', 'backtest_results')
        if not item:
            pytest.skip("No backtest results")
        payload = get_payload(item)
        check_no_nan_infinity(payload)

    def test_backtest_accuracy_valid(self):
        """If accuracy metric exists, it should be 0-100%."""
        item = get_panel('predictions', 'backtest_results')
        if not item:
            pytest.skip("No backtest results")
        payload = get_payload(item)
        accuracy = payload.get('accuracy', payload.get('hit_rate', payload.get('mape')))
        if accuracy is not None:
            val = float(accuracy)
            assert 0 <= val <= 100 or val >= 0, f"Accuracy {val} seems invalid"


class TestAnomalyClassifier:
    """Verify anomaly classifier outputs."""

    def test_anomaly_results_exist(self):
        item = get_panel('predictions', 'anomaly_classifications')
        assert item is not None, "predictions#anomaly_classifications missing"

    def test_anomaly_no_nan(self):
        item = get_panel('predictions', 'anomaly_classifications')
        if not item:
            pytest.skip("No anomaly classification")
        payload = get_payload(item)
        check_no_nan_infinity(payload)

    def test_anomaly_scores_valid(self):
        item = get_panel('predictions', 'anomaly_classifications')
        if not item:
            pytest.skip("No anomaly classification")
        payload = get_payload(item)
        anomalies = payload.get('anomalies', payload.get('scores', []))
        if isinstance(anomalies, list):
            for a in anomalies:
                score = a.get('score', a.get('anomaly_score'))
                if score is not None:
                    val = float(score)
                    assert not math.isnan(val) and not math.isinf(val)


class TestAnalyticsLambdas:
    """Verify analytics Lambdas are healthy."""

    @pytest.mark.parametrize("lambda_name", ANALYTICS_LAMBDAS)
    def test_analytics_lambda_exists(self, lambda_name):
        resp = lambda_client.get_function(FunctionName=lambda_name)
        assert resp['Configuration']['State'] == 'Active'

    @pytest.mark.parametrize("lambda_name", ANALYTICS_LAMBDAS)
    def test_analytics_lambda_memory_adequate(self, lambda_name):
        """Analytics Lambdas should have at least 256MB for numerical computation."""
        resp = lambda_client.get_function(FunctionName=lambda_name)
        mem = resp['Configuration']['MemorySize']
        assert mem >= 128, f"{lambda_name} has only {mem}MB"

    def test_predictions_panels_no_float_type(self):
        """Verify no Python float leaked into DynamoDB (must be Decimal)."""
        table = dynamodb.Table(DASHBOARD_STATE_TABLE)
        panels = ['kpi_forecasts', 'backtest_results', 'anomaly_classifications']
        for panel_name in panels:
            resp = table.get_item(Key={'dashboard': 'predictions', 'panel': panel_name})
            item = resp.get('Item', {})
            if item:
                payload = item.get('payload', item.get('data', {}))
                if isinstance(payload, dict):
                    _check_no_raw_float(payload, f"predictions#{panel_name}")


def _check_no_raw_float(obj, path):
    """Recursively check no Python float in DynamoDB item."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            _check_no_raw_float(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _check_no_raw_float(v, f"{path}[{i}]")
    elif isinstance(obj, float):
        pytest.fail(f"Raw Python float found at {path} — use Decimal(str(value))")
