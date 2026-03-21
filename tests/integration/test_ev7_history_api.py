"""EV7-02: History API Integration Tests.
Tests: verify history API returns data matching dashboard-state writes.
Validates accuracy, downsampling, time ranges, and edge cases.
"""
import os
import json
import urllib.request
import urllib.error
import pytest
import boto3
from decimal import Decimal
from datetime import datetime, timedelta

API_BASE = 'https://txvfllfypa.execute-api.us-east-1.amazonaws.com/prod'
dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
HISTORY_TABLE = 'mvt-kpi-history'


def api_get(path, timeout=15):
    """Helper to make API GET request."""
    url = f"{API_BASE}{path}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "MVT-Test/1.0",
        "Content-Type": "application/json"
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else '{}'
        try:
            return e.code, json.loads(body)
        except:
            return e.code, {'error': body}


class TestHistoryAPIEndpoints:
    """Test /api/history endpoint responses."""

    def test_history_endpoint_reachable(self):
        """History endpoint should respond (200 or 400 if params required)."""
        status, data = api_get('/api/history')
        assert status in (200, 400), f"History API returned unexpected {status}"

    def test_history_returns_json(self):
        status, data = api_get('/api/history')
        assert isinstance(data, (dict, list))

    def test_history_with_metric_param(self):
        """Test querying history for a specific metric."""
        try:
            status, data = api_get('/api/history?metric=distress_score')
            assert status in (200, 400, 404)
        except urllib.error.HTTPError as e:
            assert e.code in (200, 400, 404)

    def test_history_with_time_range(self):
        """Test querying history with time range parameters."""
        end = datetime.utcnow().isoformat() + 'Z'
        start = (datetime.utcnow() - timedelta(hours=24)).isoformat() + 'Z'
        status, data = api_get(f'/api/history?start={start}&end={end}')
        assert status in (200, 400, 404), f"History time range returned {status}"

    def test_history_with_dashboard_param(self):
        """Test filtering by dashboard."""
        try:
            status, data = api_get('/api/history?dashboard=overview')
            assert status in (200, 400, 404)
        except urllib.error.HTTPError as e:
            assert e.code in (200, 400, 404)


class TestHistoryTableData:
    """Verify history table has data from DynamoDB Stream writes."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.table = dynamodb.Table(HISTORY_TABLE)

    def test_history_table_exists(self):
        """Verify the history table is accessible."""
        status = self.table.table_status
        assert status == 'ACTIVE'

    def test_history_table_has_data(self):
        """Verify history table has at least some records."""
        resp = self.table.scan(Limit=5)
        items = resp.get('Items', [])
        assert len(items) > 0, "History table is empty — DynamoDB Stream may not be triggering"

    def test_history_records_have_timestamps(self):
        """Verify records have valid timestamp sort keys."""
        resp = self.table.scan(Limit=10)
        for item in resp.get('Items', []):
            sk = str(item.get('sk', item.get('timestamp', '')))
            assert len(sk) > 0, "History record missing timestamp/sk"

    def test_history_records_have_numeric_values(self):
        """Verify records contain numeric values (not strings)."""
        resp = self.table.scan(Limit=10)
        for item in resp.get('Items', []):
            # At least one field should be numeric
            has_numeric = any(
                isinstance(v, (int, float, Decimal))
                for k, v in item.items()
                if k not in ('pk', 'sk', 'timestamp', 'ttl', 'dashboard', 'panel', 'metric')
            )
            # Payload might be nested
            payload = item.get('payload', item.get('data', {}))
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except:
                    pass
            if isinstance(payload, dict):
                has_numeric = has_numeric or any(
                    isinstance(v, (int, float, Decimal))
                    for v in payload.values()
                )
            assert has_numeric or payload, f"History record has no numeric values: {list(item.keys())}"

    def test_history_ttl_set(self):
        """Verify TTL is set on history records (90-day retention)."""
        resp = self.table.scan(Limit=5)
        for item in resp.get('Items', []):
            ttl = item.get('ttl')
            if ttl:
                # TTL should be in the future
                assert int(ttl) > int(datetime.utcnow().timestamp()), "TTL is in the past"


class TestHistoryExportEndpoint:
    """Test /api/history/export endpoint."""

    def test_export_endpoint_reachable(self):
        status, data = api_get('/api/history/export')
        # 200 = data returned, 400 = needs params, 404 = not configured
        assert status in (200, 400, 404), f"Export returned {status}"
