"""
Unit tests for Sprint 6 Analytics Lambda handlers.
Tests: distress-predictor, backtester, anomaly-classifier, history-api

Run: python -m pytest tests/unit/test_analytics_lambdas.py -v
"""

import os
import sys
import json
import math
import importlib.util
import unittest

# Set AWS environment for boto3 module-level initialization
os.environ.setdefault('AWS_DEFAULT_REGION', 'us-east-1')
os.environ.setdefault('AWS_ACCESS_KEY_ID', 'testing')
os.environ.setdefault('AWS_SECRET_ACCESS_KEY', 'testing')

ANALYTICS_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'cdk', 'lib', 'handlers', 'analytics')


def load_module(name, subdir):
    """Load a handler module with a unique name to avoid caching conflicts."""
    path = os.path.join(ANALYTICS_DIR, subdir, 'index.py')
    path = os.path.abspath(path)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Load all modules
predictor = load_module('analytics_predictor', 'distress-predictor')
backtester = load_module('analytics_backtester', 'backtester')
classifier = load_module('analytics_classifier', 'anomaly-classifier')
history_api = load_module('analytics_history_api', 'history-api')


# ========================
# Distress Predictor Tests
# ========================
class TestExponentialSmoothing(unittest.TestCase):
    """Tests for the exponential smoothing algorithm."""

    def test_basic_smoothing(self):
        values = [10, 12, 14, 16, 18, 20, 22, 24]
        result = predictor.exponential_smoothing(values)
        self.assertIsNotNone(result)
        self.assertIn('level', result)
        self.assertIn('trend', result)
        self.assertIn('std_error', result)
        self.assertGreater(result['level'], 0)
        self.assertGreater(result['trend'], 0)  # Upward trend

    def test_constant_series(self):
        values = [50, 50, 50, 50, 50, 50]
        result = predictor.exponential_smoothing(values)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result['trend'], 0, places=1)

    def test_too_few_values(self):
        result = predictor.exponential_smoothing([10, 20])
        self.assertIsNone(result)

    def test_single_value(self):
        result = predictor.exponential_smoothing([10])
        self.assertIsNone(result)

    def test_std_error_positive(self):
        values = [10, 15, 12, 18, 14, 20, 16, 22]
        result = predictor.exponential_smoothing(values)
        self.assertGreater(result['std_error'], 0)


class TestWeightedMovingAverage(unittest.TestCase):
    """Tests for WMA calculation."""

    def test_basic_wma(self):
        values = [10, 20, 30, 40, 50]
        wma = predictor.weighted_moving_average(values, window=5)
        self.assertIsNotNone(wma)
        # WMA should be closer to recent values (higher than simple average)
        simple_avg = sum(values) / len(values)
        self.assertGreater(wma, simple_avg)

    def test_single_value(self):
        wma = predictor.weighted_moving_average([42], window=5)
        self.assertAlmostEqual(wma, 42)

    def test_window_larger_than_data(self):
        values = [10, 20, 30]
        wma = predictor.weighted_moving_average(values, window=10)
        self.assertIsNotNone(wma)

    def test_empty_values(self):
        wma = predictor.weighted_moving_average([], window=5)
        self.assertIsNone(wma)


class TestForecastKPI(unittest.TestCase):
    """Tests for the KPI forecasting function."""

    def test_forecast_with_trend(self):
        values = [(f"2026-03-{i:02d}", 10 + i * 2) for i in range(1, 21)]
        result = predictor.forecast_kpi(values)
        self.assertIsNotNone(result)
        self.assertIn('7d', result)
        self.assertIn('14d', result)
        self.assertIn('30d', result)

    def test_forecast_contains_confidence_intervals(self):
        values = [(f"2026-03-{i:02d}", 50 + i) for i in range(1, 21)]
        result = predictor.forecast_kpi(values)
        for horizon in ['7d', '14d', '30d']:
            self.assertIn('point_estimate', result[horizon])
            self.assertIn('ci_80_lower', result[horizon])
            self.assertIn('ci_80_upper', result[horizon])
            self.assertIn('ci_95_lower', result[horizon])
            self.assertIn('ci_95_upper', result[horizon])

    def test_confidence_intervals_widen_with_horizon(self):
        values = [(f"2026-03-{i:02d}", 50 + i * 0.5 + (i % 3)) for i in range(1, 31)]
        result = predictor.forecast_kpi(values)
        # 30d CI should be wider than 7d CI
        ci_7d = result['7d']['ci_95_upper'] - result['7d']['ci_95_lower']
        ci_30d = result['30d']['ci_95_upper'] - result['30d']['ci_95_lower']
        self.assertGreater(ci_30d, ci_7d)

    def test_insufficient_data(self):
        values = [("2026-03-01", 10), ("2026-03-02", 20)]
        result = predictor.forecast_kpi(values)
        self.assertIsNone(result)

    def test_lower_bound_non_negative(self):
        values = [(f"2026-03-{i:02d}", 2 + i * 0.1) for i in range(1, 21)]
        result = predictor.forecast_kpi(values)
        for horizon in ['7d', '14d', '30d']:
            self.assertGreaterEqual(result[horizon]['ci_95_lower'], 0)


