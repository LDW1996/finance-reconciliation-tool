import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient


def test_packaged_ilog_local_checks_use_only_local_services(tmp_path,monkeypatch):
    from app.main import app
    from app import ilog_tasks
    monkeypatch.setenv('FINANCE_RECONCILE_DATA_DIR',str(tmp_path/'data'))
    monkeypatch.setattr(ilog_tasks,'_manager',ilog_tasks.TaskManager(tmp_path/'tasks',auto_start=False))
    path=Path(__file__).resolve().parents[2]/'packaging'/'smoke_windows_exe.py'
    spec=importlib.util.spec_from_file_location('delivery_smoke',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    client=TestClient(app)
    urls=[]
    def local_get(url,**kwargs):
        urls.append(url);return client.get(url.replace(module.APP_URL,''))
    def local_post(url,**kwargs):
        urls.append(url);kwargs.pop('timeout',None);return client.post(url.replace(module.APP_URL,''),**kwargs)
    def local_delete(url,**kwargs):
        urls.append(url);kwargs.pop('timeout',None);return client.delete(url.replace(module.APP_URL,''),**kwargs)
    monkeypatch.setattr(module.httpx,'get',local_get)
    monkeypatch.setattr(module.httpx,'post',local_post)
    monkeypatch.setattr(module.httpx,'delete',local_delete)
    module.assert_ilog_local_services()
    assert all(url.startswith(module.APP_URL+'/api/ilog/') for url in urls)
    assert not any(url.endswith('/tasks') for url in urls)
    assert client.get('/api/ilog/accounts').json()==[]
