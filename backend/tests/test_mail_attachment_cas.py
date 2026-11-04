from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import MagicMock
import hashlib
import pytest
from app.outlook_probe import MailStore, StaleAttachmentError
from app.mail_tasks import MailManager
from app.mail_recognition import RecognitionCache
from app.mail_failures import AnalysisFailure, describe_failure


def save(store, raws):
    store.save([dict(id='mail', body='', attachments=[f'{i}.png' for i in range(len(raws))],
                     _attachments=[dict(name=f'{i}.png', raw=raw, status='pending') for i,raw in enumerate(raws)])])


def manager(store):
    obj=MailManager.__new__(MailManager)
    obj.store=store
    obj.recognition_cache=RecognitionCache(store)
    obj.performance=MagicMock()
    return obj


def result(text):
    return dict(pages=[dict(text=text,lines=[],fields={})], fields={}, warnings=[])


@pytest.mark.parametrize('failure', [False, True])
def test_replacement_during_ocr_does_not_save_old_result_or_payments(tmp_path, failure):
    store=MailStore(tmp_path/'mail.db'); save(store,[b'old'])
    obj=manager(store); entered=Event(); release=Event()
    def analyze(raw,name):
        entered.set(); assert release.wait(5)
        if failure: raise ValueError('mock OCR failure')
        return result('old')
    with ThreadPoolExecutor() as worker:
        pending=worker.submit(obj._analyze_mail,'mail',False,analyze)
        try:
            assert entered.wait(5)
            save(store,[b'new'])
        finally: release.set()
        with pytest.raises(AnalysisFailure) as caught: pending.result(timeout=5)
    assert isinstance(caught.value.cause,StaleAttachmentError)
    assert describe_failure(caught.value)['retryable']
    asset=store.get('mail')['attachment_details'][0]
    assert asset['status']=='pending' and not asset.get('result')
    assert store.payment_index(['mail'])=={}
    with store.db(write=False) as db:
        assert db.execute('SELECT COUNT(*) FROM mail_recognition_lookup').fetchone()[0]==0
    obj._analyze_mail('mail',False,lambda raw,name:result(raw.decode()))
    assert store.get('mail')['attachment_details'][0]['result']['pages'][0]['text']=='new'


def test_new_attachment_during_ocr_keeps_mail_for_retry(tmp_path):
    store=MailStore(tmp_path/'mail.db'); save(store,[b'first'])
    obj=manager(store); entered=Event(); release=Event()
    def analyze(raw,name):
        entered.set(); assert release.wait(5)
        return result('first')
    with ThreadPoolExecutor() as worker:
        pending=worker.submit(obj._analyze_mail,'mail',False,analyze)
        try:
            assert entered.wait(5)
            save(store,[b'first',b'added'])
        finally: release.set()
        with pytest.raises(AnalysisFailure) as caught: pending.result(timeout=5)
    assert isinstance(caught.value.cause,StaleAttachmentError)
    assert store.payment_index(['mail'])=={}
    assert store.get('mail')['attachment_details'][1]['status']=='pending'
    seen=[]
    obj._analyze_mail('mail',False,lambda raw,name:seen.append(raw) or result('added'))
    assert seen==[b'added']
    assert all(a['status']=='done' for a in store.get('mail')['attachment_details'])


@pytest.mark.parametrize('status',['running','done','failed'])
def test_all_status_updates_compare_raw_digest(tmp_path,status):
    store=MailStore(tmp_path/'mail.db'); save(store,[b'old'])
    identity=store.get('mail')['attachment_details'][0]['id']
    save(store,[b'new'])
    with pytest.raises(StaleAttachmentError):
        store.update_attachment('mail',identity,expected_sha256=hashlib.sha256(b'old').hexdigest(),status=status)
    assert store.get('mail')['attachment_details'][0]['status']=='pending'


def test_existing_failed_is_not_stale_retry_trigger(tmp_path):
    store=MailStore(tmp_path/'mail.db'); save(store,[b'bad'])
    identity=store.get('mail')['attachment_details'][0]['id']
    store.update_attachment('mail',identity,status='failed')
    obj=manager(store)
    obj._analyze_mail('mail',False,lambda *args:pytest.fail('failed attachment retried'))


