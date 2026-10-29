import pytest
from app.outlook_probe import MailStore
from app.mail_cleanup import MailCleanup


def add(store, identity='safe', **changes):
    mail=dict(id=identity, subject='周末愉快', body='祝大家周末愉快', source='outlook', outlook_read_version=1, read_complete=True, attachments=[])
    mail.update(changes)
    store.save([mail])
    store.set_payments(identity, [], dict(status='empty', count=0, business_ids=[]))
    return mail


def test_explicit_selection_backup_restore_and_progress_preserved(tmp_path):
    store=MailStore(tmp_path/'mail.sqlite3');add(store);add(store,'other')
    store.set_state('outlook_scan_progress',dict(cursor=852))
    cleanup=MailCleanup(store);preview=cleanup.preview()
    assert len(preview['candidates'])==2 and store.mail_count()==2
    with pytest.raises(ValueError):cleanup.delete(preview['candidates'],[])
    result=cleanup.delete(preview['candidates'],['safe'])
    assert result['deleted']==1 and store.get('other') and not store.get('safe')
    assert store.get_state('outlook_scan_progress')['cursor']==852
    backup=(cleanup.directory/(result['backup_id']+'.backup')).read_bytes()
    assert '周末'.encode() not in backup
    assert cleanup.restore(result['backup_id'])['restored']==1
    assert store.get('safe')['body']=='祝大家周末愉快'
    assert cleanup.restore(result['backup_id'])['conflicts']==1


@pytest.mark.parametrize('changes',[
    dict(body='请核对付款'),dict(subject='Invoice attached'),dict(body='参考号 ABC123'),
    dict(body='请查银行账号'),dict(read_complete=False),dict(outlook_read_version=0),
    dict(attachment_warning='failed'),dict(truncated=True),dict(candidates=['someone']),
    dict(attachments=['proof.pdf']),dict(body='',subject=''),
    dict(_attachments=[dict(name='a.png',raw=b'raw',status='done')]),
])
def test_uncertain_and_financial_mail_never_candidates(tmp_path,changes):
    store=MailStore(tmp_path/'mail.sqlite3');add(store,**changes)
    assert MailCleanup(store).preview()['candidates']==[]


def test_stale_preview_and_new_payment_roll_back_entire_batch(tmp_path):
    store=MailStore(tmp_path/'mail.sqlite3');add(store,'a');add(store,'b')
    cleanup=MailCleanup(store);preview=cleanup.preview()
    store.set_payments('b',[dict(id='payment',amount=20)])
    with pytest.raises(ValueError):cleanup.delete(preview['candidates'],['a','b'])
    assert store.mail_count()==2


def test_backup_failure_never_deletes_mail(tmp_path,monkeypatch):
    from pathlib import Path
    store=MailStore(tmp_path/'mail.sqlite3');add(store)
    cleanup=MailCleanup(store);preview=cleanup.preview()
    original=Path.open
    def fail(path,*args,**kwargs):
        if path.suffix=='.backup':raise OSError('disk full')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',fail)
    with pytest.raises(OSError):cleanup.delete(preview['candidates'],['safe'])
    assert store.get('safe')


def test_review_and_corruption_fail_closed(tmp_path):
    from app.mail_workspace import MailWorkspace
    store=MailStore(tmp_path/'mail.sqlite3');add(store);MailWorkspace(store)
    with store.db() as db:db.execute('INSERT INTO mail_review VALUES(?,?)',('receipt',store.encode(dict(mail_id='safe',state='rejected'))))
    assert not MailCleanup(store).preview()['candidates']
    with store.db() as db:db.execute('UPDATE mail_review SET payload=?',('broken',))
    result=MailCleanup(store).preview()
    assert not result['candidates'] and result['errors']==1


def test_restore_never_overwrites_reread_mail(tmp_path):
    store=MailStore(tmp_path/'mail.sqlite3');add(store)
    cleanup=MailCleanup(store);preview=cleanup.preview();result=cleanup.delete(preview['candidates'],['safe'])
    add(store,body='新邮件内容')
    assert cleanup.restore(result['backup_id'])['conflicts']==1
    assert store.get('safe')['body']=='新邮件内容'


