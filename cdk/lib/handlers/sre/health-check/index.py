"""
MVT SRE Health Check Endpoint (S4-02)
API Gateway endpoint that checks all system components and returns
a JSON health report with component statuses and overall health score.

Trigger: HTTP GET via API Gateway (or direct invocation)
Output: JSON with component statuses and overall health score
"""

import json
import os
import logging
from datetime import datetime, timedelta
from decimal import Decimal
import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb_client = boto3.client('dynamodb')
dynamodb_resource = boto3.resource('dynamodb')
lambda_client = boto3.client('lambda')
cloudwatch = boto3.client('cloudwatch')
apigateway = boto3.client('apigatewayv2')
cloudfront = boto3.client('cloudfront')
events = boto3.client('events')

DASHBOARD_STATE_TABLE = os.environ.get('DASHBOARD_STATE_TABLE', 'mvt-dashboard-state')
SIGNALS_TABLE = os.environ.get('SIGNALS_TABLE', 'mvt-signals')
CONNECTIONS_TABLE = os.environ.get('CONNECTIONS_TABLE', 'mvt-connections')
HISTORY_TABLE = os.environ.get('HISTORY_TABLE', 'mvt-history')
WEBSOCKET_API_ID = os.environ.get('WEBSOCKET_API_ID', '')
CLOUDFRONT_DISTRIBUTION_ID = os.environ.get('CLOUDFRONT_DISTRIBUTION_ID', 'E2JPNGN2TCNNV8')

# All MVT Lambda functions to check
MVT_FUNCTIONS = [
    'mvt-finnhub-connector', 'mvt-fred-poller', 'mvt-gdelt-querier',
    'mvt-trends-poller', 'mvt-worldbank-poller', 'mvt-yfinance-streamer',
    'mvt-sentiment-aggregator', 'mvt-inequality-scorer', 'mvt-contagion-modeler',
    'mvt-vulnerability-composite', 'mvt-cross-dashboard-router',
    'mvt-ws-connect', 'mvt-ws-disconnect', 'mvt-ws-broadcast',
    'mvt-telegram-bot', 'mvt-alert-manager',
    'mvt-cost-tracker', 'mvt-health-check'
]

DYNAMO_TABLES = [
    'mvt-signals', 'mvt-dashboard-state', 'mvt-connections', 'mvt-history'
]


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def check_dynamodb_tables():
    """Check DynamoDB table health."""
    results = {}
    for table_name in DYNAMO_TABLES:
        try:
            response = dynamodb_client.describe_table(TableName=table_name)
            table_info = response['Table']
            status = table_info['TableStatus']
            item_count = table_info.get('ItemCount', 0)

            results[table_name] = {
                'status': 'healthy' if status == 'ACTIVE' else 'degraded',
                'table_status': status,
                'item_count': item_count,
                'size_bytes': table_info.get('TableSizeBytes', 0)
            }
        except dynamodb_client.exceptions.ResourceNotFoundException:
            results[table_name] = {
                'status': 'missing',
                'table_status': 'NOT_FOUND',
                'item_count': 0
            }
        except Exception as e:
            results[table_name] = {
                'status': 'error',
                'error': str(e)
            }

    return results


def check_lambda_functions():
    """Check Lambda function health via recent CloudWatch errors."""
    now = datetime.utcnow()
    start = now - timedelta(minutes=15)
    results = {}

    for fn_name in MVT_FUNCTIONS:
        try:
            # Check if function exists
            config = lambda_client.get_function_configuration(FunctionName=fn_name)
            fn_state = config.get('State', 'Unknown')
            last_modified = config.get('LastModified', '')

            # Check recent errors (last 15 min)
            error_response = cloudwatch.get_metric_data(
                MetricDataQueries=[
                    {
                        'Id': 'errors',
                        'MetricStat': {
                            'Metric': {
                                'Namespace': 'AWS/Lambda',
                                'MetricName': 'Errors',
                                'Dimensions': [{'Name': 'FunctionName', 'Value': fn_name}]
                            },
                            'Period': 900,
                            'Stat': 'Sum'
                        }
                    },
                    {
                        'Id': 'invocations',
                        'MetricStat': {
                            'Metric': {
                                'Namespace': 'AWS/Lambda',
                                'MetricName': 'Invocations',
                                'Dimensions': [{'Name': 'FunctionName', 'Value': fn_name}]
                            },
                            'Period': 900,
                            'Stat': 'Sum'
                        }
                    }
                ],
                StartTime=start,
                EndTime=now
            )

            recent_errors = 0
            recent_invocations = 0
            for result in error_response.get('MetricDataResults', []):
                if result['Id'] == 'errors':
                    recent_errors = sum(result.get('Values', []))
                elif result['Id'] == 'invocations':
                    recent_invocations = sum(result.get('Values', []))

            error_rate = (recent_errors / recent_invocations * 100) if recent_invocations > 0 else 0

            if fn_state != 'Active':
                status = 'degraded'
            elif error_rate > 20:
                status = 'critical'
            elif error_rate > 5:
                status = 'warning'
            else:
                status = 'healthy'

            results[fn_name] = {
                'status': status,
                'state': fn_state,
                'recent_errors': int(recent_errors),
                'recent_invocations': int(recent_invocations),
                'error_rate': round(error_rate, 2),
                'last_modified': last_modified
            }

        except lambda_client.exceptions.ResourceNotFoundException:
            results[fn_name] = {
                'status': 'missing',
                'state': 'NOT_FOUND',
                'recent_errors': 0,
                'recent_invocations': 0,
                'error_rate': 0
            }
        except Exception as e:
            results[fn_name] = {
                'status': 'error',
                'error': str(e)
            }

    return results


