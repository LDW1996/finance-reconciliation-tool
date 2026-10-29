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


def test_disappearing_sqlite_sidecar_does_not_pause_storage_guard(tmp_path,monkeypatch):
    from pathlib import Path
    store=MailStore(tmp_path/'mail.sqlite3');storage=MailStorage(store)
    journal=Path(str(store.path)+'-journal');journal.write_bytes(b'temporary')
    original=Path.stat;calls=0
    def race(path,*args,**kwargs):
        nonlocal calls
        if path==journal:
            calls+=1
            if calls==1:
                result=original(path,*args,**kwargs);path.unlink();return result
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'stat',race)
    monkeypatch.setattr(storage,'disk_free',lambda:100*1024**3)
    assert storage.database_bytes()>=original(store.path).st_size
    assert not storage.block_reason()


def test_disappearing_preview_does_not_pause_storage_guard(tmp_path,monkeypatch):
    from app.mail_assets import PreviewCache
    store=MailStore(tmp_path/'mail.sqlite3');storage=MailStorage(store)
    cache=PreviewCache(store);cache.directory.mkdir();path=cache.directory/'old.cache'
    path.write_bytes(b'preview')
    def removed_files(self):
        files=[path];path.unlink(missing_ok=True);return files
    monkeypatch.setattr(PreviewCache,'files',removed_files)
    monkeypatch.setattr(storage,'disk_free',lambda:100*1024**3)
    assert not storage.block_reason()
    assert cache.clear()==0
    cache.prune()


def test_primary_database_missing_is_not_silently_ignored(tmp_path):
    import pytest
    store=MailStore(tmp_path/'mail.sqlite3');storage=MailStorage(store)
    store.path.unlink()
    with pytest.raises(FileNotFoundError):storage.database_bytes()


def test_storage_permission_errors_are_not_silently_ignored(tmp_path,monkeypatch):
    import pytest
    from pathlib import Path
    store=MailStore(tmp_path/'mail.sqlite3');storage=MailStorage(store)
    original=Path.stat
    def denied(path,*args,**kwargs):
        if path==store.path:raise PermissionError(13,'denied')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'stat',denied)
    with pytest.raises(PermissionError):storage.database_bytes()


def test_optional_sidecar_permission_failure_is_not_hidden(tmp_path,monkeypatch):
    import pytest
    from pathlib import Path
    store=MailStore(tmp_path/'mail.sqlite3');storage=MailStorage(store)
    journal=Path(str(store.path)+'-journal');journal.write_bytes(b'temporary')
    original=Path.stat
    def denied(path,*args,**kwargs):
        if path==journal:raise PermissionError(13,'denied')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'stat',denied)
    with pytest.raises(PermissionError):storage.database_bytes()


def test_missing_path_has_specific_actionable_diagnostic():
    from app.mail_failures import describe_failure
    error=describe_failure(FileNotFoundError(2,'missing'),'检查存储空间')
    assert error['kind']=='storage_path_missing'
    assert not error['retryable']
    assert '数据目录' in error['action']
