from app.outlook_probe import MailStore
from app.mail_storage import MailStorage, AnalysisQueue


def test_storage_settings_survive_reopen_and_keep_mail(tmp_path):
    store=MailStore(tmp_path/'mail.sqlite3')
    store.save([dict(id='old',body='历史正文')])
    storage=MailStorage(store)
    storage.configure(dict(max_gb=12,min_free_gb=2,queue_limit=200))
    reopened=MailStorage(MailStore(store.path))
    assert reopened.settings()['max_gb']==12
    assert reopened.snapshot()['mail_count']==1
    assert store.get('old')['body']=='历史正文'


def test_guard_blocks_quota_and_low_disk_without_deleting(tmp_path,monkeypatch):
    store=MailStore(tmp_path/'mail.sqlite3');storage=MailStorage(store)
    monkeypatch.setattr(storage,'disk_free',lambda:0)
    assert '磁盘' in storage.block_reason()
    monkeypatch.setattr(storage,'disk_free',lambda:100*1024**3)
    assert '上限' in storage.block_reason(reserve_bytes=11*1024**3)
    assert not storage.block_reason()


def test_queue_is_durable_deduplicated_and_claim_recovered(tmp_path):
    store=MailStore(tmp_path/'mail.sqlite3');queue=AnalysisQueue(store)
    queue.append(('one',False));queue.append(('one',False));queue.append(('two',True))
    assert len(queue)==2
    assert queue.popleft()==('one',False)
    # A crash before acknowledgement leaves the item recoverable.
    reopened=AnalysisQueue(MailStore(store.path))
    assert reopened.popleft()==('one',False)
    reopened.done('one')
    assert reopened.popleft()==('two',True)
    reopened.done('two')
    assert not reopened


def test_manager_does_not_read_or_advance_when_disk_is_low(tmp_path,monkeypatch):
    from app.mail_tasks import MailManager
    store=MailStore(tmp_path/'mail.sqlite3');calls=[]
    manager=MailManager(store,reader=lambda *args:calls.append(args))
    store.set_state('sync_progress',dict(cursor='keep'))
    monkeypatch.setattr(manager.storage,'disk_free',lambda:0)
    manager._run('store','folder',90,100)
    assert not calls
    assert '磁盘' in manager.status()['error']
    assert store.get_state('sync_progress')['cursor']=='keep'


def test_manager_applies_queue_backpressure_without_losing_cursor(tmp_path,monkeypatch):
    from app.mail_tasks import MailManager
    store=MailStore(tmp_path/'mail.sqlite3');calls=[]
    manager=MailManager(store,reader=lambda *args:calls.append(args))
    monkeypatch.setattr(manager.storage,'disk_free',lambda:100*1024**3)
    manager.storage.configure(dict(queue_limit=100))
    for i in range(100):manager.analysis_queue.append((str(i),False))
    manager._run('store','folder',90,100)
    assert not calls
    assert len(manager.analysis_queue)==100
    assert '队列' in manager.status()['error']