class TestTrendDirection(unittest.TestCase):
    """Tests for trend direction detection."""

    def test_rising_trend(self):
        values = [(str(i), i * 10) for i in range(20)]
        trend = predictor.compute_trend_direction(values)
        self.assertEqual(trend, 'rising')

    def test_falling_trend(self):
        values = [(str(i), 100 - i * 10) for i in range(20)]
        trend = predictor.compute_trend_direction(values)
        self.assertEqual(trend, 'falling')

    def test_stable(self):
        values = [(str(i), 50 + (i % 2)) for i in range(20)]
        trend = predictor.compute_trend_direction(values)
        self.assertEqual(trend, 'stable')

    def test_insufficient_data(self):
        values = [(str(i), i) for i in range(3)]
        trend = predictor.compute_trend_direction(values)
        self.assertEqual(trend, 'insufficient_data')


class TestPredictorConstants(unittest.TestCase):
    """Tests for predictor configuration."""

    def test_horizons(self):
        self.assertEqual(predictor.HORIZONS, [7, 14, 30])

    def test_forecast_kpis(self):
        self.assertEqual(len(predictor.FORECAST_KPIS), 4)
        self.assertIn('distress_index', predictor.FORECAST_KPIS)
        self.assertIn('vix_level', predictor.FORECAST_KPIS)

    def test_confidence_levels(self):
        self.assertIn('80', predictor.CONFIDENCE_LEVELS)
        self.assertIn('95', predictor.CONFIDENCE_LEVELS)


# ====================
# Backtester Tests
# ====================
class TestAccuracyMetrics(unittest.TestCase):
    """Tests for accuracy metric computation."""

    def test_perfect_predictions(self):
        pairs = [(10, 10), (20, 20), (30, 30)]
        result = backtester.compute_accuracy_metrics(pairs)
        self.assertEqual(result['mae'], 0)
        self.assertEqual(result['rmse'], 0)
        self.assertEqual(result['mape'], 0)

    def test_known_error(self):
        pairs = [(12, 10), (22, 20), (32, 30)]
        result = backtester.compute_accuracy_metrics(pairs)
        self.assertAlmostEqual(result['mae'], 2.0)

    def test_directional_accuracy(self):
        # All predictions go up, all actuals go up → 100% directional accuracy
        pairs = [(10, 8), (20, 18), (30, 28)]
        result = backtester.compute_accuracy_metrics(pairs)
        self.assertEqual(result['directional_accuracy_pct'], 100.0)

    def test_empty_pairs(self):
        result = backtester.compute_accuracy_metrics([])
        self.assertIsNone(result)

    def test_bias_detection(self):
        # Over-predicting
        pairs = [(15, 10), (25, 20), (35, 30)]
        result = backtester.compute_accuracy_metrics(pairs)
        self.assertEqual(result['bias'], 'over')

        # Under-predicting
        pairs = [(8, 10), (18, 20), (28, 30)]
        result = backtester.compute_accuracy_metrics(pairs)
        self.assertEqual(result['bias'], 'under')


class TestGradeAccuracy(unittest.TestCase):
    """Tests for letter grade assignment."""

    def test_grade_a(self):
        self.assertEqual(backtester.grade_accuracy({'mape': 3}), 'A')

    def test_grade_b(self):
        self.assertEqual(backtester.grade_accuracy({'mape': 8}), 'B')

    def test_grade_c(self):
        self.assertEqual(backtester.grade_accuracy({'mape': 15}), 'C')

    def test_grade_f(self):
        self.assertEqual(backtester.grade_accuracy({'mape': 50}), 'F')

    def test_grade_none(self):
        self.assertEqual(backtester.grade_accuracy(None), 'N/A')


