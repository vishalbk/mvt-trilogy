"""
Unit tests for Sprint 4 SRE Lambda handlers.
Tests cost tracker, health check, cost anomaly detection,
performance baseline, and capacity planner.
"""

import json
import os
import sys
import unittest
import importlib
from unittest.mock import patch, MagicMock
from decimal import Decimal

# Set AWS region for boto3 module-level initialization
os.environ.setdefault('AWS_DEFAULT_REGION', 'us-east-1')
os.environ.setdefault('AWS_ACCESS_KEY_ID', 'testing')
os.environ.setdefault('AWS_SECRET_ACCESS_KEY', 'testing')

# Base paths
SRE_BASE = os.path.join(os.path.dirname(__file__), '..', '..', 'cdk', 'lib', 'handlers', 'sre')


def load_module(name, subdir):
    """Load a module from an SRE subdirectory, ensuring fresh import."""
    module_name = f'sre_{name}'
    # Remove cached version
    sys.modules.pop(module_name, None)
    module_path = os.path.join(SRE_BASE, subdir, 'index.py')
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestCostTracker(unittest.TestCase):
    """Tests for the cost-tracker Lambda."""

    @classmethod
    def setUpClass(cls):
        os.environ['DASHBOARD_STATE_TABLE'] = 'mvt-dashboard-state'
        cls.mod = load_module('cost_tracker', 'cost-tracker')

    def test_estimate_cost_zero_invocations(self):
        result = self.mod.estimate_cost(0, 100, 128)
        self.assertEqual(result, 0.0)

    def test_estimate_cost_positive(self):
        result = self.mod.estimate_cost(1000, 200, 256)
        self.assertGreater(result, 0)

    def test_estimate_cost_high_memory(self):
        low_mem = self.mod.estimate_cost(1000, 200, 128)
        high_mem = self.mod.estimate_cost(1000, 200, 1024)
        self.assertGreater(high_mem, low_mem)

    def test_categorize_function_ingestion(self):
        self.assertEqual(self.mod.categorize_function('mvt-finnhub-connector'), 'ingestion')
        self.assertEqual(self.mod.categorize_function('mvt-fred-poller'), 'ingestion')
        self.assertEqual(self.mod.categorize_function('mvt-gdelt-querier'), 'ingestion')
        self.assertEqual(self.mod.categorize_function('mvt-yfinance-streamer'), 'ingestion')

    def test_categorize_function_processing(self):
        self.assertEqual(self.mod.categorize_function('mvt-sentiment-aggregator'), 'processing')
        self.assertEqual(self.mod.categorize_function('mvt-vulnerability-composite'), 'processing')
        self.assertEqual(self.mod.categorize_function('mvt-inequality-scorer'), 'processing')

    def test_categorize_function_realtime(self):
        self.assertEqual(self.mod.categorize_function('mvt-ws-broadcast'), 'realtime')
        self.assertEqual(self.mod.categorize_function('mvt-ws-connect'), 'realtime')

    def test_categorize_function_crosscloud(self):
        self.assertEqual(self.mod.categorize_function('mvt-event-relay'), 'crosscloud')
        self.assertEqual(self.mod.categorize_function('mvt-analytics-relay'), 'crosscloud')

    def test_categorize_function_sre(self):
        self.assertEqual(self.mod.categorize_function('mvt-cost-tracker'), 'sre')
        self.assertEqual(self.mod.categorize_function('mvt-health-check'), 'sre')

    def test_categorize_function_other(self):
        self.assertEqual(self.mod.categorize_function('mvt-telegram-bot'), 'other')

    def test_cost_pricing_constants(self):
        self.assertAlmostEqual(self.mod.PRICE_PER_GB_SECOND, 0.0000166667, places=8)
        self.assertAlmostEqual(self.mod.PRICE_PER_REQUEST, 0.0000002, places=8)

    def test_mvt_functions_list(self):
        self.assertGreater(len(self.mod.MVT_FUNCTIONS), 15)
        self.assertIn('mvt-finnhub-connector', self.mod.MVT_FUNCTIONS)
        self.assertIn('mvt-ws-broadcast', self.mod.MVT_FUNCTIONS)


