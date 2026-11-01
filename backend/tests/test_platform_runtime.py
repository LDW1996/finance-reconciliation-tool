import json
import time
from threading import Event
from app.mail_tasks import MailManager
from app.outlook_probe import MailStore
from test_recognition_reuse import save, recognition, idle


def test_denied_start_keeps_original_key_cursor_and_pending_attachments(tmp_path,monkeypatch):
    monkeypatch.setenv('FINANCE_RECONCILE_DATA_DIR',str(tmp_path/'data'))
    store=MailStore(tmp_path/'mail.sqlite3')
    save(store,'old-pending',raw=b'pending')
    store.set_state('sync_progress',{'cursor':{'folder':2,'offset':852},'scanned':2401})
    store.set_state('poll_settings',{'enabled':True,'resume_on_start':True,'store':'s','folder':'f','days':7,'limit':100,'seconds':300})
    key=(tmp_path/'mail.key').read_bytes()
    before=store.get('old-pending')
    allowed=False
    calls=[]
    def analyze(*args):calls.append(args);return recognition()
    manager=MailManager(store,analyzer=analyze,permission_check=lambda:allowed)
    assert not manager.analysis_busy and not calls
    assert store.get('old-pending')==before
    assert store.get_state('sync_progress')['cursor']['offset']==852
    assert store.get_state('poll_settings')['enabled'] is True
    assert (tmp_path/'mail.key').read_bytes()==key
    allowed=True
    manager.resume_platform();idle(manager)
    assert len(calls)==1
    completed=store.get('old-pending')
    assert completed['attachment_details'][0]['status']=='done'
    # A second login does not reprocess already-completed evidence.
    manager.resume_platform();idle(manager)
    assert store.get('old-pending')==completed and len(calls)==1
    assert store.get_state('sync_progress')['cursor']['offset']==852
    assert (tmp_path/'mail.key').read_bytes()==key


def test_logout_finishes_active_mail_and_preserves_remaining_queue(tmp_path,monkeypatch):
    monkeypatch.setenv('FINANCE_RECONCILE_DATA_DIR',str(tmp_path/'data'))
    store=MailStore(tmp_path/'mail.sqlite3')
    save(store,'one',raw=b'one');save(store,'two',raw=b'two')
    started=Event();release=Event();calls=[]
    def analyze(raw,name):
        calls.append(raw);started.set();assert release.wait(5);return recognition()
    manager=MailManager(store,analyzer=analyze,analysis_workers=1)
    assert started.wait(5)
    try:assert manager.suspend_platform() is False
    finally:release.set()
    idle(manager)
    assert len(calls)==2 and len(manager.analysis_queue)==0
    states=[store.get(name)['attachment_details'][0]['status'] for name in ('one','two')]
    assert sorted(states)==['done','done']
    assert manager.suspend_platform() is True
    manager.resume_platform();idle(manager)
    assert len(calls)==2 and len(manager.analysis_queue)==0


def test_logout_does_not_disable_durable_polling_choice(tmp_path,monkeypatch):
    monkeypatch.setenv('FINANCE_RECONCILE_DATA_DIR',str(tmp_path/'data'))
    store=MailStore(tmp_path/'mail.sqlite3')
    settings=dict(enabled=True,resume_on_start=False,store='s',folder='f',days=7,limit=100,seconds=300)
    store.set_state('poll_settings',settings)
    manager=MailManager(store,analyzer=lambda *args:recognition())
    manager.state['enabled']=True
    assert manager.suspend_platform()
    assert manager.state['enabled'] is False
    assert store.get_state('poll_settings')==settings


def test_ilog_permission_gate_pauses_without_executing_or_losing_request_key(tmp_path,monkeypatch):
    from app.ilog_tasks import TaskManager
    monkeypatch.setenv('FINANCE_RECONCILE_DATA_DIR',str(tmp_path/'data'))
    calls=[]
    manager=TaskManager(tmp_path/'tasks',auto_start=False,runner=lambda *a,**kw:calls.append(a),permission_check=lambda:False)
    task=manager.create('id','Account',['ABC123'],[],'original-request-key',True)
    manager.drain(task['id'])
    result=manager.get(task['id'])
    assert result['state']=='paused' and result['rows'][0]['status']=='waiting' and not calls
    with manager.db() as db:assert db.execute('select request_key from tasks where id=?',(task['id'],)).fetchone()[0]=='original-request-key'


def test_ilog_rejection_does_not_suspend_mail(tmp_path,monkeypatch):
    from types import SimpleNamespace
    import pytest
    from fastapi import HTTPException
    from app import platform_runtime, mail_tasks, ilog_tasks
    from app.ilog_tasks import TaskManager
    mail=MailManager(MailStore(tmp_path/'mail.sqlite3'),analyzer=lambda *args:recognition())
    tasks=TaskManager(tmp_path/'tasks',auto_start=False)
    task=tasks.create('id','Account',['ABC123'],[],'guard-test',True)
    with tasks.db() as db:
        data=tasks.get(task['id']);data['state']='running';data['rows'][0]['status']='processing';tasks.save(db,data)
    monkeypatch.setattr(mail_tasks,'_manager',mail)
    monkeypatch.setattr(ilog_tasks,'_manager',tasks)
    request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(platform_auth=SimpleNamespace(business_requests=0))))
    with pytest.raises(HTTPException):platform_runtime.transition_guard(request,None)
    assert not mail.platform_suspended
    assert mail.generation==0


def test_successful_transition_with_real_mail_manager_does_not_deadlock(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from threading import Thread
    from app import platform_runtime, mail_tasks, ilog_tasks
    mail=MailManager(MailStore(tmp_path/'mail.sqlite3'),analyzer=lambda *args:recognition())
    monkeypatch.setattr(mail_tasks,'_manager',mail)
    monkeypatch.setattr(ilog_tasks,'_manager',None)
    request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(platform_auth=SimpleNamespace(business_requests=0))))
    results=[]
    worker=Thread(target=lambda:results.append(platform_runtime.transition_guard(request,None)),daemon=True)
    worker.start();worker.join(2)
    assert not worker.is_alive()
    assert results==[True] and mail.platform_suspended