def check_websocket_api():
    """Check WebSocket API Gateway health."""
    try:
        if not WEBSOCKET_API_ID:
            return {'status': 'unconfigured', 'message': 'No WebSocket API ID configured'}

        response = apigateway.get_api(ApiId=WEBSOCKET_API_ID)
        protocol = response.get('ProtocolType', '')

        # Check active connections
        try:
            connections_table = dynamodb_resource.Table(CONNECTIONS_TABLE)
            scan = connections_table.scan(Select='COUNT')
            active_connections = scan.get('Count', 0)
        except Exception:
            active_connections = -1

        return {
            'status': 'healthy',
            'protocol': protocol,
            'endpoint': response.get('ApiEndpoint', ''),
            'active_connections': active_connections
        }
    except Exception as e:
        return {'status': 'error', 'error': str(e)}


def check_cloudfront():
    """Check CloudFront distribution health."""
    try:
        response = cloudfront.get_distribution(Id=CLOUDFRONT_DISTRIBUTION_ID)
        dist = response['Distribution']
        dist_config = dist['DistributionConfig']

        return {
            'status': 'healthy' if dist['Status'] == 'Deployed' else 'degraded',
            'distribution_status': dist['Status'],
            'domain_name': dist['DomainName'],
            'enabled': dist_config.get('Enabled', False)
        }
    except Exception as e:
        return {'status': 'error', 'error': str(e)}


def check_eventbridge_rules():
    """Check EventBridge schedule rules."""
    try:
        response = events.list_rules(NamePrefix='mvt-')
        rules = response.get('Rules', [])

        rule_statuses = {}
        enabled_count = 0
        for rule in rules:
            name = rule['Name']
            state = rule['State']
            rule_statuses[name] = state
            if state == 'ENABLED':
                enabled_count += 1

        return {
            'status': 'healthy' if enabled_count > 0 else 'warning',
            'total_rules': len(rules),
            'enabled_rules': enabled_count,
            'rules': rule_statuses
        }
    except Exception as e:
        return {'status': 'error', 'error': str(e)}


def check_data_freshness():
    """Check if data is flowing by looking at recent dashboard-state updates."""
    try:
        table = dynamodb_resource.Table(DASHBOARD_STATE_TABLE)

        # Check key metrics for freshness
        freshness = {}
        key_metrics = [
            {'dashboard': 'composite', 'panel': 'vulnerability_score'},
            {'dashboard': 'sentiment_seismic', 'panel': 'trigger_probability'},
            {'dashboard': 'sovereign_dominoes', 'panel': 'risk_scores'},
            {'dashboard': 'inequality_pulse', 'panel': 'composite_score'}
        ]

        now = datetime.utcnow()
        stale_count = 0

        for key in key_metrics:
            response = table.get_item(Key=key)
            if 'Item' in response:
                item = response['Item']
                ts = item.get('timestamp', item.get('updated_at', ''))
                if ts:
                    try:
                        last_update = datetime.fromisoformat(ts.replace('Z', '+00:00').replace('+00:00', ''))
                        age_minutes = (now - last_update).total_seconds() / 60
                        is_stale = age_minutes > 30  # Stale if not updated in 30 min
                        if is_stale:
                            stale_count += 1
                        freshness[f"{key['dashboard']}/{key['panel']}"] = {
                            'last_update': ts,
                            'age_minutes': round(age_minutes, 1),
                            'stale': is_stale
                        }
                    except (ValueError, TypeError):
                        freshness[f"{key['dashboard']}/{key['panel']}"] = {
                            'last_update': ts,
                            'age_minutes': -1,
                            'stale': True
                        }
                        stale_count += 1
            else:
                freshness[f"{key['dashboard']}/{key['panel']}"] = {
                    'last_update': None,
                    'age_minutes': -1,
                    'stale': True
                }
                stale_count += 1

        status = 'critical' if stale_count >= 3 else ('warning' if stale_count >= 1 else 'healthy')

        return {
            'status': status,
            'metrics_checked': len(key_metrics),
            'stale_count': stale_count,
            'freshness': freshness
        }
    except Exception as e:
        return {'status': 'error', 'error': str(e)}


