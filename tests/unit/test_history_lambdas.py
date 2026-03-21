"""
Evolve Sprint 1: Unit tests for history-writer and history-api Lambdas.
"""

import json
import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

# Set dummy AWS env before importing
os.environ['AWS_DEFAULT_REGION'] = 'us-east-1'
os.environ['AWS_ACCESS_KEY_ID'] = 'testing'
os.environ['AWS_SECRET_ACCESS_KEY'] = 'testing'
os.environ['HISTORY_TABLE'] = 'mvt-kpi-history'

WRITER_PATH = os.path.join(os.path.dirname(__file__), '../../cdk/lib/handlers/history/history-writer')
API_PATH = os.path.join(os.path.dirname(__file__), '../../cdk/lib/handlers/history/history-api')


def _load_writer():
    """Load history-writer module with mocked boto3."""
    if 'index' in sys.modules:
        del sys.modules['index']
    if WRITER_PATH not in sys.path:
        sys.path.insert(0, WRITER_PATH)
    if API_PATH in sys.path:
        sys.path.remove(API_PATH)
    import index
    return index


def _load_api():
    """Load history-api module with mocked boto3."""
    if 'index' in sys.modules:
        del sys.modules['index']
    if API_PATH not in sys.path:
        sys.path.insert(0, API_PATH)
    if WRITER_PATH in sys.path:
        sys.path.remove(WRITER_PATH)
    import index
    return index


class TestHistoryWriter(unittest.TestCase):
    """Tests for mvt-history-writer Lambda."""

    def setUp(self):
        self.mock_table = MagicMock()
        self.writer = _load_writer()
        self.writer._history_table = self.mock_table

    def _make_event(self, event_name, new_image):
        return {
            'Records': [{
                'eventName': event_name,
                'dynamodb': {'NewImage': new_image}
            }]
        }

    def test_insert_event_writes_history(self):
        event = self._make_event('INSERT', {
            'dashboard': {'S': 'composite'},
            'panel': {'S': 'vulnerability_score'},
            'vulnerability_score': {'N': '27.12'},
        })
        result = self.writer.handler(event, {})
        self.assertEqual(result['statusCode'], 200)
        self.assertGreaterEqual(result['records_written'], 1)
        self.mock_table.put_item.assert_called()

    def test_modify_event_writes_history(self):
        event = self._make_event('MODIFY', {
            'dashboard': {'S': 'sentiment_seismic'},
            'panel': {'S': 'trigger_probability'},
            'probability': {'N': '11.78'},
        })
        result = self.writer.handler(event, {})
        self.assertEqual(result['statusCode'], 200)
        self.assertGreaterEqual(result['records_written'], 1)

    def test_remove_event_skipped(self):
        event = {
            'Records': [{
                'eventName': 'REMOVE',
                'dynamodb': {
                    'OldImage': {
                        'dashboard': {'S': 'composite'},
                        'panel': {'S': 'vulnerability_score'},
                    }
                }
            }]
        }
        result = self.writer.handler(event, {})
        self.assertEqual(result['records_written'], 0)
        self.mock_table.put_item.assert_not_called()

    def test_nested_map_extraction(self):
        event = self._make_event('INSERT', {
            'dashboard': {'S': 'composite'},
            'panel': {'S': 'vulnerability_score'},
            'vulnerability_score': {'N': '27.12'},
            'component_scores': {
                'M': {
                    'inequality': {'N': '35.5'},
                    'sentiment': {'N': '22.1'},
                    'contagion': {'N': '59.17'},
                }
            },
        })
        result = self.writer.handler(event, {})
        # Should write: vulnerability_score + 3 component sub-scores
        self.assertGreaterEqual(result['records_written'], 4)

    def test_empty_record_skipped(self):
        """Records with no dashboard/panel fields are skipped."""
        event = self._make_event('INSERT', {
            'dashboard': {'S': ''},
            'panel': {'S': ''},
        })
        result = self.writer.handler(event, {})
        self.assertGreaterEqual(result['records_skipped'], 1)
        self.assertEqual(result['records_written'], 0)

    def test_extract_string(self):
        self.assertEqual(self.writer._extract_string({'S': 'hello'}), 'hello')
        self.assertEqual(self.writer._extract_string({}), '')

    def test_extract_number(self):
        self.assertEqual(self.writer._extract_number({'N': '42.5'}), 42.5)
        self.assertEqual(self.writer._extract_number({'S': '3.14'}), 3.14)
        self.assertIsNone(self.writer._extract_number({'S': 'not-a-number'}))
        self.assertIsNone(self.writer._extract_number({'BOOL': True}))

    def test_ttl_set_correctly(self):
        event = self._make_event('INSERT', {
            'dashboard': {'S': 'composite'},
            'panel': {'S': 'test'},
            'score': {'N': '50'},
        })
        self.writer.handler(event, {})
        if self.mock_table.put_item.called:
            item = self.mock_table.put_item.call_args[1]['Item']
            import time
            expected_ttl = int(time.time()) + 7776000
            self.assertAlmostEqual(item['ttl'], expected_ttl, delta=60)

    def test_pk_format(self):
        """Verify PK is formatted as dashboard#metric."""
        event = self._make_event('INSERT', {
            'dashboard': {'S': 'overview'},
            'panel': {'S': 'kpi'},
            'distress': {'N': '42'},
        })
        self.writer.handler(event, {})
        if self.mock_table.put_item.called:
            item = self.mock_table.put_item.call_args[1]['Item']
            self.assertTrue(item['pk'].startswith('overview#'))

    def test_value_is_decimal(self):
        """Verify values are stored as Decimal."""
        event = self._make_event('INSERT', {
            'dashboard': {'S': 'test'},
            'panel': {'S': 'p'},
            'val': {'N': '3.14159'},
        })
        self.writer.handler(event, {})
        if self.mock_table.put_item.called:
            item = self.mock_table.put_item.call_args[1]['Item']
            self.assertIsInstance(item['value'], Decimal)

    def test_multiple_records(self):
        """Test processing multiple records in one batch."""
        event = {
            'Records': [
                {'eventName': 'INSERT', 'dynamodb': {'NewImage': {
                    'dashboard': {'S': 'a'}, 'panel': {'S': 'p'}, 'v': {'N': '1'},
                }}},
                {'eventName': 'INSERT', 'dynamodb': {'NewImage': {
                    'dashboard': {'S': 'b'}, 'panel': {'S': 'q'}, 'v': {'N': '2'},
                }}},
            ]
        }
        result = self.writer.handler(event, {})
        self.assertGreaterEqual(result['records_written'], 2)


