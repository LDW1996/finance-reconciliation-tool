import json
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.platform_auth import hash_password
from app.platform_auth_routes import install_platform_auth

HEADERS = {"X-Requested-With": "XMLHttpRequest"}

def make_client(tmp_path, guard=None, on_login=None):
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps({name: hash_password("test-password", iterations=100_000) for name in ("excel", "mail", "ilog", "full")}))
    app = FastAPI()
    install_platform_auth(app, credentials_path=path, transition_guard=guard, on_login=on_login)
    @app.get('/api/mail/test')
    def endpoint():
        return {"ok": True}
    return TestClient(app)

def test_login_logout_and_restart(tmp_path):
    client = make_client(tmp_path)
    assert client.get('/api/mail/test').status_code == 401
    assert client.post('/api/platform/login', json={"username":"mail", "password":"wrong"}, headers=HEADERS).status_code == 401
    response = client.post('/api/platform/login', json={"username":"mail", "password":"test-password"}, headers=HEADERS)
    assert response.status_code == 200
    assert 'httponly' in response.headers['set-cookie'].lower()
    assert 'max-age' not in response.headers['set-cookie'].lower()
    assert client.get('/api/mail/test').status_code == 200
    second = make_client(tmp_path)
    second.cookies.update(client.cookies)
    assert second.get('/api/platform/session').status_code == 401
    assert client.post('/api/platform/logout', headers=HEADERS).status_code == 200
    assert client.get('/api/platform/session').status_code == 401

def test_guard_preserves_session_and_origin(tmp_path):
    client = make_client(tmp_path)
    client.post('/api/platform/login', json={"username":"mail", "password":"test-password"}, headers=HEADERS)
    client.app.state.platform_auth.transition_guard = lambda request, next_identity: False
    assert client.post('/api/platform/logout', headers=HEADERS).status_code == 409
    assert client.get('/api/platform/session').status_code == 200
    assert client.post('/api/platform/logout').status_code == 403
    assert client.get('/api/mail/test', headers={"Origin":"https://evil.example"}).status_code == 403

def test_global_switch_and_failed_callback_restore(tmp_path):
    client = make_client(tmp_path)
    login = lambda name: client.post('/api/platform/login', json={"username": name, "password": "test-password"}, headers=HEADERS)
    assert login('mail').status_code == 200
    old_cookie = client.cookies.get('finance_platform_session')
    assert login('excel').status_code == 200
    assert client.app.state.platform_auth.identity(old_cookie) is None
    current_cookie = client.cookies.get('finance_platform_session')
    assert client.app.state.platform_auth.allowed('excel')
    assert not client.app.state.platform_auth.allowed('mail')


def test_failed_login_callback_restores_previous_session(tmp_path):
    def callback(request, identity):
        assert request.app.state.platform_auth.allowed(identity["role"])
        if identity["role"] == "excel":
            raise RuntimeError("simulated startup failure")
    client = make_client(tmp_path, on_login=callback)
    def login(name):
        return client.post('/api/platform/login', json={"username":name,"password":"test-password"}, headers=HEADERS)
    assert login('mail').status_code == 200
    old_cookie = client.cookies.get('finance_platform_session')
    assert login('excel').status_code == 503
    assert client.cookies.get('finance_platform_session') == old_cookie
    assert client.get('/api/platform/session').json()['role'] == 'mail'
