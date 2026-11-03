from concurrent.futures import ThreadPoolExecutor
import multiprocessing
from time import monotonic
import pytest
from app import mail_workers
from ocr_spawn_helpers import hanging_attachment


def test_real_spawn_timeout_kills_replaces_and_reaps_worker():
    before={p.pid for p in multiprocessing.active_children()}
    started=monotonic()
    with mail_workers.analysis_pool(1,timeout_seconds=1,initializer=None) as pool:
        with pytest.raises(mail_workers.AttachmentOCRTimeout):
            pool.submit(hanging_attachment,b'hang','strip.png').result(timeout=8)
        result=pool.submit(hanging_attachment,b'healthy','receipt.png').result(timeout=8)
        assert result['pages'][0]['text']=='healthy'
        assert result['pid'] not in before
    assert monotonic()-started<10
    assert not [p for p in multiprocessing.active_children() if p.pid not in before]


def test_spawn_hang_does_not_kill_other_slot_and_close_is_bounded():
    before={p.pid for p in multiprocessing.active_children()}
    with mail_workers.analysis_pool(2,timeout_seconds=1,initializer=None) as pool:
        hanging=pool.submit(hanging_attachment,b'hang','strip.png')
        healthy=pool.submit(hanging_attachment,b'healthy','receipt.png')
        result=healthy.result(timeout=8)
        assert result['pages'][0]['text']=='healthy'
        with pytest.raises(mail_workers.AttachmentOCRTimeout):hanging.result(timeout=8)
    assert not [p for p in multiprocessing.active_children() if p.pid not in before]


def test_close_terminates_running_spawn_without_waiting_for_timeout():
    before={p.pid for p in multiprocessing.active_children()}
    pool=mail_workers.analysis_pool(1,timeout_seconds=120,initializer=None)
    future=pool.submit(hanging_attachment,b'hang','strip.png')
    started=monotonic()
    pool.shutdown(wait=True)
    assert monotonic()-started<5
    assert future.done()
    assert not [p for p in multiprocessing.active_children() if p.pid not in before]


def test_spawn_hung_attachment_failed_after_one_retry_other_mail_continues(tmp_path,monkeypatch):
    from app import mail_tasks
    from app.outlook_probe import MailStore
    monkeypatch.setattr(mail_tasks.MailManager,'_start_analysis_thread',lambda self:None)
    monkeypatch.setattr(mail_tasks,'analyze_attachment',hanging_attachment)
    monkeypatch.setattr(mail_tasks,'analysis_pool',lambda workers:mail_workers.analysis_pool(
        workers,timeout_seconds=1,initializer=None))
    store=MailStore(tmp_path/'mail.db')
    store.save([dict(id='mail',body='',attachments=['hang.png','good.png'],_attachments=[
        dict(name='hang.png',raw=b'hang',status='pending'),
        dict(name='good.png',raw=b'healthy',status='pending')])])
    obj=mail_tasks.MailManager(store,analyzer=hanging_attachment,analysis_workers=1,retry_wait=lambda _:None)
    obj.storage.block_reason=lambda **kwargs:None
    before={p.pid for p in multiprocessing.active_children()}
    obj._analyze_queue()
    assets=store.get('mail')['attachment_details']
    assert assets[0]['status']=='failed'
    assert assets[0]['ocr_timeout_attempts']==2
    assert '两次超时' in assets[0]['error']
    assert assets[1]['status']=='done'
    assert len(obj.analysis_queue)==0
    assert not obj.state['analysis_halted']
    assert not [p for p in multiprocessing.active_children() if p.pid not in before]


