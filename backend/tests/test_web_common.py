"""Shared entry-point policies must not depend on a product service."""
import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from app.web_common import check_local_request, install_frontend


def test_local_guard_preserves_header_origin_policy():
    from fastapi import Request
    app = FastAPI()
    def guard(request: Request):
        check_local_request(request, 'local only')
    @app.post('/api/action', dependencies=[Depends(guard)])
    def action(): return {'ok': True}
    client = TestClient(app)
    assert client.post('/api/action').status_code == 403
    assert client.post('/api/action', headers={'X-Finance-Local':'1','Origin':'https://evil.example'}).status_code == 403
    for origin in ('http://localhost:8774','http://127.0.0.1:8774','http://[::1]:8774'):
        assert client.post('/api/action', headers={'X-Finance-Local':'1','Origin':origin}).status_code == 200


@pytest.mark.parametrize('serve_files', [False, True])
def test_frontend_policy_and_missing_api_do_not_expose_files(tmp_path, serve_files):
    root = tmp_path / 'frontend'; root.mkdir()
    (root / 'index.html').write_text('app')
    (root / 'help.txt').write_text('help')
    (tmp_path / 'private.txt').write_text('private')
    app = FastAPI(); install_frontend(app, root, serve_files=serve_files)
    client = TestClient(app)
    assert client.get('/workspace').text == 'app'
    assert client.get('/help.txt').text == ('help' if serve_files else 'app')
    assert client.get('/api/unknown').status_code == 404
    assert client.get('/%2e%2e/private.txt').status_code == 404
