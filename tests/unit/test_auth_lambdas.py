"""
Unit tests for Sprint 7 Auth Lambda handlers.
Tests: cognito-auth, user-preferences, alert-config, rbac, watchlists, audit-log

Run: python -m pytest tests/unit/test_auth_lambdas.py -v
"""

import os
import sys
import json
import time
import unittest
import importlib.util

# Set AWS environment for boto3 module-level initialization
os.environ.setdefault('AWS_DEFAULT_REGION', 'us-east-1')
os.environ.setdefault('AWS_ACCESS_KEY_ID', 'testing')
os.environ.setdefault('AWS_SECRET_ACCESS_KEY', 'testing')

AUTH_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'cdk', 'lib', 'handlers', 'auth')


def load_module(name, subdir):
    """Load a handler module with a unique name."""
    path = os.path.join(AUTH_DIR, subdir, 'index.py')
    path = os.path.abspath(path)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Load all modules
cognito_auth = load_module('auth_cognito', 'cognito-auth')
user_prefs = load_module('auth_prefs', 'user-preferences')
alert_config = load_module('auth_alert_config', 'alert-config')
rbac = load_module('auth_rbac', 'rbac')
watchlists = load_module('auth_watchlists', 'watchlists')
audit_log = load_module('auth_audit_log', 'audit-log')


# ==========================
# Cognito Auth Tests
# ==========================
class TestCognitoAuthRouting(unittest.TestCase):
    """Tests for auth action routing."""

    def test_valid_actions(self):
        expected = ['register', 'login', 'verify-email', 'refresh',
                     'forgot-password', 'confirm-forgot-password']
        for action in expected:
            self.assertIn(action, cognito_auth.ACTION_HANDLERS)

    def test_invalid_action(self):
        event = {
            'pathParameters': {'action': 'hack'},
            'body': '{}',
        }
        result = cognito_auth.handler(event, None)
        self.assertEqual(result['statusCode'], 400)

    def test_options_cors(self):
        event = {'httpMethod': 'OPTIONS'}
        result = cognito_auth.handler(event, None)
        self.assertEqual(result['statusCode'], 200)

    def test_register_missing_email(self):
        result = cognito_auth.handle_register({'password': 'test1234'})
        self.assertEqual(result['statusCode'], 400)

    def test_register_missing_password(self):
        result = cognito_auth.handle_register({'email': 'test@test.com'})
        self.assertEqual(result['statusCode'], 400)

    def test_register_short_password(self):
        result = cognito_auth.handle_register({'email': 'test@test.com', 'password': 'short'})
        self.assertEqual(result['statusCode'], 400)

    def test_login_missing_fields(self):
        result = cognito_auth.handle_login({})
        self.assertEqual(result['statusCode'], 400)

    def test_verify_missing_fields(self):
        result = cognito_auth.handle_verify_email({})
        self.assertEqual(result['statusCode'], 400)

    def test_forgot_password_missing_email(self):
        result = cognito_auth.handle_forgot_password({})
        self.assertEqual(result['statusCode'], 400)

    def test_confirm_forgot_missing_fields(self):
        result = cognito_auth.handle_confirm_forgot_password({})
        self.assertEqual(result['statusCode'], 400)

    def test_refresh_missing_token(self):
        result = cognito_auth.handle_refresh({})
        self.assertEqual(result['statusCode'], 400)


class TestSecretHash(unittest.TestCase):
    """Tests for Cognito secret hash computation."""

    def test_no_secret(self):
        # When CLIENT_SECRET is empty, should return None
        original = cognito_auth.CLIENT_SECRET
        cognito_auth.CLIENT_SECRET = ''
        result = cognito_auth.compute_secret_hash('test@test.com')
        self.assertIsNone(result)
        cognito_auth.CLIENT_SECRET = original

    def test_with_secret(self):
        original = cognito_auth.CLIENT_SECRET
        cognito_auth.CLIENT_SECRET = 'test-secret'
        result = cognito_auth.compute_secret_hash('test@test.com')
        self.assertIsNotNone(result)
        self.assertIsInstance(result, str)
        cognito_auth.CLIENT_SECRET = original


class TestBuildResponse(unittest.TestCase):
    """Tests for response builder."""

    def test_response_format(self):
        resp = cognito_auth.build_response(200, {'msg': 'ok'})
        self.assertEqual(resp['statusCode'], 200)
        self.assertIn('Access-Control-Allow-Origin', resp['headers'])
        body = json.loads(resp['body'])
        self.assertEqual(body['msg'], 'ok')


