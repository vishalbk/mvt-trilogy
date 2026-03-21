"""
MVT Cognito Authentication Handler (S7-01)
Handles user registration, login, token refresh, and password reset
via AWS Cognito User Pool. Serves as the authentication API backend.

Trigger: API Gateway REST (POST /auth/{action})
Actions: register, login, refresh, forgot-password, confirm-forgot-password, verify-email
Output: JWT tokens (id_token, access_token, refresh_token) or status messages
"""

import json
import os
import logging
import hmac
import hashlib
import base64
from datetime import datetime
from decimal import Decimal
import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

cognito = boto3.client('cognito-idp')
dynamodb = boto3.resource('dynamodb')

USER_POOL_ID = os.environ.get('USER_POOL_ID', '')
CLIENT_ID = os.environ.get('COGNITO_CLIENT_ID', '')
CLIENT_SECRET = os.environ.get('COGNITO_CLIENT_SECRET', '')
AUDIT_TABLE = os.environ.get('AUDIT_TABLE', 'mvt-audit-log')

# Default user attributes for new registrations
DEFAULT_ROLE = 'viewer'
VALID_ROLES = ['admin', 'analyst', 'viewer']


def compute_secret_hash(username):
    """Compute Cognito SECRET_HASH for client with secret."""
    if not CLIENT_SECRET:
        return None
    message = username + CLIENT_ID
    dig = hmac.new(
        CLIENT_SECRET.encode('utf-8'),
        msg=message.encode('utf-8'),
        digestmod=hashlib.sha256
    ).digest()
    return base64.b64encode(dig).decode()


def build_response(status_code, body):
    """Build API Gateway response with CORS."""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, Authorization',
        },
        'body': json.dumps(body, default=str),
    }


def log_audit_event(action, username, success, detail=''):
    """Write authentication event to audit log."""
    try:
        table = dynamodb.Table(AUDIT_TABLE)
        table.put_item(Item={
            'eventId': f'auth_{action}#{username}#{datetime.utcnow().isoformat()}',
            'event_type': f'auth_{action}',
            'username': username,
            'action': action,
            'success': success,
            'detail': detail,
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'ttl': int((datetime.utcnow()).timestamp()) + 90 * 86400,  # 90-day retention
        })
    except Exception as e:
        logger.warning(f"Audit log write failed: {e}")


def handle_register(body):
    """Register a new user."""
    email = body.get('email', '').strip().lower()
    password = body.get('password', '')
    name = body.get('name', '')

    if not email or not password:
        return build_response(400, {'error': 'Email and password are required'})

    if len(password) < 8:
        return build_response(400, {'error': 'Password must be at least 8 characters'})

    try:
        params = {
            'ClientId': CLIENT_ID,
            'Username': email,
            'Password': password,
            'UserAttributes': [
                {'Name': 'email', 'Value': email},
                {'Name': 'name', 'Value': name or email.split('@')[0]},
                {'Name': 'custom:role', 'Value': DEFAULT_ROLE},
            ],
        }

        secret_hash = compute_secret_hash(email)
        if secret_hash:
            params['SecretHash'] = secret_hash

        response = cognito.sign_up(**params)

        log_audit_event('register', email, True)

        return build_response(200, {
            'message': 'Registration successful. Please check your email for verification code.',
            'user_sub': response.get('UserSub'),
            'confirmed': response.get('UserConfirmed', False),
        })

    except cognito.exceptions.UsernameExistsException:
        log_audit_event('register', email, False, 'username_exists')
        return build_response(409, {'error': 'An account with this email already exists'})

    except cognito.exceptions.InvalidPasswordException as e:
        return build_response(400, {'error': f'Password does not meet requirements: {str(e)}'})

    except Exception as e:
        logger.error(f"Registration failed: {e}")
        log_audit_event('register', email, False, str(e))
        return build_response(500, {'error': 'Registration failed'})


def handle_verify_email(body):
    """Verify email with confirmation code."""
    email = body.get('email', '').strip().lower()
    code = body.get('code', '').strip()

    if not email or not code:
        return build_response(400, {'error': 'Email and verification code are required'})

    try:
        params = {
            'ClientId': CLIENT_ID,
            'Username': email,
            'ConfirmationCode': code,
        }

        secret_hash = compute_secret_hash(email)
        if secret_hash:
            params['SecretHash'] = secret_hash

        cognito.confirm_sign_up(**params)

        log_audit_event('verify_email', email, True)
        return build_response(200, {'message': 'Email verified successfully. You can now log in.'})

    except cognito.exceptions.CodeMismatchException:
        return build_response(400, {'error': 'Invalid verification code'})

    except cognito.exceptions.ExpiredCodeException:
        return build_response(400, {'error': 'Verification code has expired. Please request a new one.'})

    except Exception as e:
        logger.error(f"Email verification failed: {e}")
        return build_response(500, {'error': 'Verification failed'})