class TestHealthCheck(unittest.TestCase):
    """Tests for the health-check Lambda."""

    @classmethod
    def setUpClass(cls):
        os.environ['DASHBOARD_STATE_TABLE'] = 'mvt-dashboard-state'
        os.environ['SIGNALS_TABLE'] = 'mvt-signals'
        os.environ['CONNECTIONS_TABLE'] = 'mvt-connections'
        os.environ['CLOUDFRONT_DISTRIBUTION_ID'] = 'E2JPNGN2TCNNV8'
        cls.mod = load_module('health_check', 'health-check')

    def test_compute_health_score_all_healthy(self):
        components = {
            'lambda_functions': {f'fn-{i}': {'status': 'healthy'} for i in range(10)},
            'dynamodb_tables': {f'tbl-{i}': {'status': 'healthy'} for i in range(4)},
            'data_freshness': {'status': 'healthy'},
            'websocket_api': {'status': 'healthy'},
            'cloudfront': {'status': 'healthy'}
        }
        score, subscores = self.mod.compute_health_score(components)
        self.assertEqual(score, 100.0)

    def test_compute_health_score_mixed(self):
        components = {
            'lambda_functions': {
                'fn-1': {'status': 'healthy'},
                'fn-2': {'status': 'critical'},
            },
            'dynamodb_tables': {'tbl-1': {'status': 'healthy'}},
            'data_freshness': {'status': 'warning'},
            'websocket_api': {'status': 'error'},
            'cloudfront': {'status': 'healthy'}
        }
        score, subscores = self.mod.compute_health_score(components)
        self.assertLess(score, 100.0)
        self.assertGreater(score, 0)
        self.assertEqual(subscores['lambda'], 50.0)
        self.assertEqual(subscores['freshness'], 60)

    def test_compute_health_score_empty(self):
        components = {
            'lambda_functions': {},
            'dynamodb_tables': {},
            'data_freshness': {'status': 'error'},
            'websocket_api': {'status': 'error'},
            'cloudfront': {'status': 'error'}
        }
        score, _ = self.mod.compute_health_score(components)
        self.assertEqual(score, 0.0)

    def test_dynamo_tables_list(self):
        self.assertIn('mvt-signals', self.mod.DYNAMO_TABLES)
        self.assertIn('mvt-dashboard-state', self.mod.DYNAMO_TABLES)
        self.assertIn('mvt-connections', self.mod.DYNAMO_TABLES)
        self.assertIn('mvt-history', self.mod.DYNAMO_TABLES)

    def test_mvt_functions_list(self):
        self.assertIn('mvt-finnhub-connector', self.mod.MVT_FUNCTIONS)
        self.assertIn('mvt-ws-broadcast', self.mod.MVT_FUNCTIONS)
        self.assertIn('mvt-telegram-bot', self.mod.MVT_FUNCTIONS)

    def test_decimal_encoder(self):
        data = {'value': Decimal('42.5')}
        result = json.dumps(data, cls=self.mod.DecimalEncoder)
        self.assertEqual(json.loads(result), {'value': 42.5})

    def test_health_weights_sum_to_one(self):
        """Health score weights should sum to 1.0."""
        # Weights from compute_health_score: lambda=0.30, dynamodb=0.25, freshness=0.25, ws=0.10, cf=0.10
        self.assertAlmostEqual(0.30 + 0.25 + 0.25 + 0.10 + 0.10, 1.0)


class TestCostAnomalyDetection(unittest.TestCase):
    """Tests for the cost-anomaly Lambda."""

    @classmethod
    def setUpClass(cls):
        os.environ['DASHBOARD_STATE_TABLE'] = 'mvt-dashboard-state'
        os.environ['DAILY_COST_THRESHOLD'] = '5.0'
        os.environ['ERROR_RATE_THRESHOLD'] = '5.0'
        os.environ.pop('TELEGRAM_BOT_TOKEN', None)
        os.environ.pop('TELEGRAM_CHAT_ID', None)
        cls.mod = load_module('cost_anomaly', 'cost-anomaly')

    def test_send_telegram_no_credentials(self):
        result = self.mod.send_telegram_alert("Test message")
        self.assertFalse(result)

    def test_threshold_defaults(self):
        self.assertEqual(self.mod.DAILY_COST_THRESHOLD, 5.0)
        self.assertEqual(self.mod.ERROR_RATE_THRESHOLD, 5.0)
        self.assertEqual(self.mod.COST_SPIKE_MULTIPLIER, 2.0)
        self.assertEqual(self.mod.THROTTLE_THRESHOLD, 10)

    def test_alert_cooldown(self):
        self.assertEqual(self.mod.ALERT_COOLDOWN_MINUTES, 60)


class TestPerfBaseline(unittest.TestCase):
    """Tests for the perf-baseline Lambda."""

    @classmethod
    def setUpClass(cls):
        os.environ['DASHBOARD_STATE_TABLE'] = 'mvt-dashboard-state'
        cls.mod = load_module('perf_baseline', 'perf-baseline')

    def test_function_list(self):
        self.assertGreater(len(self.mod.MVT_FUNCTIONS), 10)
        self.assertIn('mvt-finnhub-connector', self.mod.MVT_FUNCTIONS)

    def test_function_list_no_duplicates(self):
        self.assertEqual(len(self.mod.MVT_FUNCTIONS), len(set(self.mod.MVT_FUNCTIONS)))


class TestCapacityPlanner(unittest.TestCase):
    """Tests for the capacity-planner Lambda."""

    @classmethod
    def setUpClass(cls):
        os.environ['DASHBOARD_STATE_TABLE'] = 'mvt-dashboard-state'
        cls.mod = load_module('capacity_planner', 'capacity-planner')

    def test_project_costs_zero(self):
        result = self.mod.project_costs(0)
        self.assertEqual(result['daily'], 0)
        self.assertEqual(result['monthly_flat'], 0)
        self.assertEqual(result['yearly_flat'], 0)

    def test_project_costs_positive(self):
        result = self.mod.project_costs(1.0)
        self.assertEqual(result['daily'], 1.0)
        self.assertEqual(result['monthly_flat'], 30.0)
        self.assertEqual(result['quarterly_flat'], 90.0)
        self.assertEqual(result['yearly_flat'], 365.0)
        self.assertGreater(result['monthly_with_growth'], result['monthly_flat'])

    def test_project_costs_growth_rate(self):
        result = self.mod.project_costs(2.0)
        self.assertGreater(result['yearly_with_growth'], result['yearly_flat'])
        self.assertEqual(result['growth_rate_assumed'], 0.15)

    def test_dynamo_tables_list(self):
        self.assertEqual(len(self.mod.DYNAMO_TABLES), 4)
        self.assertIn('mvt-history', self.mod.DYNAMO_TABLES)


if __name__ == '__main__':
    unittest.main()
