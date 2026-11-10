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


class FakeMail:
    """Minimal mail manager: only the locks and flags the transition guard touches."""
    def __init__(self):
        from threading import RLock
        self.lock=RLock()
        self.state={'busy':False,'enabled':False,'next_run':None}
        self.maintenance_active=False
        self.analysis_busy=False
        self.platform_suspended=False
    def suspend_platform(self):
        if self.state['busy'] or self.maintenance_active or self.analysis_busy:return False
        self.platform_suspended=True
        return True


def guard_request():
    from types import SimpleNamespace
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(platform_auth=SimpleNamespace(business_requests=0))))


def test_transition_guard_pauses_ilog_task_and_marks_reason(tmp_path,monkeypatch):
    from app import platform_runtime, mail_tasks, ilog_tasks
    from app.ilog_tasks import TaskManager
    mail=FakeMail()
    tasks=TaskManager(tmp_path/'tasks',auto_start=False)
    task=tasks.create('id','Account',['ABC123'],[],'guard-pause',True)
    monkeypatch.setattr(mail_tasks,'_manager',mail)
    monkeypatch.setattr(ilog_tasks,'_manager',tasks)
    assert platform_runtime.transition_guard(guard_request(),None) is True
    result=tasks.get(task['id'])
    assert result['state']=='paused' and '切换账号' in result['notice']
    assert mail.platform_suspended


def test_transition_guard_releases_platform_lock_before_waiting_for_mail(tmp_path,monkeypatch):
    from threading import Event, Thread
    from app import platform_runtime, mail_tasks, ilog_tasks
    from app.ilog_tasks import TaskManager
    mail=FakeMail();held=Event();release=Event()
    def hold():
        with mail.lock:held.set();release.wait(5)
    holder=Thread(target=hold,daemon=True);holder.start();assert held.wait(2)
    tasks=TaskManager(tmp_path/'tasks',auto_start=False)
    task=tasks.create('id','Account',['ABC123'],[],'guard-release',True)
    monkeypatch.setattr(mail_tasks,'_manager',mail)
    monkeypatch.setattr(ilog_tasks,'_manager',tasks)
    worker=Thread(target=lambda:platform_runtime.transition_guard(guard_request(),None),daemon=True);worker.start()
    deadline=time.time()+3
    while time.time()<deadline and tasks.get(task['id'])['state']=='running':time.sleep(0.02)
    assert tasks.get(task['id'])['state']=='paused','任务应先被暂停，随后释放平台切换锁'
    # 邮件锁仍被占用时，平台切换锁必须已经可获取，否则解锁线程会被登录流程一起堵住。
    acquired=ilog_tasks.platform_transition_lock.acquire(timeout=1)
    if acquired:ilog_tasks.platform_transition_lock.release()
    release.set();holder.join(2);worker.join(5)
    assert acquired,'等待邮件锁时不应继续持有平台切换锁'


def test_transition_guard_times_out_and_resumes_task_when_mail_lock_is_held(tmp_path,monkeypatch):
    import pytest
    from threading import Event, Thread
    from fastapi import HTTPException
    from app import platform_runtime, mail_tasks, ilog_tasks
    from app.ilog_tasks import TaskManager
    mail=FakeMail();held=Event();release=Event()
    def hold():
        with mail.lock:held.set();release.wait(5)
    holder=Thread(target=hold,daemon=True);holder.start();assert held.wait(2)
    tasks=TaskManager(tmp_path/'tasks',auto_start=False)
    task=tasks.create('id','Account',['ABC123'],[],'guard-timeout',True)
    monkeypatch.setattr(mail_tasks,'_manager',mail)
    monkeypatch.setattr(ilog_tasks,'_manager',tasks)
    monkeypatch.setattr(platform_runtime,'MAIL_TRANSITION_TIMEOUT',0.3)
    errors=[]
    def call():
        try:platform_runtime.transition_guard(guard_request(),None)
        except HTTPException as error:errors.append(error)
    worker=Thread(target=call,daemon=True);worker.start();worker.join(5)
    release.set();holder.join(2)
    assert not worker.is_alive()
    assert [error.status_code for error in errors]==[409]
    result=tasks.get(task['id'])
    assert result['state']=='running' and 'notice' not in result


def test_transition_guard_resumes_task_when_mail_refuses(tmp_path,monkeypatch):
    import pytest
    from fastapi import HTTPException
    from app import platform_runtime, mail_tasks, ilog_tasks
    from app.ilog_tasks import TaskManager
    class RefusingMail(FakeMail):
        def suspend_platform(self):return False
    mail=RefusingMail()
    tasks=TaskManager(tmp_path/'tasks',auto_start=False)
    task=tasks.create('id','Account',['ABC123'],[],'guard-refuse',True)
    monkeypatch.setattr(mail_tasks,'_manager',mail)
    monkeypatch.setattr(ilog_tasks,'_manager',tasks)
    with pytest.raises(HTTPException) as error:platform_runtime.transition_guard(guard_request(),None)
    assert error.value.status_code==409
    result=tasks.get(task['id'])
    assert result['state']=='running' and 'notice' not in result


def test_transition_guard_leaves_task_running_when_mail_is_busy(tmp_path,monkeypatch):
    import pytest
    from fastapi import HTTPException
    from app import platform_runtime, mail_tasks, ilog_tasks
    from app.ilog_tasks import TaskManager
    mail=FakeMail();mail.state['busy']=True
    tasks=TaskManager(tmp_path/'tasks',auto_start=False)
    task=tasks.create('id','Account',['ABC123'],[],'guard-busy',True)
    monkeypatch.setattr(mail_tasks,'_manager',mail)
    monkeypatch.setattr(ilog_tasks,'_manager',tasks)
    with pytest.raises(HTTPException):platform_runtime.transition_guard(guard_request(),None)
    result=tasks.get(task['id'])
    assert result['state']=='running' and 'notice' not in result
