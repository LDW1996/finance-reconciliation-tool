"""Contract tests for the ``/api/profit-center/`` wiring.

The ``profit_center`` package is owned by the team lead; these tests are written
ahead of that module landing on disk. Do not run them until the module exists,
otherwise ``from app.main import app`` fails at import time.

Auth/session handling follows ``backend/tests/conftest.py``: the autouse
``existing_business_api_login`` fixture logs every non-``test_platform_*``
TestClient in as the ``full`` account (which owns the ``excel`` capability), so
the tests below do not need to forge a session themselves. POST requests still
need the ``X-Finance-Local: 1`` mutation header required by the auth middleware
(see ``platform_auth_routes.py``), otherwise the middleware rejects them with
403 before FastAPI validation can produce a 422.
"""
from app.platform_roles import required_capability

MUTATION_HEADERS = {"X-Finance-Local": "1"}


def test_required_capability_maps_profit_center_to_excel():
    assert required_capability('/api/profit-center/run') == 'excel'
    assert required_capability('/api/profit-center/config') == 'excel'
    # Existing guardrails must keep holding.
    assert required_capability('/api/mailbox') is None
    assert required_capability('/api/new-dangerous-route') is None


def test_profit_center_routes_registered_before_frontend_fallback():
    from app.main import app

    indexed = [
        (index, method, route.path)
        for index, route in enumerate(app.routes)
        if getattr(route, 'methods', None)
        for method in route.methods
    ]
    registered = {(method, path) for _, method, path in indexed}
    assert ('GET', '/api/profit-center/config') in registered
    assert ('POST', '/api/profit-center/run') in registered
    assert any(
        method == 'GET' and path.startswith('/api/profit-center/download')
        for _, method, path in indexed
    ), '利润中心下载路由未注册'

    config_index = next(
        index for index, method, path in indexed
        if method == 'GET' and path == '/api/profit-center/config'
    )
    catchall_indexes = [
        index for index, _, path in indexed if '{path:path}' in path
    ]
    assert catchall_indexes, '前端 catch-all 路由未注册'
    assert config_index < min(catchall_indexes), '利润中心路由必须排在前端 catch-all 之前'


def test_config_endpoint_shape():
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        response = client.get('/api/profit-center/config')
    assert response.status_code == 200, response.text
    payload = response.json()
    assert 'month' in payload
    assert 'required_files' in payload
    regions = payload['regions']
    assert len(regions) == 4
    for region in regions:
        assert {'key', 'label', 'has_exec_salary'} <= set(region)


def test_run_without_files_returns_422():
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        response = client.post('/api/profit-center/run', headers=MUTATION_HEADERS)
    assert response.status_code == 422, response.text


def test_download_unknown_job_returns_404():
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        response = client.get('/api/profit-center/download/nonexistent-job-id')
    assert response.status_code == 404, response.text
