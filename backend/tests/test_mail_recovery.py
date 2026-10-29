import errno
import sqlite3
import time
from concurrent.futures.process import BrokenProcessPool
from app.mail_tasks import MailManager
from app.outlook_probe import MailStore


def idle(manager):
    end=time.monotonic()+10
    while manager.status()['analysis_busy'] and time.monotonic()<end:time.sleep(.01)
    assert not manager.status()['analysis_busy']


def manager_for(tmp_path, monkeypatch, build, limit=3):
    from app import mail_matching
    monkeypatch.setattr('app.mail_tasks.configured_workers',lambda _:1)
    monkeypatch.setattr(mail_matching,'build_payments',build)
    store=MailStore(tmp_path/'mail.sqlite3');store.save([dict(id='one',body='preserved')])
    return MailManager(store,analyzer=lambda *a:None,analysis_retry_limit=limit,retry_wait=lambda _:None)


def test_temporary_database_busy_recovers_without_losing_mail(tmp_path,monkeypatch):
    calls=[]
    def build(row):
        calls.append(row['id'])
        if len(calls)==1:raise sqlite3.OperationalError('database is locked')
        return []
    manager=manager_for(tmp_path,monkeypatch,build);idle(manager)
    assert calls==['one','one']
    assert manager.status()['analysis_pending']==0
    assert not manager.status().get('analysis_error')
    assert manager.status()['analysis_diagnostic']['status']=='recovered'
    assert manager.store.get('one')['body']=='preserved'


def test_repeated_busy_has_bounded_retries_and_clear_phase(tmp_path,monkeypatch):
    calls=[]
    def build(row):
        calls.append(1);raise sqlite3.OperationalError('database is locked')
    manager=manager_for(tmp_path,monkeypatch,build,2);idle(manager)
    assert len(calls)==3
    info=manager.status()['analysis_diagnostic']
    assert info['kind']=='database_busy' and info['phase']=='整理付款信息'
    assert info['attempt']==2 and info['status']=='paused'
    assert manager.status()['analysis_pending']==1
    assert 'database is locked' not in manager.status()['analysis_error']


def test_disk_full_is_not_retried_and_queue_is_retained(tmp_path,monkeypatch):
    calls=[]
    def build(row):
        calls.append(1);raise OSError(errno.ENOSPC,'private-path')
    manager=manager_for(tmp_path,monkeypatch,build);idle(manager)
    assert len(calls)==1
    assert manager.status()['analysis_diagnostic']['kind']=='disk_full'
    assert manager.status()['analysis_pending']==1