class TestModelHealthScore(unittest.TestCase):
    """Tests for model health score computation."""

    def test_empty_results(self):
        score = backtester.compute_model_health_score({})
        self.assertEqual(score, 0)

    def test_perfect_model(self):
        results = {
            'distress_index': {
                'status': 'ok',
                'horizons': {
                    '7d': {'metrics': {'count': 10, 'mape': 0}},
                    '14d': {'metrics': {'count': 10, 'mape': 0}},
                    '30d': {'metrics': {'count': 10, 'mape': 0}},
                }
            }
        }
        score = backtester.compute_model_health_score(results)
        self.assertEqual(score, 100.0)


# ==========================
# Anomaly Classifier Tests
# ==========================
class TestComputeStatistics(unittest.TestCase):
    """Tests for statistics computation."""

    def test_basic_stats(self):
        values = [10, 20, 30, 40, 50]
        stats = classifier.compute_statistics(values)
        self.assertIsNotNone(stats)
        self.assertAlmostEqual(stats['mean'], 30)
        self.assertGreater(stats['std'], 0)

    def test_single_value(self):
        stats = classifier.compute_statistics([42])
        self.assertIsNone(stats)

    def test_empty(self):
        stats = classifier.compute_statistics([])
        self.assertIsNone(stats)


class TestClassifyAnomaly(unittest.TestCase):
    """Tests for the anomaly classification engine."""

    def test_structural_break(self):
        values = [10] * 20 + [50] * 20  # Clear level shift
        result = classifier.classify_anomaly(values)
        self.assertIn(result['type'], ['structural_break', 'regime_change'])
        self.assertGreater(result['confidence'], 0)

    def test_outlier(self):
        values = [50] * 29 + [200]  # One extreme outlier
        result = classifier.classify_anomaly(values)
        self.assertIn('type', result)
        self.assertIn('confidence', result)

    def test_stable_series(self):
        values = [50 + (i % 2) for i in range(30)]
        result = classifier.classify_anomaly(values)
        self.assertIn('type', result)
        # Low confidence expected for stable series
        self.assertLessEqual(result['confidence'], 0.5)

    def test_insufficient_data(self):
        result = classifier.classify_anomaly([1, 2, 3])
        self.assertEqual(result['type'], 'insufficient_data')

    def test_all_scores_present(self):
        values = [10 + i for i in range(30)]
        result = classifier.classify_anomaly(values)
        expected_types = ['structural_break', 'volatility_spike', 'mean_reversion', 'regime_change', 'outlier']
        for t in expected_types:
            self.assertIn(t, result['all_scores'])

    def test_metadata_included(self):
        values = [50] * 15 + [100] * 15
        result = classifier.classify_anomaly(values)
        if result['type'] != 'insufficient_data':
            self.assertIn('metadata', result)
            self.assertIn('statistics', result)


class TestAnomalyTypes(unittest.TestCase):
    """Tests for anomaly type definitions."""

    def test_all_types_defined(self):
        expected = ['structural_break', 'volatility_spike', 'mean_reversion', 'regime_change', 'outlier']
        for t in expected:
            self.assertIn(t, classifier.ANOMALY_TYPES)
            self.assertIn('label', classifier.ANOMALY_TYPES[t])
            self.assertIn('severity', classifier.ANOMALY_TYPES[t])
            self.assertIn('action', classifier.ANOMALY_TYPES[t])


# ====================
# History API Tests
# ====================
class TestDecimalEncoder(unittest.TestCase):
    """Tests for Decimal JSON serialization."""

    def test_decimal_float(self):
        from decimal import Decimal
        encoder = history_api.DecimalEncoder()
        result = json.loads(json.dumps({'val': Decimal('3.14')}, cls=history_api.DecimalEncoder))
        self.assertAlmostEqual(result['val'], 3.14)

    def test_decimal_int(self):
        from decimal import Decimal
        result = json.loads(json.dumps({'val': Decimal('42')}, cls=history_api.DecimalEncoder))
        self.assertEqual(result['val'], 42)