def test_spawn_retry_recovers_after_first_timeout(tmp_path,monkeypatch):
    from app import mail_tasks
    from app.outlook_probe import MailStore
    from ocr_spawn_helpers import hang_once_attachment
    monkeypatch.setattr(mail_tasks.MailManager,'_start_analysis_thread',lambda self:None)
    monkeypatch.setattr(mail_tasks,'analyze_attachment',hang_once_attachment)
    monkeypatch.setattr(mail_tasks,'analysis_pool',lambda workers:mail_workers.analysis_pool(
        workers,timeout_seconds=1,initializer=None))
    store=MailStore(tmp_path/'mail.db')
    marker=tmp_path/'first-attempt'
    store.save([dict(id='mail',body='',attachments=['receipt.png'],_attachments=[
        dict(name='receipt.png',raw=str(marker).encode(),status='pending')])])
    obj=mail_tasks.MailManager(store,analyzer=hang_once_attachment,analysis_workers=1,retry_wait=lambda _:None)
    obj.storage.block_reason=lambda **kwargs:None
    obj._analyze_queue()
    asset=store.get('mail')['attachment_details'][0]
    assert asset['status']=='done'
    assert asset['ocr_timeout_attempts']==1
    assert len(obj.analysis_queue)==0


def test_real_spawn_unexpected_exit_is_retryable_and_slot_reusable():
    from concurrent.futures.process import BrokenProcessPool
    from ocr_spawn_helpers import exited_worker
    before={p.pid for p in multiprocessing.active_children()}
    with mail_workers.analysis_pool(1,timeout_seconds=2,initializer=None) as pool:
        with pytest.raises(BrokenProcessPool):
            pool.submit(exited_worker,b'', 'crash.png').result(timeout=8)
        assert pool.submit(hanging_attachment,b'healthy','ok.png').result(timeout=8)['pages'][0]['text']=='healthy'
    assert not [p for p in multiprocessing.active_children() if p.pid not in before]


def test_explicit_manual_retry_renews_timeout_budget_once(tmp_path,monkeypatch):
    from app import mail_tasks
    from app.outlook_probe import MailStore
    monkeypatch.setattr(mail_tasks.MailManager,'_start_analysis_thread',lambda self:None)
    store=MailStore(tmp_path/'mail.db')
    store.save([dict(id='mail',body='',_attachments=[dict(name='receipt.png',raw=b'image',status='pending')])])
    calls=[]
    def timeout(*args):
        calls.append(1)
        raise mail_workers.AttachmentOCRTimeout('mock timeout')
    obj=mail_tasks.MailManager(store,analyzer=timeout,retry_wait=lambda _:None)
    obj.storage.block_reason=lambda **kwargs:None
    obj._analyze_queue()
    assert len(calls)==2
    assert store.get('mail')['attachment_details'][0]['ocr_timeout_attempts']==2
    obj.analyze('mail',retry=True)
    assert store.get('mail')['attachment_details'][0]['ocr_timeout_attempts']==0
    obj.analyzer=lambda *args:dict(pages=[],fields={},warnings=[])
    obj._analyze_queue()
    assert store.get('mail')['attachment_details'][0]['status']=='done'


def test_timeout_erases_temporary_original_before_future_returns():
    with mail_workers.analysis_pool(1,timeout_seconds=1,initializer=None) as pool:
        with pytest.raises(mail_workers.AttachmentOCRTimeout):
            pool.submit(hanging_attachment,b'hang','private-name.png').result(timeout=8)
        assert not list(pool.directory.rglob('input'))
        assert not list(pool.directory.rglob('output*'))


def test_unstoppable_slot_preserves_files_and_process_handle_until_reaped(monkeypatch):
    from unittest.mock import Mock
    from concurrent.futures.process import BrokenProcessPool
    pool=mail_workers.analysis_pool(1,timeout_seconds=1,initializer=None)
    slot=pool.slots[0]
    process=Mock(pid=123456,exitcode=0)
    process.is_alive.return_value=True
    process.terminate.side_effect=PermissionError('mock termination denied')
    process.kill.side_effect=PermissionError('mock kill denied')
    slot['process']=process
    slot['quarantined']=True
    original=slot['directory']/'input'
    original.write_bytes(b'original')
    with pytest.raises(BrokenProcessPool):pool._run(hanging_attachment,(b'healthy','name'),{})
    assert original.read_bytes()==b'original'
    assert slot['process'] is process
    assert any(item is slot for item in mail_workers._quarantined_slots)
    process.is_alive.return_value=False
    pool.shutdown()
    process.close.assert_called_once()
    assert not any(item is slot for item in mail_workers._quarantined_slots)
    assert not pool.directory.exists()


