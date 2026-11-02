import sqlite3
import pytest
from app.mail_tasks import MailManager
from app.outlook_probe import MailStore


def manager(tmp_path):
    return MailManager(MailStore(tmp_path/'mail.sqlite3'),reader=lambda *a:dict(rows=[],errors=[],partial=False))


def test_settings_write_failure_never_leaves_phantom_busy(tmp_path,monkeypatch):
    m=manager(tmp_path)
    original=m.store.set_state
    def fail(key,value):
        if key=='poll_settings':raise sqlite3.OperationalError('database is locked')
        return original(key,value)
    monkeypatch.setattr(m.store,'set_state',fail)
    with pytest.raises(sqlite3.OperationalError):m.start('s','f',7,100)
    assert not m.state['busy']
    assert m.state['error'] and not m.state['enabled']


def test_thread_creation_failure_clears_busy(tmp_path,monkeypatch):
    m=manager(tmp_path)
    class Thread:
        def __init__(self,*a,**k):pass
        def start(self):raise RuntimeError('cannot start new thread')
    monkeypatch.setattr('app.mail_tasks.Thread',Thread)
    with pytest.raises(RuntimeError):m.start('s','f',7,100)
    assert not m.state['busy'] and m.state['error']


def test_failed_configuration_is_not_applied(tmp_path,monkeypatch):
    m=manager(tmp_path)
    def fail(*a):raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(m.store,'set_state',fail)
    with pytest.raises(sqlite3.OperationalError):m.configure(True,'s','f',7,100,60)
    assert not m.state['enabled'] and not m.state['busy']


def test_skipped_image_is_not_saved_as_success(tmp_path):
    m=manager(tmp_path)
    m.store.save([dict(id='one',body='',subject='',_attachments=[dict(id='asset',name='line.png',raw=b'fake',status='pending')])])
    m._process_mail('one',False,analyzer=lambda *a:dict(status='skipped',warnings=['图片短边不超过2像素'],fields={k:[] for k in ('payer','date','amount','fee','identifiers')},pages=[]))
    asset=m.store.get('one')['attachment_details'][0]
    assert asset['status']=='skipped' and '2像素' in asset['error']