def handle_login(body):
    """Authenticate user and return tokens."""
    email = body.get('email', '').strip().lower()
    password = body.get('password', '')

    if not email or not password:
        return build_response(400, {'error': 'Email and password are required'})

    try:
        params = {
            'ClientId': CLIENT_ID,
            'AuthFlow': 'USER_PASSWORD_AUTH',
            'AuthParameters': {
                'USERNAME': email,
                'PASSWORD': password,
            },
        }

        secret_hash = compute_secret_hash(email)
        if secret_hash:
            params['AuthParameters']['SECRET_HASH'] = secret_hash

        response = cognito.initiate_auth(**params)
        auth_result = response.get('AuthenticationResult', {})

        # Get user attributes
        user_info = cognito.get_user(AccessToken=auth_result.get('AccessToken', ''))
        attrs = {a['Name']: a['Value'] for a in user_info.get('UserAttributes', [])}

        log_audit_event('login', email, True)

        return build_response(200, {
            'id_token': auth_result.get('IdToken'),
            'access_token': auth_result.get('AccessToken'),
            'refresh_token': auth_result.get('RefreshToken'),
            'expires_in': auth_result.get('ExpiresIn', 3600),
            'user': {
                'email': attrs.get('email', email),
                'name': attrs.get('name', ''),
                'role': attrs.get('custom:role', DEFAULT_ROLE),
                'sub': attrs.get('sub', ''),
            },
        })

    except cognito.exceptions.NotAuthorizedException:
        log_audit_event('login', email, False, 'invalid_credentials')
        return build_response(401, {'error': 'Invalid email or password'})

    except cognito.exceptions.UserNotConfirmedException:
        log_audit_event('login', email, False, 'unconfirmed')
        return build_response(403, {'error': 'Email not verified. Please check your inbox.'})

    except cognito.exceptions.UserNotFoundException:
        log_audit_event('login', email, False, 'user_not_found')
        return build_response(401, {'error': 'Invalid email or password'})

    except Exception as e:
        logger.error(f"Login failed: {e}")
        log_audit_event('login', email, False, str(e))
        return build_response(500, {'error': 'Authentication failed'})


def handle_refresh(body):
    """Refresh access token using refresh token."""
    refresh_token = body.get('refresh_token', '')
    email = body.get('email', '').strip().lower()

    if not refresh_token:
        return build_response(400, {'error': 'Refresh token is required'})

    try:
        params = {
            'ClientId': CLIENT_ID,
            'AuthFlow': 'REFRESH_TOKEN_AUTH',
            'AuthParameters': {
                'REFRESH_TOKEN': refresh_token,
            },
        }

        if email:
            secret_hash = compute_secret_hash(email)
            if secret_hash:
                params['AuthParameters']['SECRET_HASH'] = secret_hash

        response = cognito.initiate_auth(**params)
        auth_result = response.get('AuthenticationResult', {})

        return build_response(200, {
            'id_token': auth_result.get('IdToken'),
            'access_token': auth_result.get('AccessToken'),
            'expires_in': auth_result.get('ExpiresIn', 3600),
        })

    except cognito.exceptions.NotAuthorizedException:
        return build_response(401, {'error': 'Refresh token expired. Please log in again.'})

    except Exception as e:
        logger.error(f"Token refresh failed: {e}")
        return build_response(500, {'error': 'Token refresh failed'})


def handle_forgot_password(body):
    """Initiate password reset flow."""
    email = body.get('email', '').strip().lower()

    if not email:
        return build_response(400, {'error': 'Email is required'})

    try:
        params = {
            'ClientId': CLIENT_ID,
            'Username': email,
        }

        secret_hash = compute_secret_hash(email)
        if secret_hash:
            params['SecretHash'] = secret_hash

        cognito.forgot_password(**params)

        log_audit_event('forgot_password', email, True)
        return build_response(200, {
            'message': 'Password reset code sent to your email.',
        })

    except cognito.exceptions.UserNotFoundException:
        # Don't reveal if user exists
        return build_response(200, {
            'message': 'If an account exists with this email, a reset code has been sent.',
        })

    except Exception as e:
        logger.error(f"Forgot password failed: {e}")
        return build_response(500, {'error': 'Password reset failed'})


def handle_confirm_forgot_password(body):
    """Complete password reset with code."""
    email = body.get('email', '').strip().lower()
    code = body.get('code', '').strip()
    new_password = body.get('new_password', '')

    if not all([email, code, new_password]):
        return build_response(400, {'error': 'Email, code, and new password are required'})

    try:
        params = {
            'ClientId': CLIENT_ID,
            'Username': email,
            'ConfirmationCode': code,
            'Password': new_password,
        }

        secret_hash = compute_secret_hash(email)
        if secret_hash:
            params['SecretHash'] = secret_hash

        cognito.confirm_forgot_password(**params)

        log_audit_event('password_reset', email, True)
        return build_response(200, {'message': 'Password reset successful. You can now log in.'})

    except cognito.exceptions.CodeMismatchException:
        return build_response(400, {'error': 'Invalid reset code'})

    except Exception as e:
        logger.error(f"Confirm forgot password failed: {e}")
        return build_response(500, {'error': 'Password reset confirmation failed'})


# Action router
ACTION_HANDLERS = {
    'register': handle_register,
    'login': handle_login,
    'verify-email': handle_verify_email,
    'refresh': handle_refresh,
    'forgot-password': handle_forgot_password,
    'confirm-forgot-password': handle_confirm_forgot_password,
}


def handler(event, context):
    """Main Lambda handler — routes to action-specific handlers."""
    logger.info(f"Auth request: {json.dumps({k: v for k, v in event.items() if k != 'body'}, default=str)}")

    # CORS preflight
    method = event.get('httpMethod', event.get('requestContext', {}).get('http', {}).get('method', 'POST'))
    if method == 'OPTIONS':
        return build_response(200, {'status': 'ok'})

    # Parse action from path
    path_params = event.get('pathParameters') or {}
    action = path_params.get('action', '')

    if not action:
        # Try extracting from path
        path = event.get('path', event.get('rawPath', ''))
        action = path.rstrip('/').split('/')[-1]

    if action not in ACTION_HANDLERS:
        return build_response(400, {
            'error': f'Invalid action: {action}',
            'valid_actions': list(ACTION_HANDLERS.keys()),
        })

    # Parse body
    body = event.get('body', '{}')
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            return build_response(400, {'error': 'Invalid JSON body'})

    return ACTION_HANDLERS[action](body)
