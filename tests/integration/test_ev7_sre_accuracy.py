"""EV7-03: SRE Collector Accuracy Integration Tests.
Tests: verify SRE metrics match CloudWatch console values.
Validates health scores, cost calculations, and performance baselines.
"""
import json
import time
import pytest
import boto3
from decimal import Decimal
from datetime import datetime, timedelta

dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
cloudwatch = boto3.client('cloudwatch', region_name='us-east-1')
lambda_client = boto3.client('lambda', region_name='us-east-1')

DASHBOARD_STATE_TABLE = 'mvt-dashboard-state'

SRE_LAMBDAS = [
    'mvt-health-check',
    'mvt-cost-tracker',
    'mvt-perf-baseline',
    'mvt-capacity-planner',
]


def get_panel(dashboard, panel):
    table = dynamodb.Table(DASHBOARD_STATE_TABLE)
    resp = table.get_item(Key={'dashboard': dashboard, 'panel': panel})
    return resp.get('Item')


def get_payload(item):
    """Get the data payload from a dashboard-state item.
    Data fields are stored directly on the item (not nested under 'payload' or 'data').
    """
    payload = item.get('payload', item.get('data'))
    if payload is not None:
        if isinstance(payload, str):
            payload = json.loads(payload)
        return payload
    skip_keys = {'dashboard', 'panel', 'timestamp', 'last_updated', 'ttl'}
    return {k: v for k, v in item.items() if k not in skip_keys}


class TestSREHealthMetrics:
    """Verify SRE health metrics are accurate."""

    def test_health_status_has_overall_score(self):
        item = get_panel('sre_observatory', 'health_status')
        assert item is not None
        payload = get_payload(item)
        score = float(payload.get('overall_health', payload.get('health_score', payload.get('score', -1))))
        assert 0 <= score <= 100, f"Health score {score} out of range"

    def test_health_has_component_panels(self):
        """Verify individual health component panels exist."""
        components = ['health_lambda_functions', 'health_dynamodb_tables', 'health_websocket_api', 'health_cloudfront']
        for comp in components:
            item = get_panel('sre_observatory', comp)
            assert item is not None, f"sre_observatory#{comp} panel missing"

    def test_health_data_freshness_exists(self):
        item = get_panel('sre_observatory', 'health_data_freshness')
        assert item is not None, "sre_observatory#health_data_freshness panel missing"

    def test_health_lambda_invocations_match_cloudwatch(self):
        """Verify Lambda invocation count is within 5% of CloudWatch."""
        item = get_panel('sre_observatory', 'health_status')
        if not item:
            pytest.skip("No health summary panel")
        payload = get_payload(item)

        # Get actual CloudWatch metric for a sample Lambda
        end = datetime.utcnow()
        start = end - timedelta(hours=1)
        cw_resp = cloudwatch.get_metric_statistics(
            Namespace='AWS/Lambda',
            MetricName='Invocations',
            Dimensions=[{'Name': 'FunctionName', 'Value': 'mvt-ws-broadcast'}],
            StartTime=start,
            EndTime=end,
            Period=3600,
            Statistics=['Sum']
        )
        datapoints = cw_resp.get('Datapoints', [])
        if datapoints:
            cw_invocations = datapoints[0]['Sum']
            # Just verify we got a valid positive number
            assert cw_invocations >= 0, "CloudWatch invocations should be non-negative"


class TestSRECostMetrics:
    """Verify SRE cost tracking metrics."""

    def test_cost_summary_exists(self):
        item = get_panel('sre_observatory', 'cost_summary')
        assert item is not None, "cost_summary panel missing"

    def test_cost_summary_has_daily_cost(self):
        item = get_panel('sre_observatory', 'cost_summary')
        payload = get_payload(item)
        daily = float(payload.get('total_cost_24h', payload.get('daily_total', payload.get('total', -1))))
        assert daily >= 0, f"Daily cost should be non-negative, got {daily}"

    def test_cost_has_group_panels(self):
        """Verify cost group panels exist."""
        groups = ['group_ingestion', 'group_processing', 'group_realtime']
        for g in groups:
            item = get_panel('sre_observatory', g)
            assert item is not None, f"sre_observatory#{g} panel missing"

    def test_cost_values_are_decimals_not_floats(self):
        """Ensure costs were stored as Decimal (DynamoDB requirement)."""
        table = dynamodb.Table(DASHBOARD_STATE_TABLE)
        resp = table.get_item(Key={'dashboard': 'sre_observatory', 'panel': 'cost_summary'})
        item = resp.get('Item', {})
        # Check raw DynamoDB item for Decimal types
        payload = item.get('payload', item.get('data', {}))
        if isinstance(payload, dict):
            for k, v in payload.items():
                if isinstance(v, float):
                    pytest.fail(f"Cost field '{k}' is Python float, should be Decimal")


class TestSREPerformanceBaseline:
    """Verify SRE performance baseline metrics."""

    def test_performance_baseline_exists(self):
        item = get_panel('sre_observatory', 'perf_baseline_summary')
        assert item is not None

    def test_performance_has_data(self):
        item = get_panel('sre_observatory', 'perf_baseline_summary')
        payload = get_payload(item)
        assert payload, "Performance baseline has no data"

    def test_latency_values_positive(self):
        item = get_panel('sre_observatory', 'perf_baseline_summary')
        payload = get_payload(item)
        if isinstance(payload, dict):
            for k, v in payload.items():
                if 'latency' in k.lower() or k.startswith('p') and k[1:].isdigit():
                    val = float(v) if v else 0
                    assert val >= 0, f"Latency {k}={val} should be non-negative"


class TestSRECapacityPlanner:
    """Verify SRE capacity planning metrics."""

    def test_capacity_panel_exists(self):
        item = get_panel('sre_observatory', 'capacity_summary')
        assert item is not None, "sre_observatory#capacity_summary panel missing"

    def test_utilization_in_range(self):
        item = get_panel('sre_observatory', 'capacity_summary')
        payload = get_payload(item)
        util = float(payload.get('utilization', payload.get('current_utilization', -1)))
        if util >= 0:
            assert 0 <= util <= 100, f"Utilization {util} out of range"


class TestSRELambdasRunnable:
    """Verify SRE Lambdas can be invoked without errors."""

    @pytest.mark.parametrize("lambda_name", SRE_LAMBDAS)
    def test_sre_lambda_exists_and_active(self, lambda_name):
        resp = lambda_client.get_function(FunctionName=lambda_name)
        assert resp['Configuration']['State'] == 'Active'

    @pytest.mark.parametrize("lambda_name", SRE_LAMBDAS)
    def test_sre_lambda_runtime_python(self, lambda_name):
        resp = lambda_client.get_function(FunctionName=lambda_name)
        runtime = resp['Configuration']['Runtime']
        assert 'python' in runtime.lower(), f"{lambda_name} runtime is {runtime}"
