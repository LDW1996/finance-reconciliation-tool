import pytest
from app.platform_roles import ROLE_CAPABILITIES, required_capability

@pytest.mark.parametrize('role', ['excel','mail','ilog','full'])
def test_fixed_matrix(role):
    for capability, path in [('excel','/api/analyze'),('mail','/api/mail/tasks'),('ilog','/api/ilog/accounts')]:
        assert required_capability(path) == capability
        assert (capability in ROLE_CAPABILITIES[role]) == (role == capability or role == 'full')
    assert required_capability('/api/new-dangerous-route') is None
    assert required_capability('/api/mailbox') is None


def test_session_route_precedes_frontend_fallback():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        response = client.get('/api/platform/session')
    assert response.status_code == 401
    assert response.headers['content-type'].startswith('application/json')
