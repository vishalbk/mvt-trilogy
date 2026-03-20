"""Sprint 1 Integration Smoke Tests.
Tests that deployed endpoints and APIs are reachable.
Run after staging deployment to verify connectivity.
"""
import os
import json
import urllib.request
import urllib.error
import pytest


DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "https://d2p9otbgwjwwuv.cloudfront.net")
WEBSOCKET_URL = os.environ.get("WEBSOCKET_URL", "wss://g91wyyvc0m.execute-api.us-east-1.amazonaws.com/prod")


def http_get(url, timeout=10):
    """Helper to make HTTP GET request."""
    req = urllib.request.Request(url, headers={"User-Agent": "MVT-SmokeTest/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8")


class TestDashboardReachable:
    def test_dashboard_returns_200(self):
        status, body = http_get(DASHBOARD_URL)
        assert status == 200
        assert len(body) > 0

    def test_dashboard_contains_html(self):
        status, body = http_get(DASHBOARD_URL)
        assert "<html" in body.lower() or "<!doctype" in body.lower()


class TestExternalAPIs:
    def test_fred_api_reachable(self):
        """Verify FRED API is accessible (public endpoint, no key needed for test)."""
        url = "https://api.stlouisfed.org/fred/series?series_id=UNRATE&api_key=TESTKEY&file_type=json"
        try:
            status, _ = http_get(url)
            # Even with invalid key, FRED returns 200 with error in body
            assert status == 200
        except urllib.error.HTTPError as e:
            # 400/403 means API is reachable but key issue — that's fine for smoke test
            assert e.code in (400, 403)

    def test_finnhub_api_reachable(self):
        """Verify Finnhub API is accessible."""
        url = "https://finnhub.io/api/v1/news?category=general&token=test"
        try:
            status, _ = http_get(url)
            assert status in (200, 401, 403)
        except urllib.error.HTTPError as e:
            assert e.code in (401, 403)

    def test_yahoo_finance_reachable(self):
        """Verify Yahoo Finance API is accessible."""
        url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?range=1d&interval=1m"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                assert resp.status == 200
        except urllib.error.HTTPError as e:
            # Yahoo sometimes returns 404 outside market hours — still reachable
            assert e.code in (200, 404, 429)

    def test_worldbank_api_reachable(self):
        """Verify World Bank API is accessible."""
        status, body = http_get("https://api.worldbank.org/v2/country/USA/indicator/SI.POV.GINI?format=json&per_page=1")
        assert status == 200
        data = json.loads(body)
        assert isinstance(data, list)


class TestAnthropicAPI:
    @pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="No API key")
    def test_anthropic_api_reachable(self):
        """Verify Anthropic API endpoint is accessible."""
        url = "https://api.anthropic.com/v1/messages"
        req = urllib.request.Request(url, headers={
            "x-api-key": "test",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }, data=b'{}', method="POST")
        try:
            urllib.request.urlopen(req, timeout=10)
        except urllib.error.HTTPError as e:
            # 401 = reachable but bad key, which is expected
            assert e.code in (400, 401, 403)