# ==========================
# User Preferences Tests
# ==========================
class TestDefaultPreferences(unittest.TestCase):
    """Tests for default preference structure."""

    def test_has_all_sections(self):
        prefs = user_prefs.DEFAULT_PREFERENCES
        self.assertIn('alerts', prefs)
        self.assertIn('dashboard', prefs)
        self.assertIn('notifications', prefs)
        self.assertIn('watchlists', prefs)

    def test_alert_defaults(self):
        alerts = user_prefs.DEFAULT_PREFERENCES['alerts']
        self.assertEqual(alerts['distress_index_threshold'], 70)
        self.assertTrue(alerts['enabled'])
        self.assertIn('dashboard', alerts['channels'])

    def test_dashboard_defaults(self):
        dash = user_prefs.DEFAULT_PREFERENCES['dashboard']
        self.assertEqual(dash['default_tab'], 'overview')
        self.assertEqual(dash['theme'], 'dark')


class TestSanitizePreferences(unittest.TestCase):
    """Tests for input sanitization."""

    def test_valid_threshold(self):
        result = user_prefs.sanitize_preferences({
            'alerts': {'distress_index_threshold': 80}
        })
        self.assertEqual(result['alerts']['distress_index_threshold'], 80)

    def test_invalid_threshold_too_high(self):
        result = user_prefs.sanitize_preferences({
            'alerts': {'distress_index_threshold': 200}
        })
        self.assertNotIn('distress_index_threshold', result.get('alerts', {}))

    def test_invalid_threshold_negative(self):
        result = user_prefs.sanitize_preferences({
            'alerts': {'distress_index_threshold': -10}
        })
        self.assertNotIn('distress_index_threshold', result.get('alerts', {}))

    def test_valid_tab(self):
        result = user_prefs.sanitize_preferences({
            'dashboard': {'default_tab': 'sre'}
        })
        self.assertEqual(result['dashboard']['default_tab'], 'sre')

    def test_invalid_tab(self):
        result = user_prefs.sanitize_preferences({
            'dashboard': {'default_tab': 'hacker_tab'}
        })
        self.assertNotIn('default_tab', result.get('dashboard', {}))

    def test_valid_channels(self):
        result = user_prefs.sanitize_preferences({
            'alerts': {'channels': ['dashboard', 'telegram']}
        })
        self.assertEqual(result['alerts']['channels'], ['dashboard', 'telegram'])

    def test_invalid_channels_filtered(self):
        result = user_prefs.sanitize_preferences({
            'alerts': {'channels': ['dashboard', 'sms', 'telegram']}
        })
        self.assertNotIn('sms', result['alerts']['channels'])

    def test_country_normalization(self):
        result = user_prefs.sanitize_preferences({
            'watchlists': {'countries': ['arg', 'bra', 'chl']}
        })
        self.assertEqual(result['watchlists']['countries'], ['ARG', 'BRA', 'CHL'])

    def test_country_limit(self):
        result = user_prefs.sanitize_preferences({
            'watchlists': {'countries': ['ARG'] * 100}
        })
        self.assertLessEqual(len(result['watchlists']['countries']), 50)


class TestDeepMerge(unittest.TestCase):
    """Tests for deep merge utility."""

    def test_basic_merge(self):
        base = {'a': 1, 'b': 2}
        override = {'b': 3, 'c': 4}
        result = user_prefs.deep_merge(base, override)
        self.assertEqual(result, {'a': 1, 'b': 3, 'c': 4})

    def test_nested_merge(self):
        base = {'a': {'x': 1, 'y': 2}}
        override = {'a': {'y': 3, 'z': 4}}
        result = user_prefs.deep_merge(base, override)
        self.assertEqual(result['a'], {'x': 1, 'y': 3, 'z': 4})

    def test_no_mutation(self):
        base = {'a': 1}
        override = {'b': 2}
        result = user_prefs.deep_merge(base, override)
        self.assertNotIn('b', base)


class TestPrefsHandler(unittest.TestCase):
    """Tests for handler routing."""

    def test_options(self):
        result = user_prefs.handler({'httpMethod': 'OPTIONS'}, None)
        self.assertEqual(result['statusCode'], 200)

    def test_invalid_method(self):
        result = user_prefs.handler({'httpMethod': 'DELETE'}, None)
        self.assertEqual(result['statusCode'], 405)


# ==========================
# Alert Config Tests
# ==========================
class TestQuietHours(unittest.TestCase):
    """Tests for quiet hours checking."""

    def test_disabled(self):
        result = alert_config.check_quiet_hours({'enabled': False})
        self.assertFalse(result)

    def test_none_config(self):
        result = alert_config.check_quiet_hours(None)
        self.assertFalse(result)

    def test_empty_config(self):
        result = alert_config.check_quiet_hours({})
        self.assertFalse(result)


