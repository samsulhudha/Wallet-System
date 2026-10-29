import pytest
import jwt
from django.conf import settings
from rest_framework.test import APIClient
from rest_framework import status
from apps.accounts.models import User


@pytest.mark.django_db
def test_swagger_schema_and_ui_endpoints(api_client):
    """
    Validates that:
    1. /api/schema/ returns a valid OpenAPI 3.0 schema containing all endpoints,
       schemas, parameters, and JWT Bearer security schemes.
    2. /api/docs/ loads Swagger UI successfully.
    3. /api/redoc/ loads ReDoc UI successfully.
    """
    # 1. OpenAPI 3 Schema Endpoint
    schema_res = api_client.get('/api/schema/')
    assert schema_res.status_code == status.HTTP_200_OK

    # In DRF Spectacular, accept application/vnd.oai.openapi+json or json
    schema_json_res = api_client.get('/api/schema/', HTTP_ACCEPT='application/json')
    assert schema_json_res.status_code == status.HTTP_200_OK
    schema = schema_json_res.data if hasattr(schema_json_res, 'data') and isinstance(schema_json_res.data, dict) else {}

    # If returned as raw content or parsed dict
    if not schema and hasattr(schema_json_res, 'content'):
        import json
        schema = json.loads(schema_json_res.content)

    assert schema.get('openapi') == '3.0.3'
    assert 'Enterprise Wallet' in schema.get('info', {}).get('title', '')

    # Verify JWT Bearer Security Scheme is documented
    security_schemes = schema.get('components', {}).get('securitySchemes', {})
    assert 'jwtAuth' in security_schemes
    jwt_scheme = security_schemes['jwtAuth']
    assert jwt_scheme.get('type') == 'http'
    assert jwt_scheme.get('scheme') == 'bearer'
    assert jwt_scheme.get('bearerFormat') == 'JWT'

    # Verify core paths exist in Swagger specification
    paths = schema.get('paths', {})
    assert any('/auth/register/' in p for p in paths)
    assert any('/auth/token/' in p for p in paths)
    assert any('/auth/token/refresh/' in p for p in paths)
    assert any('/wallets/' in p for p in paths)
    assert any('/transfers/' in p for p in paths)
    assert any('/topups/' in p for p in paths)
    assert any('/withdrawals/' in p for p in paths)
    assert any('/transactions/' in p for p in paths)
    assert any('/reverse/' in p for p in paths)
    assert any('/freeze/' in p for p in paths)
    assert any('/admin/flags/' in p or '/admin/risk/flagged/' in p for p in paths)

    # Verify Idempotency-Key header is documented on money-moving transfer path
    transfer_path = next((paths[p] for p in paths if p.endswith('/transfers/')), None)
    assert transfer_path is not None
    post_op = transfer_path.get('post', {})
    param_names = [param.get('name') for param in post_op.get('parameters', [])]
    assert 'Idempotency-Key' in param_names

    # Verify Components / Schemas
    schemas = schema.get('components', {}).get('schemas', {})
    assert 'Wallet' in schemas
    assert 'TransferResponse' in schemas
    assert 'WithdrawalResponse' in schemas
    assert 'TopUpResponse' in schemas
    assert 'RiskDecision' in schemas

    # 2. Swagger UI HTML endpoint
    swagger_ui_res = api_client.get('/api/docs/')
    assert swagger_ui_res.status_code == status.HTTP_200_OK
    assert b'swagger-ui' in swagger_ui_res.content.lower()

    # 3. ReDoc UI HTML endpoint
    redoc_ui_res = api_client.get('/api/redoc/')
    assert redoc_ui_res.status_code == status.HTTP_200_OK
    assert b'redoc' in redoc_ui_res.content.lower()


