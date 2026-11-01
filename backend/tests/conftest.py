"""Existing business API tests log in normally; auth tests remain unauthenticated."""
import json
import pytest

@pytest.fixture(scope='session')
def platform_test_credentials(tmp_path_factory):
    from app.platform_auth import hash_password
    path=tmp_path_factory.mktemp('platform-accounts')/'accounts.json'
    path.write_text(json.dumps({name:hash_password('test-only-password',iterations=100000) for name in ('excel','mail','ilog','full')}),encoding='utf-8')
    return path

@pytest.fixture(autouse=True)
def existing_business_api_login(request,monkeypatch,tmp_path,platform_test_credentials):
    if request.node.path.name.startswith('test_platform_'):return
    from starlette.testclient import TestClient
    from app import mail_tasks, ilog_tasks
    monkeypatch.setenv('FINANCE_RECONCILE_DATA_DIR',str(tmp_path/'isolated-default-data'))
    monkeypatch.setenv('FINANCE_PLATFORM_ACCOUNTS',str(platform_test_credentials))
    monkeypatch.setattr(mail_tasks,'_manager',None)
    monkeypatch.setattr(ilog_tasks,'_manager',None)
    monkeypatch.setattr(mail_tasks,'_platform_permission',None)
    monkeypatch.setattr(ilog_tasks,'_platform_permission',None)
    original=TestClient.__init__
    def initialize(self,*args,**kwargs):
        original(self,*args,**kwargs)
        if hasattr(self.app.state,'platform_auth'):
            self.app.state.platform_auth.attempts.clear()
            response=self.post('/api/platform/login',json={'username':'full','password':'test-only-password'},headers={'X-Finance-Local':'1'})
            assert response.status_code==200,response.text
    monkeypatch.setattr(TestClient,'__init__',initialize)