def test_payment_snapshot_change_rejects_stale_summary(tmp_path,monkeypatch):
    from app import mail_matching
    store=MailStore(tmp_path/'mail.db'); save(store,[b'first'])
    obj=manager(store); entered=Event(); release=Event()
    original=mail_matching.build_payments
    def build(row):
        entered.set(); assert release.wait(5)
        return original(row)
    monkeypatch.setattr(mail_matching,'build_payments',build)
    with ThreadPoolExecutor() as worker:
        pending=worker.submit(obj._analyze_mail,'mail',False,lambda *args:result('first'))
        try:
            assert entered.wait(5)
            save(store,[b'first',b'added'])
        finally: release.set()
        with pytest.raises(AnalysisFailure) as caught:pending.result(timeout=5)
    assert isinstance(caught.value.cause,StaleAttachmentError)
    assert store.payment_index(['mail'])=={}
    assert store.payment_summaries(['mail'])=={}


def test_stale_queue_is_released_and_retries_are_bounded(tmp_path,monkeypatch):
    monkeypatch.setattr(MailManager,'_start_analysis_thread',lambda self:None)
    store=MailStore(tmp_path/'mail.db'); save(store,[b'first'])
    attempts=[]
    def analyze(raw,name):
        attempts.append(raw)
        # Every attempt races with a fresh read; finite controller budget must halt.
        save(store,[f'new-{len(attempts)}'.encode()])
        return result('stale')
    obj=MailManager(store,analyzer=analyze,analysis_workers=1,analysis_retry_limit=2,retry_wait=lambda _:None)
    obj.storage.block_reason=lambda **kwargs:None
    obj._skip_bad_mail=MagicMock(side_effect=AssertionError('stale must not be skipped'))
    obj._analyze_queue()
    assert len(attempts)==3
    assert obj.state['analysis_halted'] is True
    assert obj.state['analysis_skipped']==0
    obj._skip_bad_mail.assert_not_called()
    assert len(obj.analysis_queue)==1
    with store.db(write=False) as db:
        assert db.execute('SELECT claimed FROM mail_analysis_queue WHERE mail_id=?',('mail',)).fetchone()==(0,)
    assert store.get('mail')['attachment_details'][0]['status']=='pending'
    assert store.payment_index(['mail'])=={}


def test_new_attachment_queue_retries_and_ack_only_after_completion(tmp_path,monkeypatch):
    monkeypatch.setattr(MailManager,'_start_analysis_thread',lambda self:None)
    store=MailStore(tmp_path/'mail.db'); save(store,[b'first'])
    seen=[]
    def analyze(raw,name):
        seen.append(raw)
        if raw==b'first':save(store,[b'first',b'added'])
        return result(raw.decode())
    obj=MailManager(store,analyzer=analyze,analysis_workers=1,analysis_retry_limit=2,retry_wait=lambda _:None)
    obj.storage.block_reason=lambda **kwargs:None
    obj._analyze_queue()
    assert seen==[b'first',b'added']
    assert len(obj.analysis_queue)==0
    assert not obj.state['analysis_halted']
    assert all(a['status']=='done' for a in store.get('mail')['attachment_details'])