def test_cleanup_api_requires_preview_confirmation_and_returns_recovery(tmp_path,monkeypatch):
    import time
    from fastapi.testclient import TestClient
    from app.main import app
    from app import mail_tasks
    store=MailStore(tmp_path/'mail.sqlite3');add(store)
    manager=mail_tasks.MailManager(store,reader=lambda *x:dict(rows=[],errors=[],partial=False))
    monkeypatch.setattr(mail_tasks,'_manager',manager)
    client=TestClient(app);headers={'X-Finance-Local':'1'}
    endpoint='/api/mail/storage/cleanup'
    assert client.post(endpoint+'/preview').status_code==403
    assert client.post(endpoint+'/execute',headers=headers,json=dict(confirmed=False,preview_id='none',selected=['safe'])).status_code==400
    assert client.post(endpoint+'/execute',headers=headers,json=dict(confirmed=True,preview_id='none',selected=['safe'])).status_code==409
    def done():
        for _ in range(300):
            job=manager.maintenance_job()
            if job.get('status')!='running':return job
            time.sleep(.01)
        raise AssertionError('cleanup timeout')
    assert client.post(endpoint+'/preview',headers=headers).status_code==202
    preview=done();assert preview['result']['candidates'][0]['id']=='safe'
    assert client.post(endpoint+'/execute',headers=headers,json=dict(confirmed=True,preview_id=preview['id'],selected=['safe'])).status_code==202
    job=done();assert job['status']=='completed' and not store.get('safe')
    assert client.post(endpoint+'/restore',headers=headers,json=dict(backup_id=job['result']['backup_id'])).status_code==202
    assert done()['result']['restored']==1 and store.get('safe')


def test_unrelated_resolved_review_does_not_block_safe_candidates(tmp_path):
    from app.mail_workspace import MailWorkspace
    store=MailStore(tmp_path/'mail.sqlite3');add(store);add(store,'payment',body='payment')
    store.set_payments('payment',[dict(id='known-payment',mail_id='payment',amount=20)])
    MailWorkspace(store)
    with store.db() as db:db.execute('INSERT INTO mail_review VALUES(?,?)',('receipt',store.encode(dict(payment_id='known-payment',state='confirmed'))))
    assert [r['id'] for r in MailCleanup(store).preview()['candidates']]==['safe']
    with store.db() as db:db.execute('UPDATE mail_review SET payload=?',(store.encode(dict(payment_id='unresolved',state='confirmed')),))
    assert not MailCleanup(store).preview()['candidates']


def test_queue_entries_and_all_attachment_states_are_protected(tmp_path):
    from app.mail_storage import AnalysisQueue
    store=MailStore(tmp_path/'mail.sqlite3');add(store)
    AnalysisQueue(store).append(('safe',False))
    assert not MailCleanup(store).preview()['candidates']
    for status in ('done','failed','skipped','running','pending'):
        add(store,status,_attachments=[dict(name='proof.png',raw=b'original',status=status)])
    assert not MailCleanup(store).preview()['candidates']


def test_backups_remain_discoverable_and_count_toward_storage(tmp_path):
    from app.mail_storage import MailStorage
    store=MailStore(tmp_path/'mail.sqlite3');add(store)
    cleanup=MailCleanup(store);preview=cleanup.preview();result=cleanup.delete(preview['candidates'],['safe'])
    backups=cleanup.backups()
    assert backups[0]['backup_id']==result['backup_id']
    assert MailStorage(store).snapshot()['cleanup_backup_bytes']==result['backup_bytes']
    assert MailStorage(store).used_bytes()>=store.path.stat().st_size+result['backup_bytes']
    with pytest.raises(ValueError):cleanup.restore('../outside')
    (cleanup.directory/(result['backup_id']+'.backup')).write_bytes(b'broken')
    with pytest.raises(ValueError,match='无法解密'):cleanup.restore(result['backup_id'])
    assert not store.get('safe')


def test_delete_failure_rolls_back_all_mail_and_keeps_backup(tmp_path):
    import sqlite3
    store=MailStore(tmp_path/'mail.sqlite3');add(store,'a');add(store,'b')
    cleanup=MailCleanup(store);preview=cleanup.preview()
    with store.db() as db:db.execute("CREATE TRIGGER forbid_delete BEFORE DELETE ON mail BEGIN SELECT RAISE(ABORT, 'blocked'); END")
    with pytest.raises(sqlite3.IntegrityError):cleanup.delete(preview['candidates'],['a','b'])
    assert store.mail_count()==2
    assert len(cleanup.backups())==1
    assert cleanup.restore(cleanup.backups()[0]['backup_id'])['conflicts']==2