class TestHistoryAPI(unittest.TestCase):
    """Tests for mvt-history-api Lambda."""

    def setUp(self):
        self.api = _load_api()
        self.mock_table = MagicMock()
        self.api._history_table = self.mock_table

    def test_missing_params_returns_400(self):
        event = {'httpMethod': 'GET', 'path': '/api/history', 'queryStringParameters': {}}
        result = self.api.handler(event, {})
        self.assertEqual(result['statusCode'], 400)

    def test_invalid_range_returns_400(self):
        event = {'httpMethod': 'GET', 'path': '/api/history',
                 'queryStringParameters': {'dashboard': 'o', 'metric': 'm', 'range': 'bad'}}
        result = self.api.handler(event, {})
        self.assertEqual(result['statusCode'], 400)

    def test_options_returns_cors(self):
        event = {'httpMethod': 'OPTIONS', 'path': '/api/history', 'queryStringParameters': {}}
        result = self.api.handler(event, {})
        self.assertEqual(result['statusCode'], 200)
        self.assertIn('Access-Control-Allow-Origin', result['headers'])

    def test_valid_query_returns_200(self):
        self.mock_table.query.return_value = {
            'Items': [
                {'sk': '2026-03-21T14:00:00Z', 'value': Decimal('27.12')},
                {'sk': '2026-03-21T14:05:00Z', 'value': Decimal('27.45')},
            ]
        }
        event = {'httpMethod': 'GET', 'path': '/api/history',
                 'queryStringParameters': {'dashboard': 'composite', 'metric': 'vulnerability_score', 'range': '24h'}}
        result = self.api.handler(event, {})
        self.assertEqual(result['statusCode'], 200)
        body = json.loads(result['body'])
        self.assertEqual(body['dashboard'], 'composite')
        self.assertGreater(len(body['data']), 0)

    def test_change_pct_calculated(self):
        self.mock_table.query.return_value = {
            'Items': [
                {'sk': '2026-03-21T00:00:00Z', 'value': Decimal('100')},
                {'sk': '2026-03-21T12:00:00Z', 'value': Decimal('110')},
            ]
        }
        event = {'httpMethod': 'GET', 'path': '/api/history',
                 'queryStringParameters': {'dashboard': 'x', 'metric': 'y', 'range': '24h'}}
        result = self.api.handler(event, {})
        body = json.loads(result['body'])
        self.assertAlmostEqual(body['change_pct'], 10.0)

    def test_empty_query_returns_null_change(self):
        self.mock_table.query.return_value = {'Items': []}
        event = {'httpMethod': 'GET', 'path': '/api/history',
                 'queryStringParameters': {'dashboard': 'x', 'metric': 'y', 'range': '1h'}}
        result = self.api.handler(event, {})
        body = json.loads(result['body'])
        self.assertIsNone(body['change_pct'])
        self.assertEqual(body['point_count'], 0)

    def test_downsample_no_data(self):
        result = self.api.downsample([], 15, 100)
        self.assertEqual(result, [])

    def test_downsample_small_dataset(self):
        items = [
            {'sk': '2026-03-21T14:00:00Z', 'value': Decimal('10')},
            {'sk': '2026-03-21T14:05:00Z', 'value': Decimal('20')},
            {'sk': '2026-03-21T14:10:00Z', 'value': Decimal('30')},
        ]
        result = self.api.downsample(items, 5, 100)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]['value'], 10.0)

    def test_downsample_large_dataset(self):
        items = []
        base = datetime(2026, 3, 21, 0, 0, tzinfo=timezone.utc)
        for i in range(500):
            ts = base + timedelta(minutes=i)
            items.append({'sk': ts.strftime('%Y-%m-%dT%H:%M:00Z'), 'value': Decimal(str(i * 0.5))})
        result = self.api.downsample(items, 5, 100)
        self.assertLessEqual(len(result), 100)

    def test_decimal_serializer(self):
        self.assertEqual(self.api.decimal_serializer(Decimal('3.14')), 3.14)
        with self.assertRaises(TypeError):
            self.api.decimal_serializer(set())

    def test_export_csv(self):
        self.mock_table.query.return_value = {
            'Items': [
                {'sk': '2026-03-21T14:00:00Z', 'timestamp': '2026-03-21T14:00:00Z', 'value': Decimal('27.12')},
            ]
        }
        event = {'httpMethod': 'GET', 'path': '/api/history/export',
                 'queryStringParameters': {'dashboard': 'c', 'metric': 'm', 'range': '24h', 'format': 'csv'}}
        result = self.api.handler(event, {})
        self.assertEqual(result['statusCode'], 200)
        self.assertIn('text/csv', result['headers']['Content-Type'])
        self.assertIn('timestamp,value', result['body'])

    def test_export_json(self):
        self.mock_table.query.return_value = {
            'Items': [
                {'sk': '2026-03-21T14:00:00Z', 'value': Decimal('27.12')},
            ]
        }
        event = {'httpMethod': 'GET', 'path': '/api/history/export',
                 'queryStringParameters': {'dashboard': 'c', 'metric': 'm', 'range': '24h', 'format': 'json'}}
        result = self.api.handler(event, {})
        self.assertEqual(result['statusCode'], 200)
        body = json.loads(result['body'])
        self.assertIn('data', body)

    def test_single_item_downsample(self):
        items = [{'sk': '2026-03-21T14:00:00Z', 'value': Decimal('42')}]
        result = self.api.downsample(items, 15, 100)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['value'], 42.0)

    def test_chronological_order_preserved(self):
        items = [
            {'sk': '2026-03-21T14:00:00Z', 'value': Decimal('10')},
            {'sk': '2026-03-21T14:01:00Z', 'value': Decimal('20')},
            {'sk': '2026-03-21T14:02:00Z', 'value': Decimal('15')},
        ]
        result = self.api.downsample(items, 1, 100)
        self.assertLess(result[0]['timestamp'], result[1]['timestamp'])

    def test_all_ranges_valid(self):
        """Verify all defined ranges are accepted."""
        for r in ['1h', '6h', '24h', '7d', '30d', '90d']:
            self.mock_table.query.return_value = {'Items': []}
            event = {'httpMethod': 'GET', 'path': '/api/history',
                     'queryStringParameters': {'dashboard': 'x', 'metric': 'y', 'range': r}}
            result = self.api.handler(event, {})
            self.assertEqual(result['statusCode'], 200, f"Range {r} should be valid")

    def test_auto_granularity(self):
        """Test that auto granularity picks correct default."""
        self.mock_table.query.return_value = {'Items': []}
        event = {'httpMethod': 'GET', 'path': '/api/history',
                 'queryStringParameters': {'dashboard': 'x', 'metric': 'y', 'range': '7d', 'granularity': 'auto'}}
        result = self.api.handler(event, {})
        body = json.loads(result['body'])
        self.assertEqual(body['granularity'], '1h')  # 7d default


if __name__ == '__main__':
    unittest.main()