class TestParseDate(unittest.TestCase):
    """Tests for date parsing."""

    def test_iso_date(self):
        result = history_api.parse_date('2026-03-15')
        self.assertIsNotNone(result)
        self.assertEqual(result.day, 15)

    def test_iso_datetime(self):
        result = history_api.parse_date('2026-03-15T10:30:00')
        self.assertIsNotNone(result)

    def test_iso_datetime_z(self):
        result = history_api.parse_date('2026-03-15T10:30:00Z')
        self.assertIsNotNone(result)

    def test_invalid_date(self):
        result = history_api.parse_date('not-a-date')
        self.assertIsNone(result)

    def test_none_input(self):
        from datetime import datetime
        default = datetime(2026, 1, 1)
        result = history_api.parse_date(None, default)
        self.assertEqual(result, default)


class TestAggregateValues(unittest.TestCase):
    """Tests for data aggregation."""

    def test_no_aggregation(self):
        data = [{'timestamp': '2026-03-15T10:00:00Z', 'value': 42}]
        result = history_api.aggregate_values(data, 'none')
        self.assertEqual(len(result), 1)

    def test_daily_aggregation(self):
        data = [
            {'timestamp': '2026-03-15T10:00:00Z', 'value': 10},
            {'timestamp': '2026-03-15T14:00:00Z', 'value': 20},
            {'timestamp': '2026-03-16T10:00:00Z', 'value': 30},
        ]
        result = history_api.aggregate_values(data, 'daily')
        self.assertEqual(len(result), 2)
        # First day should have avg of 10 and 20
        self.assertAlmostEqual(result[0]['avg'], 15)
        self.assertEqual(result[0]['count'], 2)

    def test_weekly_aggregation(self):
        data = [
            {'timestamp': '2026-03-09T10:00:00Z', 'value': 10},  # Monday
            {'timestamp': '2026-03-10T10:00:00Z', 'value': 20},  # Tuesday
            {'timestamp': '2026-03-16T10:00:00Z', 'value': 30},  # Next Monday
        ]
        result = history_api.aggregate_values(data, 'weekly')
        self.assertEqual(len(result), 2)

    def test_empty_data(self):
        result = history_api.aggregate_values([], 'daily')
        self.assertEqual(len(result), 0)


class TestBuildResponse(unittest.TestCase):
    """Tests for API response building."""

    def test_success_response(self):
        resp = history_api.build_response(200, {'status': 'ok'})
        self.assertEqual(resp['statusCode'], 200)
        self.assertIn('Access-Control-Allow-Origin', resp['headers'])
        body = json.loads(resp['body'])
        self.assertEqual(body['status'], 'ok')

    def test_error_response(self):
        resp = history_api.build_response(400, {'error': 'bad request'})
        self.assertEqual(resp['statusCode'], 400)


class TestHistoryAPIValidation(unittest.TestCase):
    """Tests for API input validation."""

    def test_valid_kpis(self):
        self.assertIn('distress_index', history_api.VALID_KPIS)
        self.assertIn('vix_level', history_api.VALID_KPIS)
        self.assertEqual(len(history_api.VALID_KPIS), 12)

    def test_valid_aggregations(self):
        self.assertEqual(history_api.VALID_AGGREGATIONS, ['none', 'hourly', 'daily', 'weekly'])

    def test_signal_type_map(self):
        self.assertIn('distress_index', history_api.SIGNAL_TYPE_MAP)
        self.assertEqual(history_api.SIGNAL_TYPE_MAP['vix_level'], 'market_vix')

    def test_cors_preflight(self):
        event = {'httpMethod': 'OPTIONS'}
        result = history_api.handler(event, None)
        self.assertEqual(result['statusCode'], 200)

    def test_missing_kpi(self):
        event = {'pathParameters': {}, 'queryStringParameters': {}}
        result = history_api.handler(event, None)
        self.assertEqual(result['statusCode'], 400)
        body = json.loads(result['body'])
        self.assertIn('error', body)

    def test_invalid_kpi(self):
        event = {'pathParameters': {'kpi': 'nonexistent'}, 'queryStringParameters': {}}
        result = history_api.handler(event, None)
        self.assertEqual(result['statusCode'], 400)


if __name__ == '__main__':
    unittest.main()
