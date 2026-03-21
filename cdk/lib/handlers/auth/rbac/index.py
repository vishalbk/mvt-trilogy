"""
MVT Role-Based Access Control (S7-04)
API Gateway Lambda authorizer that validates JWT tokens and enforces
role-based permissions on API endpoints and dashboard features.

Roles: admin, analyst, viewer
Trigger: API Gateway Lambda Authorizer (TOKEN type)
Output: IAM policy allowing/denying access based on role
"""

import json
import os
import logging
import time
import urllib.request
from datetime import datetime
from decimal import Decimal
import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')

USER_POOL_ID = os.environ.get('USER_POOL_ID', '')
REGION = os.environ.get('AWS_REGION', 'us-east-1')
AUDIT_TABLE = os.environ.get('AUDIT_TABLE', 'mvt-audit-log')

# Role hierarchy: higher index = more permissions
ROLE_HIERARCHY = {
    'viewer': 0,
    'analyst': 1,
    'admin': 2,
}

# Endpoint permissions: minimum role required for each resource pattern
ENDPOINT_PERMISSIONS = {
    # Public endpoints (no auth required — handled outside this authorizer)
    # Auth endpoints
    'POST /auth/*': 'viewer',

    # Read-only endpoints (viewer+)
    'GET /preferences': 'viewer',
    'GET /history/*': 'viewer',
    'GET /health': 'viewer',
    'GET /dashboard/*': 'viewer',

    # Write endpoints (analyst+)
    'PUT /preferences': 'analyst',
    'POST /watchlists': 'analyst',
    'PUT /watchlists/*': 'analyst',
    'DELETE /watchlists/*': 'analyst',

    # Admin endpoints
    'GET /admin/*': 'admin',
    'PUT /admin/*': 'admin',
    'POST /admin/*': 'admin',
    'GET /audit/*': 'admin',
    'PUT /users/*/role': 'admin',
    'DELETE /users/*': 'admin',
}

# Feature visibility per role (sent to frontend)
FEATURE_VISIBILITY = {
    'viewer': {
        'tabs': ['overview', 'inequality', 'sentiment', 'sovereign'],
        'features': ['view_dashboard', 'view_signals'],
        'can_export': False,
        'can_configure_alerts': False,
        'can_manage_users': False,
    },
    'analyst': {
        'tabs': ['overview', 'inequality', 'sentiment', 'sovereign', 'sre', 'predictions'],
        'features': ['view_dashboard', 'view_signals', 'configure_alerts', 'export_data',
                      'view_predictions', 'view_sre', 'manage_watchlists'],
        'can_export': True,
        'can_configure_alerts': True,
        'can_manage_users': False,
    },
    'admin': {
        'tabs': ['overview', 'inequality', 'sentiment', 'sovereign', 'sre', 'predictions'],
        'features': ['view_dashboard', 'view_signals', 'configure_alerts', 'export_data',
                      'view_predictions', 'view_sre', 'manage_watchlists',
                      'manage_users', 'view_audit_log', 'system_config'],
        'can_export': True,
        'can_configure_alerts': True,
        'can_manage_users': True,
    },
}


# JWT validation (simplified — in production use python-jose or pyjwt with JWKS)
_jwks_cache = {}
_jwks_cache_time = 0
JWKS_CACHE_TTL = 3600  # 1 hour


def get_jwks():
    """Fetch Cognito JWKS (JSON Web Key Set) with caching."""
    global _jwks_cache, _jwks_cache_time

    if _jwks_cache and (time.time() - _jwks_cache_time) < JWKS_CACHE_TTL:
        return _jwks_cache

    try:
        url = f"https://cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}/.well-known/jwks.json"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            _jwks_cache = json.loads(resp.read())
            _jwks_cache_time = time.time()
            return _jwks_cache
    except Exception as e:
        logger.error(f"Failed to fetch JWKS: {e}")
        return _jwks_cache or {}


def decode_jwt_payload(token):
    """
    Decode JWT payload without full signature verification.
    NOTE: In production, use python-jose or aws-jwt-verify for proper validation.
    This is a simplified version that extracts claims from the token.
    """
    import base64

    try:
        parts = token.split('.')
        if len(parts) != 3:
            return None

        # Decode payload (part 2)
        payload_b64 = parts[1]
        # Add padding
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += '=' * padding

        payload_bytes = base64.urlsafe_b64decode(payload_b64)
        claims = json.loads(payload_bytes)

        # Basic validation
        now = int(time.time())

        # Check expiration
        if claims.get('exp', 0) < now:
            logger.warning("Token expired")
            return None

        # Check issuer
        expected_issuer = f"https://cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}"
        if claims.get('iss') != expected_issuer and USER_POOL_ID:
            logger.warning(f"Invalid issuer: {claims.get('iss')}")
            return None

        return claims

    except Exception as e:
        logger.error(f"JWT decode failed: {e}")
        return None