def compute_health_score(components):
    """
    Compute overall health score from 0-100.

    Weights:
    - Lambda functions: 30%
    - DynamoDB tables: 25%
    - Data freshness: 25%
    - WebSocket: 10%
    - CloudFront: 10%
    """
    scores = {}

    # Lambda health (percentage of healthy functions)
    lambdas = components.get('lambda_functions', {})
    if lambdas:
        healthy = sum(1 for v in lambdas.values() if isinstance(v, dict) and v.get('status') == 'healthy')
        total = len(lambdas)
        scores['lambda'] = (healthy / total * 100) if total > 0 else 0
    else:
        scores['lambda'] = 0

    # DynamoDB health
    tables = components.get('dynamodb_tables', {})
    if tables:
        healthy = sum(1 for v in tables.values() if isinstance(v, dict) and v.get('status') == 'healthy')
        total = len(tables)
        scores['dynamodb'] = (healthy / total * 100) if total > 0 else 0
    else:
        scores['dynamodb'] = 0

    # Data freshness
    freshness = components.get('data_freshness', {})
    freshness_status = freshness.get('status', 'error')
    scores['freshness'] = {'healthy': 100, 'warning': 60, 'critical': 20, 'error': 0}.get(freshness_status, 0)

    # WebSocket
    ws = components.get('websocket_api', {})
    ws_status = ws.get('status', 'error')
    scores['websocket'] = {'healthy': 100, 'degraded': 50, 'error': 0, 'unconfigured': 50}.get(ws_status, 0)

    # CloudFront
    cf = components.get('cloudfront', {})
    cf_status = cf.get('status', 'error')
    scores['cloudfront'] = {'healthy': 100, 'degraded': 50, 'error': 0}.get(cf_status, 0)

    # Weighted average
    overall = (
        scores['lambda'] * 0.30 +
        scores['dynamodb'] * 0.25 +
        scores['freshness'] * 0.25 +
        scores['websocket'] * 0.10 +
        scores['cloudfront'] * 0.10
    )

    return round(overall, 1), scores


def handler(event, context):
    """
    Main Lambda handler. Checks all system components and returns health report.
    Supports both HTTP (API Gateway) and direct invocation.
    """
    logger.info("Starting health check")

    start_time = datetime.utcnow()

    # Run all health checks
    components = {
        'dynamodb_tables': check_dynamodb_tables(),
        'lambda_functions': check_lambda_functions(),
        'websocket_api': check_websocket_api(),
        'cloudfront': check_cloudfront(),
        'eventbridge_rules': check_eventbridge_rules(),
        'data_freshness': check_data_freshness()
    }

    # Compute overall health score
    health_score, component_scores = compute_health_score(components)

    # Determine overall status
    if health_score >= 80:
        overall_status = 'healthy'
    elif health_score >= 60:
        overall_status = 'degraded'
    else:
        overall_status = 'critical'

    elapsed_ms = (datetime.utcnow() - start_time).total_seconds() * 1000

    report = {
        'status': overall_status,
        'health_score': health_score,
        'component_scores': component_scores,
        'components': components,
        'timestamp': start_time.isoformat(),
        'check_duration_ms': round(elapsed_ms, 1)
    }

    # Write health status to dashboard-state for frontend
    try:
        table = dynamodb_resource.Table(DASHBOARD_STATE_TABLE)
        table.put_item(Item={
            'dashboard': 'sre_observatory',
            'metric': 'health_status',
            'overall_status': overall_status,
            'health_score': Decimal(str(health_score)),
            'component_scores': json.loads(json.dumps(component_scores)),
            'timestamp': start_time.isoformat(),
            'updated_at': start_time.isoformat()
        })

        # Write per-component summary
        for comp_name, comp_data in components.items():
            comp_status = comp_data.get('status', 'unknown') if isinstance(comp_data, dict) else 'unknown'
            # For lambda_functions and dynamodb_tables, compute aggregate status
            if comp_name in ('lambda_functions', 'dynamodb_tables') and isinstance(comp_data, dict):
                statuses = [v.get('status', 'unknown') for v in comp_data.values() if isinstance(v, dict)]
                if 'critical' in statuses:
                    comp_status = 'critical'
                elif 'error' in statuses or 'missing' in statuses:
                    comp_status = 'warning'
                elif 'degraded' in statuses or 'warning' in statuses:
                    comp_status = 'warning'
                else:
                    comp_status = 'healthy'

            table.put_item(Item={
                'dashboard': 'sre_observatory',
                'metric': f'health_{comp_name}',
                'status': comp_status,
                'score': Decimal(str(component_scores.get(comp_name.split('_')[0], 0))),
                'timestamp': start_time.isoformat(),
                'updated_at': start_time.isoformat()
            })

    except Exception as e:
        logger.error(f"Failed to write health status to dashboard-state: {e}")

    logger.info(f"Health check complete: {overall_status} (score: {health_score})")

    # Return HTTP response format for API Gateway
    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*'
        },
        'body': json.dumps(report, cls=DecimalEncoder)
    }