class TestAlertEvaluation(unittest.TestCase):
    """Tests for per-user alert evaluation."""

    def test_alerts_disabled(self):
        user_prefs_data = {
            'user_id': 'test',
            'preferences': {'alerts': {'enabled': False}}
        }
        result = alert_config.evaluate_alerts_for_user(user_prefs_data, {'distress_index': 90})
        self.assertEqual(result, [])

    def test_below_threshold(self):
        user_prefs_data = {
            'user_id': 'test2',
            'preferences': {'alerts': {'enabled': True, 'distress_index_threshold': 80}}
        }
        result = alert_config.evaluate_alerts_for_user(user_prefs_data, {'distress_index': 50})
        self.assertEqual(result, [])

    def test_above_threshold(self):
        user_prefs_data = {
            'user_id': 'test3',
            'preferences': {'alerts': {'enabled': True, 'distress_index_threshold': 40}}
        }
        result = alert_config.evaluate_alerts_for_user(user_prefs_data, {'distress_index': 50})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['kpi'], 'distress_index')


class TestAlertFormatting(unittest.TestCase):
    """Tests for alert message formatting."""

    def test_format_message(self):
        alerts = [{'kpi': 'distress_index', 'value': 85.5, 'threshold': 70, 'severity': 'critical'}]
        msg = alert_config.format_alert_message(alerts, 'TestUser')
        self.assertIn('TestUser', msg)
        self.assertIn('Distress Index', msg)
        self.assertIn('85.5', msg)

    def test_severity_emoji(self):
        alerts_crit = [{'kpi': 'vix_level', 'value': 50, 'threshold': 30, 'severity': 'critical'}]
        msg = alert_config.format_alert_message(alerts_crit)
        self.assertIn('🔴', msg)


class TestDefaultThresholds(unittest.TestCase):
    """Tests for default threshold configuration."""

    def test_all_kpis_have_defaults(self):
        for kpi in ['distress_index', 'trigger_probability', 'contagion_risk', 'vix_level']:
            self.assertIn(kpi, alert_config.DEFAULT_THRESHOLDS)

    def test_kpi_threshold_map(self):
        for kpi in alert_config.DEFAULT_THRESHOLDS:
            self.assertIn(kpi, alert_config.KPI_THRESHOLD_MAP)


# ==========================
# RBAC Tests
# ==========================
class TestRoleHierarchy(unittest.TestCase):
    """Tests for role hierarchy."""

    def test_admin_highest(self):
        self.assertGreater(rbac.ROLE_HIERARCHY['admin'], rbac.ROLE_HIERARCHY['analyst'])
        self.assertGreater(rbac.ROLE_HIERARCHY['analyst'], rbac.ROLE_HIERARCHY['viewer'])

    def test_all_roles_defined(self):
        for role in ['viewer', 'analyst', 'admin']:
            self.assertIn(role, rbac.ROLE_HIERARCHY)


class TestFeatureVisibility(unittest.TestCase):
    """Tests for role-based feature visibility."""

    def test_viewer_limited_tabs(self):
        vis = rbac.FEATURE_VISIBILITY['viewer']
        self.assertNotIn('sre', vis['tabs'])
        self.assertNotIn('predictions', vis['tabs'])

    def test_analyst_has_sre(self):
        vis = rbac.FEATURE_VISIBILITY['analyst']
        self.assertIn('sre', vis['tabs'])
        self.assertIn('predictions', vis['tabs'])
        self.assertTrue(vis['can_export'])
        self.assertFalse(vis['can_manage_users'])

    def test_admin_full_access(self):
        vis = rbac.FEATURE_VISIBILITY['admin']
        self.assertTrue(vis['can_manage_users'])
        self.assertTrue(vis['can_export'])
        self.assertIn('manage_users', vis['features'])


class TestCheckPermission(unittest.TestCase):
    """Tests for endpoint permission checking."""

    def test_viewer_can_get_preferences(self):
        self.assertTrue(rbac.check_permission('viewer', 'GET', '/preferences'))

    def test_viewer_cannot_put_preferences(self):
        self.assertFalse(rbac.check_permission('viewer', 'PUT', '/preferences'))

    def test_analyst_can_put_preferences(self):
        self.assertTrue(rbac.check_permission('analyst', 'PUT', '/preferences'))

    def test_viewer_cannot_admin(self):
        self.assertFalse(rbac.check_permission('viewer', 'GET', '/admin/users'))

    def test_admin_can_admin(self):
        self.assertTrue(rbac.check_permission('admin', 'GET', '/admin/users'))


class TestExtractRole(unittest.TestCase):
    """Tests for role extraction from claims."""

    def test_valid_role(self):
        self.assertEqual(rbac.extract_role({'custom:role': 'admin'}), 'admin')

    def test_invalid_role_defaults(self):
        self.assertEqual(rbac.extract_role({'custom:role': 'hacker'}), 'viewer')

    def test_missing_role(self):
        self.assertEqual(rbac.extract_role({}), 'viewer')


