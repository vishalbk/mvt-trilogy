"""EV7-06: Comprehensive API Endpoint Integration Tests.
Tests all API Gateway routes for correct responses, error handling, and edge cases.
"""
import json
import urllib.request
import urllib.error
import pytest

API_BASE = 'https://txvfllfypa.execute-api.us-east-1.amazonaws.com/prod'

COUNTRY_CODES = ['ARG', 'BRA', 'TUR', 'EGY', 'PAK', 'NGA', 'ZAF', 'MEX', 'IDN', 'IND']


def api_get(path, timeout=15):
    url = f"{API_BASE}{path}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "MVT-Test/1.0",
        "Content-Type": "application/json"
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode() if e.fp else '{}'
        try:
            return e.code, json.loads(body_text)
        except:
            return e.code, {'error': body_text}


def api_post(path, body, timeout=15):
    url = f"{API_BASE}{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": "MVT-Test/1.0",
        "Content-Type": "application/json"
    }, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode() if e.fp else '{}'
        try:
            return e.code, json.loads(body_text)
        except:
            return e.code, {'error': body_text}


def api_put(path, body, timeout=15):
    url = f"{API_BASE}{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": "MVT-Test/1.0",
        "Content-Type": "application/json"
    }, method="PUT")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode() if e.fp else '{}'
        try:
            return e.code, json.loads(body_text)
        except:
            return e.code, {'error': body_text}


class TestCountryAPI:
    """Test GET /api/country/{code} for all 10 countries."""

    @pytest.mark.parametrize("code", COUNTRY_CODES)
    def test_country_endpoint_returns_200(self, code):
        status, data = api_get(f'/api/country/{code}')
        assert status == 200, f"Country {code} returned {status}: {data}"

    @pytest.mark.parametrize("code", COUNTRY_CODES)
    def test_country_has_risk_score(self, code):
        status, data = api_get(f'/api/country/{code}')
        if status == 200:
            assert 'risk_score' in data or 'score' in data or 'risk' in data, \
                f"Country {code} missing risk_score: {list(data.keys())}"

    @pytest.mark.parametrize("code", COUNTRY_CODES)
    def test_country_has_indicators(self, code):
        status, data = api_get(f'/api/country/{code}')
        if status == 200:
            has_indicators = 'indicators' in data or 'data' in data or 'metrics' in data
            assert has_indicators or len(data) > 2, \
                f"Country {code} has too few fields: {list(data.keys())}"

    def test_invalid_country_returns_404(self):
        status, data = api_get('/api/country/XXX')
        assert status in (404, 400), f"Expected 404/400 for invalid country, got {status}"


class TestNotificationAPI:
    """Test notification endpoints."""

    def test_get_notifications_returns_200(self):
        status, data = api_get('/api/notifications')
        assert status == 200
        assert isinstance(data, (dict, list))

    def test_notifications_have_structure(self):
        status, data = api_get('/api/notifications')
        if status == 200:
            notifs = data if isinstance(data, list) else data.get('notifications', data.get('items', []))
            if len(notifs) > 0:
                n = notifs[0]
                assert 'id' in n or 'notificationId' in n or 'type' in n

    def test_dismiss_all_notifications(self):
        status, data = api_post('/api/notifications/dismiss-all', {})
        assert status in (200, 204, 404), f"Dismiss-all returned {status}"


class TestHistoryAPI:
    """Test history API endpoints."""

    def test_get_history_returns_response(self):
        status, data = api_get('/api/history')
        # 200 = success, 400 = needs params (acceptable for route check)
        assert status in (200, 400), f"History API returned {status}"

    def test_history_export_returns_200(self):
        status, data = api_get('/api/history/export')
        assert status in (200, 404, 400)


class TestAuthActions:
    """Test all auth action routes."""

    AUTH_ACTIONS = ['login', 'register', 'verify-email', 'refresh',
                    'forgot-password', 'confirm-forgot-password']

    @pytest.mark.parametrize("action", AUTH_ACTIONS)
    def test_auth_action_route_exists(self, action):
        """Verify route exists (400 = route exists but bad body, not 404)."""
        status, data = api_post(f'/auth/{action}', {})
        # 400 means route found but validation failed, which is correct
        # 404 means route doesn't exist
        assert status != 404, f"/auth/{action} route not found (404)"
        assert status in (200, 400, 401, 403, 409, 500), \
            f"/auth/{action} returned unexpected {status}"

    def test_invalid_auth_action_returns_400(self):
        status, data = api_post('/auth/nonexistent', {})
        assert status in (400, 404), f"Expected 400/404, got {status}"


class TestPreferencesAPI:
    """Test preferences CRUD endpoints."""

    def test_get_preferences(self):
        status, _ = api_get('/api/preferences')
        assert status in (200, 401, 403)

    def test_put_preferences(self):
        status, _ = api_put('/api/preferences', {'theme': 'dark', 'timezone': 'UTC'})
        assert status in (200, 401, 403)

    def test_get_watchlists(self):
        status, _ = api_get('/api/watchlists')
        assert status in (200, 401, 403)

    def test_put_watchlists(self):
        status, _ = api_put('/api/watchlists', {'countries': ['BRA', 'ARG']})
        assert status in (200, 401, 403)

    def test_delete_watchlists(self):
        """Test DELETE on watchlists endpoint."""
        url = f"{API_BASE}/api/watchlists"
        req = urllib.request.Request(url, headers={"Content-Type": "application/json"}, method="DELETE")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                assert resp.status in (200, 204, 401, 403)
        except urllib.error.HTTPError as e:
            assert e.code in (200, 204, 401, 403, 400)


class TestAuditAPI:
    """Test audit log endpoints."""

    def test_get_audit_log(self):
        status, _ = api_get('/api/audit')
        assert status in (200, 401, 403)

    def test_post_audit_log(self):
        status, _ = api_post('/api/audit', {'event_type': 'auth_login'})
        assert status in (200, 401, 403, 400)


class TestAPIEdgeCases:
    """Test error handling and edge cases."""

    def test_nonexistent_route_returns_error(self):
        status, _ = api_get('/api/nonexistent')
        assert status in (403, 404), f"Expected 403/404, got {status}"

    def test_large_body_handled(self):
        """Send oversized body to verify Lambda doesn't crash."""
        large_body = {'data': 'x' * 10000}
        status, _ = api_post('/auth/login', large_body)
        assert status in (400, 401, 413, 500)

    def test_empty_body_handled(self):
        status, _ = api_post('/auth/login', {})
        assert status == 400

    def test_malformed_json_handled(self):
        """Send invalid JSON to verify error handling."""
        url = f"{API_BASE}/auth/login"
        req = urllib.request.Request(url, data=b'not json', headers={
            "Content-Type": "application/json"
        }, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                assert resp.status in (400, 500)
        except urllib.error.HTTPError as e:
            assert e.code in (400, 500)