@pytest.mark.parametrize('value,expected',[(None,600),('900',900),('30',30),('3600',3600),('0',600),('99999',600),('nan',600),('inf',600),('bad',600)])
def test_default_ocr_deadline_is_conservative_and_env_is_bounded(monkeypatch,value,expected):
    if value is None:monkeypatch.delenv('FINANCE_OCR_TIMEOUT_SECONDS',raising=False)
    else:monkeypatch.setenv('FINANCE_OCR_TIMEOUT_SECONDS',value)
    assert mail_workers.configured_timeout_seconds()==expected
    with mail_workers.analysis_pool(1,initializer=None) as pool:
        assert pool.timeout_seconds==expected


def test_failed_shutdown_keeps_quarantine_then_reaper_removes_original(monkeypatch):
    from unittest.mock import Mock
    from concurrent.futures.process import BrokenProcessPool
    pool=mail_workers.analysis_pool(1,initializer=None)
    slot=pool.slots[0]
    process=Mock(pid=123457,exitcode=0)
    process.is_alive.return_value=True
    process.terminate.side_effect=PermissionError('denied')
    process.kill.side_effect=PermissionError('denied')
    slot['process']=process
    original=slot['directory']/'input'
    original.write_bytes(b'private original')
    monkeypatch.setattr(mail_workers,'_start_quarantine_reaper',lambda:None)
    with pytest.raises(BrokenProcessPool):pool.shutdown()
    assert original.exists()
    assert slot['process'] is process
    assert slot['retired']
    process.is_alive.return_value=False
    mail_workers.reap_quarantined_workers()
    process.close.assert_called_once()
    assert not pool.directory.exists()
    assert not any(item is slot for item in mail_workers._quarantined_slots)


def test_quarantine_blocks_new_pool_and_is_not_automatically_retryable():
    from unittest.mock import Mock
    from concurrent.futures.process import BrokenProcessPool
    from app.mail_failures import describe_failure
    pool=mail_workers.analysis_pool(1,initializer=None)
    slot=pool.slots[0]
    process=Mock(pid=123458,exitcode=0)
    process.is_alive.return_value=True
    slot['process']=process
    try:
        with pytest.raises(BrokenProcessPool) as caught:pool._stop(slot)
        info=describe_failure(caught.value)
        assert info['kind']=='worker_quarantined'
        assert not info['retryable']
        assert '权限' in info['message'] and '重启' in info['message']
        with pytest.raises(BrokenProcessPool):
            with mail_workers.analysis_pool(1,initializer=None):pass
    finally:
        process.is_alive.return_value=False
        pool.shutdown()
    with mail_workers.analysis_pool(1,initializer=None):pass


def test_preexisting_pool_cannot_spawn_new_slot_while_quarantined():
    from unittest.mock import Mock
    from concurrent.futures.process import BrokenProcessPool
    first=mail_workers.analysis_pool(1,initializer=None)
    other=mail_workers.analysis_pool(1,initializer=None)
    slot=first.slots[0]
    process=Mock(pid=123459,exitcode=0)
    process.is_alive.return_value=True
    slot['process']=process
    try:
        with pytest.raises(BrokenProcessPool):first._stop(slot)
        with pytest.raises(BrokenProcessPool):
            other.submit(hanging_attachment,b'healthy','ok.png').result(timeout=8)
        assert other.slots[0]['process'] is None
    finally:
        process.is_alive.return_value=False
        first.shutdown()
        other.shutdown()
