import sqlite3
import time
from app.mail_tasks import MailManager
from app.outlook_probe import MailStore


def idle(manager):
    end=time.monotonic()+5
    while manager.status()['analysis_busy'] and time.monotonic()<end:time.sleep(.02)
    assert not manager.status()['analysis_busy']


def test_bad_mail_does_not_stop_following_mail_or_repeat_forever(tmp_path,monkeypatch):
    monkeypatch.setattr('app.mail_tasks.configured_workers',lambda _:1)
    from app import mail_matching
    build=mail_matching.build_payments
    calls=[]
    def broken(row):
        calls.append(row['id'])
        if row['id']=='bad':raise AttributeError('bad mail field')
        return build(row)
    monkeypatch.setattr(mail_matching,'build_payments',broken)
    store=MailStore(tmp_path/'mail.sqlite3')
    store.save([dict(id='bad',body='bad'),dict(id='good',body='good')])
    manager=MailManager(store,analyzer=lambda *a:None)
    idle(manager)
    assert calls==['bad','good']
    assert manager.status()['analysis_pending']==0
    assert not manager.status().get('analysis_error')
    assert store.payment_summaries(['bad'])['bad']['status']=='failed'
    assert manager.status()['analysis_skipped']==1
    assert 'good' in store.payment_index()
    reopened=MailManager(MailStore(store.path),analyzer=lambda *a:None)
    idle(reopened)
    assert calls==['bad','good']  # quarantined content is not retried on every restart


def test_database_failure_is_not_misclassified_as_bad_mail(tmp_path,monkeypatch):
    monkeypatch.setattr('app.mail_tasks.configured_workers',lambda _:1)
    from app import mail_matching
    monkeypatch.setattr(mail_matching,'build_payments',lambda _:(_ for _ in ()).throw(sqlite3.OperationalError('database locked')))
    store=MailStore(tmp_path/'mail.sqlite3');store.save([dict(id='one',body='preserve')])
    manager=MailManager(store,analyzer=lambda *a:None,analysis_retry_limit=0);idle(manager)
    assert manager.status()['analysis_pending']==1
    assert manager.status().get('analysis_error')
    assert store.get('one')['body']=='preserve'
