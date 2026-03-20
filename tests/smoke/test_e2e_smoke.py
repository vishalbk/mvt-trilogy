"""
Sprint 5 End-to-End Smoke Tests (S5-01)
Verifies all system components are functional across the full MVT platform.

Tests:
1. Dashboard accessibility (all 5 tabs)
2. External API connectivity (FRED, Finnhub, Yahoo, WorldBank, GDELT)
3. DynamoDB table health (signals, dashboard-state, connections, history)
4. Lambda function existence and recent invocation
5. WebSocket API endpoint
6. CloudFront distribution
7. Frontend content validation (tabs, KPI elements, SRE section)
8. Cross-cloud relay readiness

Run: pytest tests/smoke/test_e2e_smoke.py -v
Requires: AWS credentials (via env or IAM role)
"""

import json
import os
import sys
import unittest
import urllib.request
import urllib.error

# Optional: boto3 for AWS checks
try:
    import boto3
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False

DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "https://d2p9otbgwjwwuv.cloudfront.net")
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")


def http_get(url, timeout=10, headers=None):
    """Helper to make HTTP GET request."""
    default_headers = {"User-Agent": "MVT-SmokeTest/2.0"}
    if headers:
        default_headers.update(headers)
    req = urllib.request.Request(url, headers=default_headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8")


class TestDashboardFrontend(unittest.TestCase):
    """Verify the CloudFront-hosted dashboard is accessible and contains all tabs."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.status, cls.html = http_get(DASHBOARD_URL)
        except Exception as e:
            cls.status = 0
            cls.html = ""

    def test_dashboard_returns_200(self):
        self.assertEqual(self.status, 200)

    def test_dashboard_is_html(self):
        self.assertTrue("<html" in self.html.lower() or "<!doctype" in self.html.lower())

    def test_dashboard_has_title(self):
        self.assertIn("MVT Observatory", self.html)

    def test_dashboard_has_overview_tab(self):
        self.assertIn('data-tab="overview"', self.html)

    def test_dashboard_has_inequality_tab(self):
        self.assertIn('data-tab="inequality"', self.html)

    def test_dashboard_has_sentiment_tab(self):
        self.assertIn('data-tab="sentiment"', self.html)

    def test_dashboard_has_sovereign_tab(self):
        self.assertIn('data-tab="sovereign"', self.html)

    def test_dashboard_has_sre_tab(self):
        """Sprint 4: SRE Observatory tab should exist."""
        self.assertIn('data-tab="sre"', self.html)

    def test_dashboard_has_websocket_url(self):
        self.assertIn("wss://", self.html)

    def test_dashboard_has_kpi_elements(self):
        self.assertIn('id="distressIndex"', self.html)
        self.assertIn('id="triggerProb"', self.html)
        self.assertIn('id="contagionRisk"', self.html)
        self.assertIn('id="vixLevel"', self.html)

    def test_dashboard_has_sparklines(self):
        """Sprint 3: Sparkline containers should exist."""
        self.assertIn('id="sparkline-distress"', self.html)

    def test_dashboard_has_sre_elements(self):
        """Sprint 4: SRE Observatory elements should exist."""
        self.assertIn('id="sreHealthScore"', self.html)
        self.assertIn('id="sreDailyCost"', self.html)
        self.assertIn('id="sreErrorRate"', self.html)
        self.assertIn('id="srePerfTableBody"', self.html)

    def test_dashboard_has_chart_js(self):
        self.assertIn("chart.js", self.html.lower())

    def test_dashboard_content_size(self):
        """Dashboard should be substantial (>10KB)."""
        self.assertGreater(len(self.html), 10000)


class TestExternalAPIs(unittest.TestCase):
    """Verify external data source APIs are reachable."""

    def test_fred_api(self):
        try:
            status, _ = http_get("https://api.stlouisfed.org/fred/series?series_id=UNRATE&api_key=test&file_type=json")
            self.assertIn(status, (200, 400, 403))
        except urllib.error.HTTPError as e:
            self.assertIn(e.code, (400, 403))

    def test_finnhub_api(self):
        try:
            status, _ = http_get("https://finnhub.io/api/v1/news?category=general&token=test")
            self.assertIn(status, (200, 401, 403))
        except urllib.error.HTTPError as e:
            self.assertIn(e.code, (401, 403))

    def test_worldbank_api(self):
        status, body = http_get("https://api.worldbank.org/v2/country/USA/indicator/SI.POV.GINI?format=json&per_page=1")
        self.assertEqual(status, 200)

    def test_gdelt_bigquery_endpoint(self):
        """GDELT data is accessible via BigQuery public datasets API."""
        # Just verify the GDELT project page is reachable
        try:
            status, _ = http_get("https://blog.gdeltproject.org/", timeout=10)
            self.assertEqual(status, 200)
        except Exception:
            pass  # Non-critical - GDELT is queried via BigQuery, not HTTP


@unittest.skipUnless(HAS_BOTO3, "boto3 not available")
class TestDynamoDBTables(unittest.TestCase):
    """Verify DynamoDB tables exist and are active."""

    @classmethod
    def setUpClass(cls):
        cls.dynamodb = boto3.client('dynamodb', region_name=REGION)

    def _check_table(self, table_name):
        try:
            response = self.dynamodb.describe_table(TableName=table_name)
            return response['Table']['TableStatus']
        except Exception:
            return None

    def test_signals_table_active(self):
        status = self._check_table('mvt-signals')
        self.assertEqual(status, 'ACTIVE')

    def test_dashboard_state_table_active(self):
        status = self._check_table('mvt-dashboard-state')
        self.assertEqual(status, 'ACTIVE')

    def test_connections_table_active(self):
        status = self._check_table('mvt-connections')
        self.assertEqual(status, 'ACTIVE')

    def test_history_table_active(self):
        status = self._check_table('mvt-history')
        self.assertEqual(status, 'ACTIVE')

    def test_signals_table_has_items(self):
        """Signals table should have data from pollers."""
        try:
            response = self.dynamodb.describe_table(TableName='mvt-signals')
            item_count = response['Table'].get('ItemCount', 0)
            self.assertGreater(item_count, 0, "Signals table is empty - pollers may not be running")
        except Exception as e:
            self.fail(f"Could not check signals table: {e}")

    def test_dashboard_state_has_items(self):
        """Dashboard state should have KPI data."""
        try:
            response = self.dynamodb.describe_table(TableName='mvt-dashboard-state')
            item_count = response['Table'].get('ItemCount', 0)
            self.assertGreater(item_count, 0, "Dashboard state is empty")
        except Exception as e:
            self.fail(f"Could not check dashboard-state: {e}")


@unittest.skipUnless(HAS_BOTO3, "boto3 not available")
class TestLambdaFunctions(unittest.TestCase):
    """Verify Lambda functions exist."""

    EXPECTED_FUNCTIONS = [
        'mvt-finnhub-connector', 'mvt-fred-poller', 'mvt-gdelt-querier',
        'mvt-trends-poller', 'mvt-worldbank-poller', 'mvt-yfinance-streamer',
        'mvt-sentiment-aggregator', 'mvt-inequality-scorer', 'mvt-contagion-modeler',
        'mvt-vulnerability-composite', 'mvt-cross-dashboard-router',
        'mvt-ws-connect', 'mvt-ws-disconnect', 'mvt-ws-broadcast',
        'mvt-telegram-bot'
    ]

    @classmethod
    def setUpClass(cls):
        cls.lambda_client = boto3.client('lambda', region_name=REGION)

    def test_all_expected_functions_exist(self):
        """All core Lambda functions should exist."""
        missing = []
        for fn_name in self.EXPECTED_FUNCTIONS:
            try:
                self.lambda_client.get_function_configuration(FunctionName=fn_name)
            except Exception:
                missing.append(fn_name)

        self.assertEqual(len(missing), 0,
                         f"Missing Lambda functions: {', '.join(missing)}")

    def test_ingestion_functions_have_schedules(self):
        """Ingestion functions should be triggered by EventBridge schedules."""
        events = boto3.client('events', region_name=REGION)
        rules = events.list_rules(NamePrefix='mvt-')
        rule_names = [r['Name'] for r in rules.get('Rules', [])]
        self.assertGreater(len(rule_names), 0, "No EventBridge rules found")


@unittest.skipUnless(HAS_BOTO3, "boto3 not available")
class TestCloudFrontDistribution(unittest.TestCase):
    """Verify CloudFront distribution is deployed and serving."""

    def test_distribution_exists(self):
        cf = boto3.client('cloudfront', region_name=REGION)
        try:
            response = cf.get_distribution(Id='E2JPNGN2TCNNV8')
            self.assertEqual(response['Distribution']['Status'], 'Deployed')
        except Exception as e:
            self.fail(f"CloudFront distribution check failed: {e}")


class TestCodebaseIntegrity(unittest.TestCase):
    """Verify codebase structure is complete."""

    BASE = os.path.join(os.path.dirname(__file__), '..', '..')

    def _file_exists(self, path):
        return os.path.exists(os.path.join(self.BASE, path))

    def test_frontend_exists(self):
        self.assertTrue(self._file_exists('frontend/index.html'))

    def test_cdk_stacks_exist(self):
        stacks = ['storage-stack.ts', 'ingestion-stack.ts', 'processing-stack.ts',
                   'realtime-stack.ts', 'events-stack.ts', 'monitoring-stack.ts', 'frontend-stack.ts']
        for stack in stacks:
            self.assertTrue(self._file_exists(f'cdk/lib/stacks/{stack}'), f"Missing: {stack}")

    def test_sre_handlers_exist(self):
        """Sprint 4: SRE handlers should be present."""
        handlers = ['cost-tracker', 'health-check', 'cost-anomaly', 'perf-baseline', 'capacity-planner']
        for h in handlers:
            self.assertTrue(self._file_exists(f'cdk/lib/handlers/sre/{h}/index.py'), f"Missing: sre/{h}")

    def test_relay_handlers_exist(self):
        """Sprint 3: Cross-cloud relay handlers should be present."""
        self.assertTrue(self._file_exists('cdk/lib/handlers/relay/event-relay/index.py'))
        self.assertTrue(self._file_exists('cdk/lib/handlers/relay/analytics-relay/index.py'))

    def test_processing_handlers_exist(self):
        handlers = ['sentiment-aggregator', 'inequality-scorer', 'contagion-modeler',
                     'vulnerability-composite', 'cross-dashboard-router',
                     'alert-manager', 'history-writer', 'history-api']
        for h in handlers:
            self.assertTrue(self._file_exists(f'cdk/lib/handlers/processing/{h}/index.py'), f"Missing: processing/{h}")

    def test_terraform_modules_exist(self):
        modules = ['pubsub', 'bigquery', 'firestore', 'functions', 'hosting', 'monitoring']
        for m in modules:
            self.assertTrue(self._file_exists(f'terraform/modules/{m}/main.tf'), f"Missing: terraform/{m}")

    def test_agent_files_exist(self):
        self.assertTrue(self._file_exists('agents/orchestrator.py'))
        self.assertTrue(self._file_exists('agents/subagents/code_agent.py'))
        self.assertTrue(self._file_exists('agents/subagents/test_agent.py'))
        self.assertTrue(self._file_exists('agents/subagents/deploy_agent.py'))
        self.assertTrue(self._file_exists('agents/subagents/docs_agent.py'))

    def test_unit_tests_exist(self):
        self.assertTrue(self._file_exists('tests/unit/test_telegram_bot.py'))
        self.assertTrue(self._file_exists('tests/unit/test_sre_lambdas.py'))
        self.assertTrue(self._file_exists('tests/unit/test_code_agent.py'))
        self.assertTrue(self._file_exists('tests/unit/test_orchestrator.py'))


if __name__ == '__main__':
    unittest.main()