class TestJWTDecode(unittest.TestCase):
    """Tests for JWT payload decoding."""

    def test_invalid_token(self):
        result = rbac.decode_jwt_payload('not.a.token')
        self.assertIsNone(result)

    def test_malformed_token(self):
        result = rbac.decode_jwt_payload('abc')
        self.assertIsNone(result)

    def test_expired_token(self):
        import base64
        payload = base64.urlsafe_b64encode(json.dumps({
            'sub': 'test', 'exp': 1000000, 'iss': 'test'
        }).encode()).decode().rstrip('=')
        header = base64.urlsafe_b64encode(b'{"alg":"RS256"}').decode().rstrip('=')
        token = f"{header}.{payload}.signature"
        result = rbac.decode_jwt_payload(token)
        self.assertIsNone(result)


# ==========================
# Watchlists Tests
# ==========================
class TestWatchlistConstants(unittest.TestCase):
    """Tests for watchlist configuration."""

    def test_available_countries(self):
        self.assertGreater(len(watchlists.AVAILABLE_COUNTRIES), 20)
        self.assertIn('ARG', watchlists.AVAILABLE_COUNTRIES)
        self.assertIn('BRA', watchlists.AVAILABLE_COUNTRIES)

    def test_available_sectors(self):
        self.assertEqual(len(watchlists.AVAILABLE_SECTORS), 11)
        self.assertIn('Technology', watchlists.AVAILABLE_SECTORS)

    def test_limits(self):
        self.assertEqual(watchlists.MAX_COUNTRIES, 50)
        self.assertEqual(watchlists.MAX_SECTORS, 20)
        self.assertEqual(watchlists.MAX_WATCHLISTS, 10)


class TestWatchlistHandler(unittest.TestCase):
    """Tests for watchlist handler routing."""

    def test_options(self):
        result = watchlists.handler({'httpMethod': 'OPTIONS'}, None)
        self.assertEqual(result['statusCode'], 200)

    def test_no_auth(self):
        result = watchlists.handler({
            'httpMethod': 'GET',
            'requestContext': {},
            'headers': {},
        }, None)
        self.assertEqual(result['statusCode'], 401)


# ==========================
# Audit Log Tests
# ==========================
class TestAuditLogConstants(unittest.TestCase):
    """Tests for audit log configuration."""

    def test_valid_event_types(self):
        self.assertIn('auth_login', audit_log.VALID_EVENT_TYPES)
        self.assertIn('admin_role_change', audit_log.VALID_EVENT_TYPES)
        self.assertGreater(len(audit_log.VALID_EVENT_TYPES), 10)

    def test_retention(self):
        self.assertEqual(audit_log.RETENTION_DAYS, 90)


class TestAuditLogHandler(unittest.TestCase):
    """Tests for audit log handler routing."""

    def test_options(self):
        result = audit_log.handler({'httpMethod': 'OPTIONS'}, None)
        self.assertEqual(result['statusCode'], 200)

    def test_invalid_method(self):
        result = audit_log.handler({'httpMethod': 'DELETE'}, None)
        self.assertEqual(result['statusCode'], 405)

    def test_get_requires_admin(self):
        event = {
            'httpMethod': 'GET',
            'queryStringParameters': {},
            'requestContext': {'authorizer': {'claims': {'custom:role': 'viewer'}}},
        }
        result = audit_log.handler(event, None)
        self.assertEqual(result['statusCode'], 403)

    def test_post_missing_fields(self):
        event = {
            'httpMethod': 'POST',
            'body': json.dumps({}),
        }
        result = audit_log.handler(event, None)
        self.assertEqual(result['statusCode'], 400)

    def test_post_invalid_event_type(self):
        event = {
            'httpMethod': 'POST',
            'body': json.dumps({'event_type': 'invalid', 'user_id': 'test'}),
        }
        result = audit_log.handler(event, None)
        self.assertEqual(result['statusCode'], 400)


class TestDecimalEncoder(unittest.TestCase):
    """Tests for Decimal JSON encoding."""

    def test_decimal_float(self):
        from decimal import Decimal
        result = json.loads(json.dumps({'v': Decimal('3.14')}, cls=audit_log.DecimalEncoder))
        self.assertAlmostEqual(result['v'], 3.14)

    def test_decimal_int(self):
        from decimal import Decimal
        result = json.loads(json.dumps({'v': Decimal('42')}, cls=audit_log.DecimalEncoder))
        self.assertEqual(result['v'], 42)


if __name__ == '__main__':
    unittest.main()
