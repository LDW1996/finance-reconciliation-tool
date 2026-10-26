import time

from fastapi.testclient import TestClient

from app.mail_tasks import MailManager
from app.outlook_probe import MailStore


def wait(manager):
    for _ in range(100):
        if not manager.status()['busy']: return
        time.sleep(.01)
    raise AssertionError('Mail scan did not complete')


def test_mail_manager_deduplicates_without_business_side_effects(tmp_path):
    calls=[]
    def read(*args):
        calls.append(args)
        return {'rows':[dict(id='one',body='正文',subject='测试',candidates=[])], 'errors':[], 'partial':False}
    manager=MailManager(MailStore(tmp_path/'mail.sqlite3'), reader=read)
    manager.start('store','folder',7,100); wait(manager)
    assert manager.status()['new']==1
    manager.start('store','folder',7,100); wait(manager)
    assert manager.status()['new']==0 and len(calls)==2
    assert manager.store.rows()[0]['body']=='正文'


def test_failure_has_actionable_error_and_no_automatic_unlock(tmp_path):
    def fail(*args): raise ValueError('请打开 Outlook')
    manager=MailManager(MailStore(tmp_path/'mail.sqlite3'),reader=fail)
    manager.start('store','folder',7,100);wait(manager)
    assert manager.status()['error']=='请打开 Outlook'
    assert manager.status()['enabled'] is False


def test_stop_invalidates_scheduled_callback(tmp_path):
    timers=[]
    class Timer:
        def __init__(self,seconds,callback): self.callback=callback; timers.append(self)
        def start(self): pass
        def cancel(self): pass
    manager=MailManager(MailStore(tmp_path/'mail.sqlite3'),reader=lambda *a:dict(rows=[],errors=[],partial=False),timer_factory=Timer)
    manager.configure(True,'store','folder',7,100,60);wait(manager)
    assert len(timers)==1
    manager.configure(False,'store','folder',7,100,60)
    timers[0].callback()
    assert len(timers)==1 and not manager.status()['enabled']


def test_manual_read_cancels_prior_poll_callback(tmp_path):
    timers=[]; calls=[]
    class Timer:
        def __init__(self,seconds,callback): self.callback=callback;self.cancelled=False;timers.append(self)
        def start(self): pass
        def cancel(self): self.cancelled=True
    def read(*args):
        calls.append(1);return dict(rows=[],errors=[],partial=False)
    manager=MailManager(MailStore(tmp_path/'mail.sqlite3'),reader=read,timer_factory=Timer)
    manager.configure(True,'store','folder',7,100,60);wait(manager)
    old=timers[0]
    manager.start('store','folder',7,100);wait(manager)
    old.callback()
    assert old.cancelled and len(calls)==2 and len(timers)==2
    manager.configure(False,'store','folder',7,100,60)


def test_mail_routes_require_local_header_and_allow_sample_import(tmp_path, monkeypatch):
    from app.main import app
    from app import mail_tasks
    manager=MailManager(MailStore(tmp_path/'mail.sqlite3'))
    monkeypatch.setattr(mail_tasks,'_manager',manager)
    client=TestClient(app)
    assert client.get('/api/mail/messages').status_code==403
    headers={'X-Finance-Local':'1','Origin':'http://127.0.0.1:5173'}
    assert client.get('/api/mail/status',headers=headers).status_code==200
    raw=b'Subject: BILL123456\r\nFrom: test@example.com\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n082600028858'
    response=client.post('/api/mail/import',files={'mail':('sample.eml',raw)},headers=headers)
    assert response.status_code==200
    rows=client.get('/api/mail/messages',headers=headers).json()
    assert len(rows)==1 and 'body' not in rows[0]
    assert client.get('/api/mail/messages',headers=headers).headers['cache-control']=='no-store'
    detail=client.get('/api/mail/messages/'+rows[0]['id'],headers=headers).json()
    assert detail['body']=='082600028858'
    assert client.post('/api/mail/import',files={'mail':('sample.eml',raw)},headers={**headers,'Origin':'https://evil.example'}).status_code==403
    assert client.post('/api/mail/scan',json={'store':'','folder':'','days':7,'limit':100},headers=headers).status_code==422
    assert client.delete('/api/mail/messages',headers=headers).status_code==200
    assert manager.store.rows()==[]
