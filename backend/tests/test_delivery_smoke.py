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


def test_packaged_mail_checks_do_not_connect_to_outlook(tmp_path,monkeypatch):
    from app.main import app
    from app import mail_tasks
    from app.outlook_probe import MailStore
    monkeypatch.setattr(mail_tasks,'_manager',mail_tasks.MailManager(MailStore(tmp_path/'mail.sqlite3')))
    monkeypatch.setattr(mail_tasks,'list_folders',lambda: (_ for _ in ()).throw(AssertionError('Unexpected real Outlook access')))
    path=Path(__file__).resolve().parents[2]/'packaging'/'smoke_windows_exe.py'
    spec=importlib.util.spec_from_file_location('mail_smoke',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    client=TestClient(app);urls=[]
    def local_get(url,**kwargs):
        urls.append(url);kwargs.pop('timeout',None)
        return client.get(url.replace(module.APP_URL,''),**kwargs)
    def local_post(url,**kwargs):
        urls.append(url);kwargs.pop('timeout',None)
        return client.post(url.replace(module.APP_URL,''),**kwargs)
    monkeypatch.setattr(module.httpx,'get',local_get)
    monkeypatch.setattr(module.httpx,'post',local_post)
    sample=module.assert_mail_local_services()
    monkeypatch.setattr(mail_tasks,'_manager',mail_tasks.MailManager(MailStore(tmp_path/'mail.sqlite3')))
    module.assert_mail_restart_preserved(sample['mail'])
    assert sample['performance']['counters']['cache_hits']>=2
    assert all(url.startswith(module.APP_URL+'/api/mail/') for url in urls)
    assert not any('/folders' in url or '/scan' in url for url in urls)
    assert any(url.endswith('/import') for url in urls)


def test_health_wait_reports_early_exit_with_startup_trace(tmp_path,monkeypatch):
    import pytest
    import httpx
    path=Path(__file__).resolve().parents[2]/'packaging'/'smoke_windows_exe.py'
    spec=importlib.util.spec_from_file_location('startup_smoke',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    log=tmp_path/'startup.log';log.write_text('ModuleNotFoundError: synthetic_runtime_dependency',encoding='utf-8')
    process=type('Process',(),{'poll':lambda self:1})()
    monkeypatch.setattr(module.httpx,'get',lambda *a,**kw: (_ for _ in ()).throw(httpx.ConnectError('not listening')))
    with pytest.raises(RuntimeError,match='synthetic_runtime_dependency'):
        module.wait_for_health(process=process,startup_log=log)


def test_smoke_poll_retries_only_transport_timeouts(monkeypatch):
    import httpx
    path=Path(__file__).resolve().parents[2]/'packaging'/'smoke_windows_exe.py'
    spec=importlib.util.spec_from_file_location('poll_smoke',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    calls=[]
    def get(*args,**kwargs):
        calls.append(kwargs['timeout'])
        if len(calls)==1:raise httpx.ReadTimeout('cold OCR start')
        return httpx.Response(200,json={'ready':True},request=httpx.Request('GET',args[0]))
    monkeypatch.setattr(module.httpx,'get',get)
    monkeypatch.setattr(module.time,'sleep',lambda _:None)
    assert module.poll_json('/test',module.time.monotonic()+30)=={'ready':True}
    assert len(calls)==2


def test_smoke_stops_job_descendants_even_after_launcher_exit():
    path=Path(__file__).resolve().parents[2]/'packaging'/'smoke_windows_exe.py'
    spec=importlib.util.spec_from_file_location('job_smoke',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    calls=[]
    kernel=type('Kernel',(),{'TerminateJobObject':lambda self,h,c:calls.append(('terminate',h)) or True,
                            'CloseHandle':lambda self,h:calls.append(('close',h))})()
    process=type('Process',(),{'_smoke_job':(kernel,123),'poll':lambda self:0,
                              'wait':lambda self,timeout:calls.append(('wait',timeout))})()
    module.stop_process_tree(process)
    assert calls==[('terminate',123),('wait',10),('close',123)]


def test_smoke_cleanup_preserves_primary_failure(tmp_path,monkeypatch):
    import pytest,sys
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]/'packaging'))
    import smoke_mail_exe as module
    monkeypatch.setattr(module.shutil,'rmtree',lambda p: (_ for _ in ()).throw(PermissionError('locked database')))
    ticks=iter([0,11])
    monkeypatch.setattr(module.time,'monotonic',lambda:next(ticks))
    with pytest.raises(RuntimeError,match='original OCR failure'):
        with module.smoke_directory(tmp_path):
            raise RuntimeError('original OCR failure')
    assert 'original OCR failure' in (tmp_path/'failure.log').read_text(encoding='utf-8')
    assert 'locked database' in (tmp_path/'cleanup.log').read_text(encoding='utf-8')