@pytest.mark.django_db
def test_jwt_token_full_lifecycle_and_authentication(api_client):
    """
    Validates complete JWT authentication:
    1. Registration -> 201 Created
    2. Obtain Token -> returns access and refresh tokens
    3. Decode and verify standard claims in access token
    4. Access protected endpoint with valid Bearer token -> 200 OK
    5. Access protected endpoint with NO token -> 401 Unauthorized
    6. Access protected endpoint with TAMPERED/INVALID token -> 401 Unauthorized
    7. Access protected endpoint with malformed auth header -> 401 Unauthorized
    8. Token refresh -> obtain new access token from refresh token
    9. Use refreshed access token -> 200 OK
    10. Invalid refresh token -> 401 Unauthorized
    """
    # 1. Register new user
    reg_res = api_client.post('/api/auth/register/', {
        'username': 'jwt_tester',
        'email': 'jwt_tester@example.com',
        'password': 'SecurePassword999!',
        'password_confirm': 'SecurePassword999!'
    }, format='json')
    assert reg_res.status_code == status.HTTP_201_CREATED
    user_id = reg_res.data['id']

    # 2. Obtain Token Pair
    token_res = api_client.post('/api/auth/token/', {
        'username': 'jwt_tester',
        'password': 'SecurePassword999!'
    }, format='json')
    assert token_res.status_code == status.HTTP_200_OK
    assert 'access' in token_res.data
    assert 'refresh' in token_res.data

    access_token = token_res.data['access']
    refresh_token = token_res.data['refresh']

    # 3. Inspect JWT Token Claims
    decoded = jwt.decode(
        access_token,
        settings.SECRET_KEY,
        algorithms=['HS256'],
        options={'verify_signature': False}
    )
    assert decoded.get('token_type') == 'access'
    assert str(decoded.get('user_id')) == str(user_id)
    assert 'exp' in decoded
    assert 'jti' in decoded

    # 4. Access protected endpoint with valid Bearer token
    api_client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')
    me_res = api_client.get('/api/auth/me/')
    assert me_res.status_code == status.HTTP_200_OK
    assert me_res.data['username'] == 'jwt_tester'
    assert me_res.data['email'] == 'jwt_tester@example.com'

    # 5. Access protected endpoint with NO token
    api_client.credentials()  # Clear credentials
    no_token_res = api_client.get('/api/auth/me/')
    assert no_token_res.status_code == status.HTTP_401_UNAUTHORIZED
    assert no_token_res.data['error']['code'] == 'AUTHENTICATION_FAILED'

    # 6. Access protected endpoint with TAMPERED token
    api_client.credentials(HTTP_AUTHORIZATION='Bearer invalid.header.payload')
    tampered_res = api_client.get('/api/auth/me/')
    assert tampered_res.status_code == status.HTTP_401_UNAUTHORIZED
    assert tampered_res.data['error']['code'] == 'AUTHENTICATION_FAILED'

    # 7. Access protected endpoint with malformed scheme
    api_client.credentials(HTTP_AUTHORIZATION='Token bogus_token')
    malformed_res = api_client.get('/api/auth/me/')
    assert malformed_res.status_code == status.HTTP_401_UNAUTHORIZED

    # 8. Token Refresh
    api_client.credentials()
    refresh_res = api_client.post('/api/auth/token/refresh/', {
        'refresh': refresh_token
    }, format='json')
    assert refresh_res.status_code == status.HTTP_200_OK
    assert 'access' in refresh_res.data
    new_access_token = refresh_res.data['access']

    # 9. Use refreshed access token
    api_client.credentials(HTTP_AUTHORIZATION=f'Bearer {new_access_token}')
    me_refreshed_res = api_client.get('/api/auth/me/')
    assert me_refreshed_res.status_code == status.HTTP_200_OK
    assert me_refreshed_res.data['username'] == 'jwt_tester'

    # 10. Bogus refresh token
    bad_refresh_res = api_client.post('/api/auth/token/refresh/', {
        'refresh': 'completely_invalid_refresh_string'
    }, format='json')
    assert bad_refresh_res.status_code == status.HTTP_401_UNAUTHORIZED