def extract_role(claims):
    """Extract user role from JWT claims."""
    role = claims.get('custom:role', 'viewer')
    if role not in ROLE_HIERARCHY:
        role = 'viewer'
    return role


def check_permission(role, method, resource):
    """Check if role has permission for the given endpoint."""
    endpoint_key = f"{method} {resource}"

    # Check exact match first
    if endpoint_key in ENDPOINT_PERMISSIONS:
        required_role = ENDPOINT_PERMISSIONS[endpoint_key]
        return ROLE_HIERARCHY.get(role, 0) >= ROLE_HIERARCHY.get(required_role, 0)

    # Check wildcard patterns
    for pattern, required_role in ENDPOINT_PERMISSIONS.items():
        pattern_method, pattern_path = pattern.split(' ', 1)
        if pattern_method != method and pattern_method != '*':
            continue

        if pattern_path.endswith('/*'):
            prefix = pattern_path[:-2]
            if resource.startswith(prefix):
                return ROLE_HIERARCHY.get(role, 0) >= ROLE_HIERARCHY.get(required_role, 0)

    # Default: allow for authenticated users
    return True


def generate_policy(principal_id, effect, resource, context=None):
    """Generate IAM policy for API Gateway authorizer."""
    policy = {
        'principalId': principal_id,
        'policyDocument': {
            'Version': '2012-10-17',
            'Statement': [{
                'Action': 'execute-api:Invoke',
                'Effect': effect,
                'Resource': resource,
            }],
        },
    }

    if context:
        policy['context'] = context

    return policy


def build_response(status_code, body):
    """Build standard API response."""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, Authorization',
        },
        'body': json.dumps(body, default=str),
    }


def handle_permissions_request(event):
    """Handle GET /permissions — return feature visibility for user role."""
    claims = event.get('requestContext', {}).get('authorizer', {}).get('claims', {})
    role = claims.get('custom:role', 'viewer')

    visibility = FEATURE_VISIBILITY.get(role, FEATURE_VISIBILITY['viewer'])

    return build_response(200, {
        'role': role,
        'visibility': visibility,
    })


def handler(event, context):
    """
    Main handler — supports both authorizer mode and permissions query mode.

    Authorizer mode: event contains 'authorizationToken'
    Permissions mode: event contains 'httpMethod' for GET /permissions
    """
    # Check if this is a permissions query
    method = event.get('httpMethod', '')
    if method:
        return handle_permissions_request(event)

    # Authorizer mode
    token = event.get('authorizationToken', '')
    method_arn = event.get('methodArn', '*')

    if not token:
        logger.warning("No authorization token provided")
        raise Exception('Unauthorized')

    # Strip 'Bearer ' prefix
    if token.startswith('Bearer '):
        token = token[7:]

    # Decode and validate JWT
    claims = decode_jwt_payload(token)
    if not claims:
        logger.warning("Invalid or expired token")
        raise Exception('Unauthorized')

    # Extract user info
    user_id = claims.get('sub', 'unknown')
    email = claims.get('email', '')
    role = extract_role(claims)

    # Parse method and resource from ARN
    # arn:aws:execute-api:region:account:api-id/stage/method/resource
    arn_parts = method_arn.split('/')
    http_method = arn_parts[2] if len(arn_parts) > 2 else '*'
    resource_path = '/' + '/'.join(arn_parts[3:]) if len(arn_parts) > 3 else '/*'

    # Check permission
    allowed = check_permission(role, http_method, resource_path)

    if allowed:
        effect = 'Allow'
        # Allow all methods on this API (token is valid for session)
        resource_arn = '/'.join(method_arn.split('/')[:2]) + '/*'
    else:
        effect = 'Deny'
        resource_arn = method_arn
        logger.warning(f"Access denied: user={email}, role={role}, endpoint={http_method} {resource_path}")

    # Build policy with user context
    policy_context = {
        'user_id': user_id,
        'email': email,
        'role': role,
    }

    return generate_policy(user_id, effect, resource_arn, policy_context)
