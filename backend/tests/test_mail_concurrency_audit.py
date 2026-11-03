from concurrent.futures import ThreadPoolExecutor
import pytest
from app.mail_tasks import MailManager
from app.outlook_probe import MailStore


class FailedThread:
    def __init__(self,*a,**k):pass
    def start(self):raise RuntimeError('cannot start new thread')


def test_analysis_launch_failure_restores_state_and_keeps_queue(tmp_path,monkeypatch):
    m=MailManager(MailStore(tmp_path/'mail.sqlite3'),analyzer=lambda *a:{})
    m.store.save([dict(id='one',body='',subject='')])
    monkeypatch.setattr('app.mail_tasks.Thread',FailedThread)
    with pytest.raises(RuntimeError):m.analyze('one')
    assert not m.analysis_busy
    assert m.state['analysis_halted'] and len(m.analysis_queue)==1


def test_maintenance_launch_failure_releases_guard(tmp_path,monkeypatch):
    m=MailManager(MailStore(tmp_path/'mail.sqlite3'),analyzer=lambda *a:{})
    monkeypatch.setattr('app.mail_tasks.Thread',FailedThread)
    with pytest.raises(RuntimeError):m.maintain_storage('preview')
    assert not m.maintenance_active
    assert m.maintenance_job()['status']=='failed'


def test_close_error_never_leaks_writer_gate(tmp_path,monkeypatch):
    import sqlite3
    from app.mail_database import MailDatabase
    database=MailDatabase(tmp_path/'db.sqlite3');original=sqlite3.connect
    class BadClose(sqlite3.Connection):
        def close(self):
            super().close()
            raise OSError('close error')
    monkeypatch.setattr('app.mail_database.sqlite3.connect',lambda *a,**k:original(*a,**k,factory=BadClose))
    with pytest.raises(OSError):
        with database.connection() as db:db.execute('CREATE TABLE example (id INTEGER)')
    monkeypatch.setattr('app.mail_database.sqlite3.connect',original)
    with ThreadPoolExecutor(1) as pool:
        def write():
            with database.connection() as db:db.execute('INSERT INTO example VALUES(1)')
        pool.submit(write).result(timeout=2)


def test_maintenance_prepare_failure_does_not_leak_guard(tmp_path,monkeypatch):
    m=MailManager(MailStore(tmp_path/'mail.sqlite3'),analyzer=lambda *a:{})
    original=m.store.set_state
    def fail(key,value):
        if key=='mail_maintenance_job':raise OSError('write failed')
        return original(key,value)
    monkeypatch.setattr(m.store,'set_state',fail)
    with pytest.raises(OSError):m.maintain_storage('preview')
    assert not m.maintenance_active