def test_native_worker_exit_rebuilds_pool_and_resumes(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from app import mail_tasks
    pools=[]
    class Pool(ThreadPoolExecutor):
        def __enter__(self):pools.append(self);return super().__enter__()
        def submit(self, fn, *args):
            def work():
                if len(pools)==1:raise BrokenProcessPool('child terminated')
                return dict(pages=[],fields={},warnings=[])
            return super().submit(work)
    monkeypatch.setattr(mail_tasks,'analysis_pool',lambda n:Pool(max_workers=n))
    store=MailStore(tmp_path/'mail.sqlite3')
    store.save([dict(id='one',body='',_attachments=[dict(name='one.png',raw=b'image',status='pending')])])
    manager=MailManager(store,retry_wait=lambda _:None);idle(manager)
    assert len(pools)==2
    assert manager.status()['analysis_pending']==0
    assert store.get('one')['attachment_details'][0]['status']=='done'
    assert manager.status()['analysis_diagnostic']['kind']=='worker_pool_broken'
    assert manager.status()['analysis_diagnostic']['status']=='recovered'


def test_status_stays_readable_when_database_status_query_fails(tmp_path,monkeypatch):
    from contextlib import contextmanager
    store=MailStore(tmp_path/'mail.sqlite3')
    manager=MailManager(store)
    @contextmanager
    def locked():raise sqlite3.OperationalError('database is locked');yield
    monkeypatch.setattr(store,'db',locked)
    status=manager.status()
    assert status['status_diagnostic']['kind']=='database_busy'
    assert status['status_diagnostic']['phase']=='读取状态'


def test_periodic_read_does_not_reset_exhausted_retry_budget(tmp_path,monkeypatch):
    def fail(row):raise sqlite3.OperationalError('database is locked')
    manager=manager_for(tmp_path,monkeypatch,fail,0);idle(manager)
    manager.state['enabled']=True
    generation=manager.generation
    # An automatic callback must preserve the analysis halt, not create a fresh
    # retry budget every polling interval. Use a no-op reader timer in this test.
    monkeypatch.setattr(manager,'_run',lambda *a,**kw:manager.state.update(busy=False))
    manager.start('s','f',90,100,generation=generation)
    idle(manager)
    assert manager.state['analysis_halted']
    assert manager.state['analysis_diagnostic']['status']=='paused'


def test_storage_endpoint_reports_full_disk_and_phase(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app import mail_tasks
    manager=MailManager(MailStore(tmp_path/'mail.sqlite3'))
    monkeypatch.setattr(mail_tasks,'_manager',manager)
    def full():raise OSError(errno.ENOSPC,'private-path')
    monkeypatch.setattr(manager.storage,'snapshot',full)
    response=TestClient(app).get('/api/mail/storage',headers={'X-Finance-Local':'1'})
    assert response.status_code==503
    detail=response.json()['detail']
    assert '查询邮件存储占用' in detail and '存储空间不足' in detail
    assert 'private-path' not in detail


def test_manual_read_can_recover_when_all_claim_releases_failed(tmp_path,monkeypatch):
    from app.mail_storage import AnalysisQueue
    from app import mail_matching
    original=AnalysisQueue.release
    monkeypatch.setattr(AnalysisQueue,'release',lambda *a:(_ for _ in ()).throw(sqlite3.OperationalError('database is locked')))
    manager=manager_for(tmp_path,monkeypatch,lambda _:(_ for _ in ()).throw(sqlite3.OperationalError('database is locked')),0)
    idle(manager)
    assert manager.analysis_current=={'one'} and not manager.analysis_queue
    monkeypatch.setattr(AnalysisQueue,'release',original)
    monkeypatch.setattr(mail_matching,'build_payments',lambda _:[])
    manager.reader=lambda *a:dict(rows=[],errors=[],partial=False)
    manager.paged_reader=None
    manager.start('s','f',90,100);idle(manager)
    assert manager.status()['analysis_pending']==0
    assert not manager.state['analysis_error']


def test_disk_full_wins_over_concurrent_retryable_failure(tmp_path,monkeypatch):
    from app import mail_matching
    from threading import Barrier
    barrier=Barrier(2);calls=[]
    def build(row):
        calls.append(row['id']);barrier.wait(timeout=3)
        if row['id']=='busy':raise sqlite3.OperationalError('database is locked')
        time.sleep(.05);raise OSError(errno.ENOSPC,'private')
    monkeypatch.setattr(mail_matching,'build_payments',build)
    monkeypatch.setattr('app.mail_tasks.configured_workers',lambda _:2)
    store=MailStore(tmp_path/'mail.sqlite3');store.save([dict(id='busy'),dict(id='full')])
    manager=MailManager(store,analyzer=lambda *a:None,retry_wait=lambda _:None);idle(manager)
    assert len(calls)==2
    assert manager.state['analysis_diagnostic']['kind']=='disk_full'
    assert manager.status()['analysis_pending']==2


def test_final_resume_failure_gets_a_diagnosis(tmp_path,monkeypatch):
    manager=MailManager(MailStore(tmp_path/'mail.sqlite3'),analysis_retry_limit=0)
    monkeypatch.setattr(manager,'_drain_analysis_queue',lambda:True)
    monkeypatch.setattr(manager,'_resume_after_backpressure',lambda:(_ for _ in ()).throw(sqlite3.OperationalError('database is locked')))
    manager._analyze_queue()
    assert manager.state['analysis_diagnostic']['phase']=='恢复自动读取'