@pytest.mark.parametrize('replacement',[False,True])
def test_attachment_arrives_after_process_returns_before_ack(tmp_path,monkeypatch,replacement):
    monkeypatch.setattr(MailManager,'_start_analysis_thread',lambda self:None)
    store=MailStore(tmp_path/'mail.db'); save(store,[b'first'])
    seen=[]
    obj=MailManager(store,analyzer=lambda raw,name:seen.append(raw) or result(raw.decode()),
                    analysis_workers=1,analysis_retry_limit=2,retry_wait=lambda _:None)
    obj.storage.block_reason=lambda **kwargs:None
    processed=Event(); release=Event()
    original=obj._analyze_mail
    calls=[]
    def after_process(*args):
        value=original(*args)
        calls.append(1)
        if len(calls)==1:
            processed.set(); assert release.wait(5)
        return value
    obj._analyze_mail=after_process
    obj._skip_bad_mail=MagicMock(side_effect=AssertionError('stale must not be skipped'))
    with ThreadPoolExecutor() as worker:
        drain=worker.submit(obj._analyze_queue)
        try:
            assert processed.wait(5)
            with obj.lock:
                save(store,[b'new'] if replacement else [b'first',b'new'])
                obj.analyze('mail')  # analysis_current suppresses append: old ack must not erase this work.
        finally:release.set()
        drain.result(timeout=10)
    assert seen==[b'first',b'new']
    assert len(calls)==2
    assert len(obj.analysis_queue)==0
    assert all(a['status']=='done' for a in store.get('mail')['attachment_details'])
    obj._skip_bad_mail.assert_not_called()


def test_repeated_changes_after_process_before_ack_are_bounded(tmp_path,monkeypatch):
    monkeypatch.setattr(MailManager,'_start_analysis_thread',lambda self:None)
    store=MailStore(tmp_path/'mail.db'); save(store,[b'first'])
    obj=MailManager(store,analyzer=lambda raw,name:result(raw.decode()),
                    analysis_workers=1,analysis_retry_limit=1,retry_wait=lambda _:None)
    obj.storage.block_reason=lambda **kwargs:None
    original=obj._analyze_mail
    calls=[]
    def after_process(*args):
        value=original(*args)
        calls.append(1)
        with obj.lock:save(store,[f'new-{len(calls)}'.encode()])
        return value
    obj._analyze_mail=after_process
    obj._skip_bad_mail=MagicMock()
    obj._analyze_queue()
    assert len(calls)==2
    assert obj.state['analysis_halted']
    assert len(obj.analysis_queue)==1
    assert store.get('mail')['attachment_details'][0]['status']=='pending'
    obj._skip_bad_mail.assert_not_called()


def test_stale_has_specific_retryable_classification_without_database_busy_label():
    info=describe_failure(AnalysisFailure(StaleAttachmentError('changed'),'保存附件识别结果'))
    assert info['kind']=='attachment_changed'
    assert info['retryable'] is True
    assert '数据库' not in info['message']
    assert info['phase']=='保存附件识别结果'


def test_poll_save_releases_manager_lock_and_keeps_queue_claim(tmp_path,monkeypatch):
    monkeypatch.setattr(MailManager,'_start_analysis_thread',lambda self:None)
    store=MailStore(tmp_path/'mail.db')
    row=dict(id='mail',body='',attachments=['0.png'],
             _attachments=[dict(name='0.png',raw=b'new',status='pending')])
    obj=MailManager(store,reader=lambda *args:dict(rows=[row],errors=[],partial=False),
                    analyzer=lambda *args:result('new'))
    original_save=store.save
    original_append=obj.analysis_queue.append
    calls=[]
    def save_locked(rows):
        assert not obj.lock._is_owned(), 'mail save must not block configuration on manager lock'
        calls.append('save')
        return original_save(rows)
    def append_locked(item):
        assert obj.lock._is_owned(), 'mail enqueue must serialize with ack check'
        calls.append('append')
        return original_append(item)
    monkeypatch.setattr(store,'save',save_locked)
    monkeypatch.setattr(obj.analysis_queue,'append',append_locked)
    obj._run('store','folder',90,100)
    assert not obj.state['error']
    assert calls==['save','append']
    assert len(obj.analysis_queue)==1


def test_empty_original_does_not_permanently_block_queue(tmp_path):
    store=MailStore(tmp_path/'mail.db'); save(store,[b''])
    obj=manager(store)
    obj._analyze_mail('mail',False,lambda *args:pytest.fail('empty original reached OCR'))
    asset=store.get('mail')['attachment_details'][0]
    assert asset['status']=='failed'
    assert '原件' in asset['error']
    assert 'mail' in store.payment_index(['mail'])
