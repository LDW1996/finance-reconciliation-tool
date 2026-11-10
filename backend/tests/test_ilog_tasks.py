import pytest
from app.ilog_tasks import TaskManager


def manager(tmp_path, runner=None):
    return TaskManager(tmp_path, runner=runner, auto_start=False)


def test_accepts_partial_numeric_and_letter_queries(tmp_path):
    m=manager(tmp_path)
    job=m.create('west','West',['082600028858','TRBT','2609','A-1/2_3'],[],'fuzzy',True)
    assert [r['bill'] for r in job['rows']]==['082600028858','TRBT','2609','A-1/2_3']


def test_confirmation_validation_and_dedup(tmp_path):
    m = manager(tmp_path)
    with pytest.raises(ValueError):
        m.create('west', 'West', ['&^BAD12345'], [], 'key', True)
    with pytest.raises(ValueError):
        m.create('west', 'West', ['TRBTSTZH2606S'], [], 'key', False)
    job = m.create('west', 'West', ['TRBTSTZH2606S', 'trbtstzh2606s'], ['TRBTSTZH2606S'], 'key', True)
    assert len(job['rows']) == 2
    assert m.create('west', 'West', ['TRBTSTZH2606S'], ['TRBTSTZH2606S'], 'key', True)['id'] == job['id']
    with pytest.raises(ValueError):
        m.create('west', 'West', ['TRBTSTHP2607S'], [], 'key', True)
    with pytest.raises(RuntimeError):
        m.create('west', 'West', ['TRBTSTHP2607S'], [], 'other', True)


def test_uncertain_continues_without_repeating(tmp_path):
    calls = []
    def runner(account, bill, kind, **kw):
        calls.append(bill)
        return {'status': 'needs_verification', 'submitted': True}
    m = manager(tmp_path, runner)
    job = m.create('west', 'West', ['TRBTSTZH2606S', 'TRBTSTHP2607S'], [], 'key', True)
    m.drain(job['id'])
    job = m.get(job['id'])
    assert job['state'] == 'completed'
    assert [r['status'] for r in job['rows']] == ['attention', 'attention']
    assert calls == ['TRBTSTZH2606S', 'TRBTSTHP2607S']
    assert m.get(job['id'])['state'] == 'completed'


def test_success_results_and_restart_recovery(tmp_path):
    m = manager(tmp_path, lambda *a, **kw: {'status':'verified_fee_unlocked','platform_success':True,'message':'ok'})
    job = m.create('west', 'West', ['TRBTSTZH2606S'], [], 'key', True)
    m.drain(job['id'])
    assert m.get(job['id'])['rows'][0]['status'] == 'success'
    job = m.create('west', 'West', [], ['TRBTSTHP2607S'], 'next', True)
    m.recover()
    assert m.get(job['id'])['state'] == 'paused'


def test_api_protection_and_idempotency(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.accounts import AccountStore
    from app import ilog_tasks
    monkeypatch.setenv('FINANCE_RECONCILE_DATA_DIR',str(tmp_path / 'accounts'))
    account = AccountStore().create('Test','test','test-secret')
    m = manager(tmp_path / 'tasks')
    monkeypatch.setattr(ilog_tasks,'_manager',m)
    monkeypatch.setattr(ilog_tasks,'runtime',lambda: {'available':True,'channel':'chrome'})
    client = TestClient(app)
    body = {'account_id':account['id'],'fee':['TRBTSTZH2606S'],'request_id':'test-key','confirmed':True}
    assert client.post('/api/ilog/tasks',json=body).status_code == 403
    headers = {'X-Finance-Local':'1','Origin':'http://127.0.0.1:5173'}
    assert client.post('/api/ilog/tasks',json={**body,'confirmed':False},headers=headers).status_code == 400
    one = client.post('/api/ilog/tasks',json=body,headers=headers)
    assert one.status_code == 201
    two = client.post('/api/ilog/tasks',json=body,headers=headers)
    assert two.json()['id'] == one.json()['id']
    assert 'test-secret' not in two.text
    assert client.get('/api/ilog/tasks/latest').json()['id'] == one.json()['id']
    task_id = one.json()['id']
    paused = client.post(f'/api/ilog/tasks/{task_id}/control',json={'action':'pause'},headers=headers)
    assert paused.json()['state']=='paused'
    cancelled = client.post(f'/api/ilog/tasks/{task_id}/control',json={'action':'cancel'},headers=headers)
    assert cancelled.json()['rows'][0]['status']=='cancelled'
    assert client.get(f'/api/ilog/tasks/{task_id}/evidence/0').status_code==404


def test_cancel_during_execution_does_not_start_next(tmp_path):
    import threading
    started, release = threading.Event(), threading.Event()
    calls = []
    def runner(account,bill,kind,**kw):
        calls.append(bill); started.set(); release.wait(3)
        return {'status':'verified_unlocked'}
    m = manager(tmp_path,runner)
    job = m.create('west','West',['TRBTSTZH2606S','TRBTSTHP2607S'],[],'key',True)
    thread = threading.Thread(target=m.drain,args=(job['id'],));thread.start()
    try:
        assert started.wait(2)
        m.control(job['id'],'cancel')
    finally:
        release.set();thread.join(4)
    result=m.get(job['id'])
    assert calls==['TRBTSTZH2606S']
    assert result['state']=='completed'
    assert [r['status'] for r in result['rows']]==['success','cancelled']


def test_restart_marks_inflight_as_uncertain(tmp_path):
    m=manager(tmp_path)
    job=m.create('west','West',['TRBTSTZH2606S','TRBTSTHP2607S'],[],'key',True)
    job['rows'][0]['status']='processing'
    with m.db() as db: m.save(db,job)
    reopened=manager(tmp_path)
    result=reopened.get(job['id'])
    assert result['state']=='paused'
    assert [r['status'] for r in result['rows']]==['attention','waiting']


def test_fallback_only_after_confirmed_empty_query(tmp_path):
    calls=[]
    def runner(account,bill,kind,**kw):
        calls.append(account)
        return {'status':'not_found','submitted':False} if account=='east' else {'status':'verified_fee_unlocked','submitted':True}
    m=manager(tmp_path,runner)
    job=m.create('east','East',['TRBTSTHP2607S'],[],'key',True,fallback_account='west',fallback_name='West')
    m.drain(job['id'])
    result=m.get(job['id'])['rows'][0]
    assert calls==['east','west']
    assert result['status']=='success' and result['actualAccount']=='west'
    assert len(result['attempts'])==2


@pytest.mark.parametrize('result',[
    {'status':'ambiguous','submitted':False},
    {'status':'needs_verification','submitted':False},
    {'status':'rejected','submitted':True},
    {'status':'not_found','submitted':True},
    {'status':'already_unlocked','submitted':False},
])
def test_fallback_does_not_mask_errors_or_existing_match(tmp_path,result):
    calls=[]
    def runner(account,*args,**kw): calls.append(account); return result
    m=manager(tmp_path,runner)
    job=m.create('east','East',['TRBTSTHP2607S'],[],'key',True,fallback_account='west',fallback_name='West')
    m.drain(job['id'])
    assert calls==['east']


def test_fallback_is_part_of_confirmation_and_not_primary(tmp_path):
    m=manager(tmp_path)
    with pytest.raises(ValueError):
        m.create('east','East',['TRBTSTHP2607S'],[],'key',True,fallback_account='east',fallback_name='East')
    m.create('east','East',['TRBTSTHP2607S'],[],'key',True,fallback_account='west',fallback_name='West')
    with pytest.raises(ValueError):
        m.create('east','East',['TRBTSTHP2607S'],[],'key',True)


def test_both_accounts_empty_continue_and_keep_trail(tmp_path):
    calls=[]
    def runner(account,*args,**kwargs):
        calls.append(account);return {'status':'not_found','submitted':False}
    m=manager(tmp_path,runner)
    job=m.create('east','East',['TRBTSTHP2607S','TRBTSTZH2606S'],[],'key',True,fallback_account='west',fallback_name='West')
    m.drain(job['id'])
    result=m.get(job['id'])
    assert calls==['east','west','east','west']
    assert result['state']=='completed'
    assert result['rows'][0]['actualAccount']=='west'
    assert [r['status'] for r in result['rows']]==['attention','attention']
    assert [a['status'] for a in result['rows'][0]['attempts']]==['not_found','not_found']


@pytest.mark.parametrize('status', ['rejected','ambiguous','blocked_duplicate','needs_verification'])
def test_failed_order_does_not_block_next_order(tmp_path, status):
    calls=[]
    def runner(account,bill,kind,**kwargs):
        calls.append(bill)
        return {'status':status,'submitted':status=='needs_verification'} if len(calls)==1 else {'status':'verified_unlocked'}
    m=manager(tmp_path,runner)
    job=m.create('west','West',[],['BILL123456','BILL234567'],'key',True)
    m.drain(job['id'])
    result=m.get(job['id'])
    assert calls==['BILL123456','BILL234567']
    assert result['state']=='completed'
    assert [r['status'] for r in result['rows']]==['attention','success']


@pytest.mark.parametrize('batch', [False, True])
def test_duplicate_block_reports_that_no_unlock_was_submitted(tmp_path, batch):
    detail = {'status': 'blocked_duplicate', 'submitted': False}
    if batch:
        detail['reason'] = '有历史未核实提交，本条未再次解锁'
        result = {'status': 'batch_attention', 'records': [detail]}
    else:
        detail['failed_stage'] = 'reserve_submission'
        result = detail
    m = manager(tmp_path, lambda *args, **kwargs: result)
    job = m.create('west', 'West', ['BILL123456'], [], 'key', True)
    m.drain(job['id'])
    row = m.get(job['id'])['rows'][0]
    assert row['status'] == 'attention'
    assert row['detail']['submitted'] is False
    assert '未核实' in row['message']
    assert '未再次解锁' in row['message']
    assert '具体原因尚未确认' not in row['message']


def test_explicit_pause_still_stops_after_current_failure(tmp_path):
    calls=[]
    def runner(account,bill,kind,**kwargs):
        calls.append(bill)
        m.control(job['id'],'pause')
        return {'status':'needs_verification','submitted':True}
    m=manager(tmp_path,runner)
    job=m.create('west','West',[],['BILL123456','BILL234567'],'key',True)
    m.drain(job['id'])
    result=m.get(job['id'])
    assert result['state']=='paused' and calls==['BILL123456']
    assert [r['status'] for r in result['rows']]==['attention','waiting']


def test_runner_exception_keeps_failure_and_continues(tmp_path):
    calls=[]
    def runner(account,bill,kind,**kwargs):
        calls.append(bill)
        if len(calls)==1:
            raise RuntimeError('simulated browser failure')
        return {'status':'verified_unlocked'}
    m=manager(tmp_path,runner)
    job=m.create('west','West',[],['BILL123456','BILL234567'],'key',True)
    m.drain(job['id'])
    result=m.get(job['id'])
    assert result['state']=='completed'
    assert [r['status'] for r in result['rows']]==['attention','success']
    assert len(calls)==2 and result['rows'][0]['detail']['reason']


def test_ambiguous_result_lists_business_numbers_for_review(tmp_path):
    matches=[{'id':'a','booking':'BUSINESS1','mbl':'MBL123456','hbl':'DJSJKT260921011'},
             {'id':'b','booking':'BUSINESS2','mbl':'DJSJKT260921011','hbl':'DJSJKT260921011'}]
    m=manager(tmp_path,lambda *args,**kwargs:{'status':'ambiguous','submitted':False,'matches':matches,'matched_count':2})
    job=m.create('east','East',[],['DJSJKT260921011'],'key',True)
    m.drain(job['id'])
    row=m.get(job['id'])['rows'][0]
    assert row['status']=='attention'
    assert 'BUSINESS1' in row['message'] and 'BUSINESS2' in row['message']
    assert not row['detail']['submitted']


def test_batch_results_expand_by_business_and_continue_next_bill(tmp_path):
    calls=[]
    def runner(account,bill,kind,**kwargs):
        calls.append(bill)
        if bill=='BILL123456':
            return {'status':'batch_attention','records':[
                {'status':'verified_unlocked','before':{'id':'a','booking':'JOB1'},'submitted':True},
                {'status':'needs_verification','before':{'id':'b','booking':'JOB2'},'submitted':True}]}
        return {'status':'verified_unlocked'}
    m=manager(tmp_path,runner)
    job=m.create('east','East',[],['BILL123456','BILL234567'],'key',True)
    m.drain(job['id']);result=m.get(job['id'])
    assert result['state']=='completed'
    assert calls==['BILL123456','BILL234567']
    assert [r['status'] for r in result['rows']]==['success','attention','success']
    assert [r.get('businessNo') for r in result['rows'][:2]]==['JOB1','JOB2']


def test_api_rejects_unusable_fallback_account(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.accounts import AccountStore
    from app import ilog_tasks
    monkeypatch.setenv('FINANCE_RECONCILE_DATA_DIR',str(tmp_path/'accounts'))
    store=AccountStore()
    primary=store.create('East','east','test-secret')
    no_password=store.create('West','west')
    monkeypatch.setattr(ilog_tasks,'_manager',manager(tmp_path/'tasks'))
    monkeypatch.setattr(ilog_tasks,'runtime',lambda:{'available':True,'channel':'chrome'})
    client=TestClient(app)
    for fallback in [primary['id'],no_password['id'],'unknown']:
        response=client.post('/api/ilog/tasks',json={'account_id':primary['id'],'fallback_account_id':fallback,'fee':['TRBTSTHP2607S'],'request_id':'key','confirmed':True},headers={'X-Finance-Local':'1'})
        assert response.status_code==400
    assert client.get('/api/ilog/tasks/latest').json() is None


def test_platform_acknowledged_success_is_not_labelled_verified(tmp_path):
    m = manager(tmp_path, lambda *a, **kw: {'status':'platform_accepted','platform_success':True,'submitted':True,'verification_performed':False})
    job = m.create('west', 'West', ['TRBTSTZH2606S'], [], 'accepted', True)
    m.drain(job['id'])
    row = m.get(job['id'])['rows'][0]
    assert row['status'] == 'success'
    assert row['message'] == '平台返回成功（未复查）'
    assert row['detail']['verification_performed'] is False


def test_fast_mode_is_saved_and_legacy_task_defaults_to_verification(tmp_path):
    m = manager(tmp_path)
    job = m.create('west','West',['TRBTSTZH2606S'],[],'fast',True,verify_after=False)
    assert job['verify_after'] is False
    assert m.get(job['id'])['verify_after'] is False
    with pytest.raises(ValueError):
        m.create('west','West',['TRBTSTZH2606S'],[],'fast',True,verify_after=True)


def test_drain_pauses_with_notice_when_platform_switch_is_held(tmp_path,monkeypatch):
    import threading
    from app import ilog_tasks
    monkeypatch.setattr(ilog_tasks,'TRANSITION_WAIT_SECONDS',0.3)
    monkeypatch.setattr(ilog_tasks,'TRANSITION_POLL_SECONDS',0.1)
    m=manager(tmp_path)
    job=m.create('west','West',['TRBTSTZH2606S'],[],'switch-held',True)
    assert ilog_tasks.platform_transition_lock.acquire(timeout=1)
    try:
        worker=threading.Thread(target=m.drain,args=(job['id'],));worker.start();worker.join(5)
        assert not worker.is_alive()
    finally:
        ilog_tasks.platform_transition_lock.release()
    result=m.get(job['id'])
    assert result['state']=='paused'
    assert [r['status'] for r in result['rows']]==['waiting']
    assert '切换账号' in result['notice']
    # 用户点“继续”后提示应清掉，避免一直显示已暂停原因。
    assert 'notice' not in m.control(job['id'],'resume')


def test_cancel_releases_thread_waiting_for_platform_switch(tmp_path,monkeypatch):
    import threading,time
    from app import ilog_tasks
    monkeypatch.setattr(ilog_tasks,'TRANSITION_WAIT_SECONDS',30)
    monkeypatch.setattr(ilog_tasks,'TRANSITION_POLL_SECONDS',0.1)
    m=manager(tmp_path)
    job=m.create('west','West',['TRBTSTZH2606S','TRBTSTHP2607S'],[],'switch-cancel',True)
    assert ilog_tasks.platform_transition_lock.acquire(timeout=1)
    worker=threading.Thread(target=m.drain,args=(job['id'],));worker.start()
    try:
        time.sleep(0.2)
        m.control(job['id'],'cancel')
        worker.join(3)
        assert not worker.is_alive()
    finally:
        ilog_tasks.platform_transition_lock.release();worker.join(3)
    result=m.get(job['id'])
    assert result['state']=='completed'
    assert [r['status'] for r in result['rows']]==['cancelled','cancelled']


def test_drain_pauses_when_worker_lock_queue_times_out(tmp_path,monkeypatch):
    import threading,time
    from app import ilog_tasks
    monkeypatch.setattr(ilog_tasks,'TRANSITION_WAIT_SECONDS',0.8)
    held=threading.Event();release=threading.Event()
    def hold():
        with ilog_tasks.worker_lock(tmp_path):
            held.set();release.wait(5)
    holder=threading.Thread(target=hold,daemon=True);holder.start();assert held.wait(2)
    m=manager(tmp_path)
    job=m.create('west','West',['TRBTSTZH2606S'],[],'worker-queue',True)
    worker=threading.Thread(target=m.drain,args=(job['id'],));worker.start()
    worker.join(5)
    release.set();holder.join(2)
    assert not worker.is_alive()
    result=m.get(job['id'])
    assert result['state']=='paused'
    assert [r['status'] for r in result['rows']]==['waiting']
    assert '排队等待超过' in result['notice']


def test_cancel_exits_thread_queued_on_worker_lock(tmp_path,monkeypatch):
    import threading,time
    from app import ilog_tasks
    monkeypatch.setattr(ilog_tasks,'TRANSITION_WAIT_SECONDS',30)
    monkeypatch.setattr(ilog_tasks,'WORKER_POLL_SECONDS',0.05)
    held=threading.Event();release=threading.Event()
    def hold():
        with ilog_tasks.worker_lock(tmp_path):
            held.set();release.wait(5)
    holder=threading.Thread(target=hold,daemon=True);holder.start();assert held.wait(2)
    m=manager(tmp_path)
    job=m.create('west','West',['TRBTSTZH2606S'],[],'worker-cancel',True)
    worker=threading.Thread(target=m.drain,args=(job['id'],));worker.start()
    time.sleep(0.2)
    m.control(job['id'],'cancel')
    worker.join(3)
    release.set();holder.join(2)
    assert not worker.is_alive()
    result=m.get(job['id'])
    assert result['state']=='completed'
    assert [r['status'] for r in result['rows']]==['cancelled']
    assert 'notice' not in result


def test_cancel_keeps_task_closing_until_owner_thread_closes_browser(tmp_path, monkeypatch):
    import threading

    from app import ilog_tasks, ilog_unlock

    started = threading.Event()
    finish_item = threading.Event()
    closing = threading.Event()
    finish_close = threading.Event()
    calls = []
    close_threads = []
    worker_threads = []

    class Browser:
        def close(self):
            close_threads.append(threading.get_ident())
            closing.set()
            finish_close.wait(3)

    def run(account, bill, kind, *, session_pool, **kwargs):
        calls.append(bill)
        worker_threads.append(threading.get_ident())
        session_pool._browsers.append(Browser())
        started.set()
        finish_item.wait(3)
        return {'status': 'verified_fee_unlocked'}

    monkeypatch.setattr(ilog_unlock, 'run', run)
    monkeypatch.setattr(ilog_tasks, 'runtime', lambda: {'channel': 'chrome'})
    m = manager(tmp_path)
    job = m.create('west', 'West', ['TRBTSTZH2606S', 'TRBTSTHP2607S'], [], 'key', True)
    worker = threading.Thread(target=m.drain, args=(job['id'],))
    worker.start()
    try:
        assert started.wait(2)
        cancelled = m.control(job['id'], 'cancel')
        assert [row['status'] for row in cancelled['rows']] == ['processing', 'cancelled']
        finish_item.set()
        assert closing.wait(2)
        # 新契约：close 由看门狗线程执行（不在 drain 线程内），任务仍保持 closing 直到收尾完成。
        assert close_threads and close_threads != worker_threads
        assert m.get(job['id'])['state'] == 'closing'
        # 新契约：收尾（closing）期间允许立即建新任务，新任务的 drain 在 worker 锁上排队自动衔接。
        assert m.create('west', 'West', ['TRBTSTHP2607S'], [], 'next', True)['state'] == 'running'
    finally:
        finish_item.set()
        finish_close.set()
        worker.join(4)

    assert not worker.is_alive()
    assert m.get(job['id'])['state'] == 'completed'
    assert calls == ['TRBTSTZH2606S']
    with ilog_tasks.worker_lock(tmp_path) as acquired:
        assert acquired
    assert m.create('west', 'West', ['TRBTSTHP2607S'], [], 'next', True)['state'] == 'running'


def test_create_allowed_while_previous_task_is_closing(tmp_path):
    # 取消/跑完后的 'closing'（关浏览器、放锁的收尾窗口）不应拦截新任务：新任务排队自动衔接。
    m=manager(tmp_path, lambda *a, **kw: {'status':'verified_fee_unlocked','platform_success':True,'message':'ok'})
    first=m.create('west','West',['TRBTSTZH2606S'],[],'closing-a',True)
    with m.db() as db:
        db.execute("UPDATE tasks SET state='closing' WHERE id=?",(first['id'],))
    second=m.create('west','West',['TRBTSTHP2607S'],[],'closing-b',True)
    assert second['state']=='running'
    with m.db() as db:
        assert db.execute("SELECT state FROM tasks WHERE id=?",(first['id'],)).fetchone()[0]=='closing'
    # 真正未结束的任务（running/paused）仍然拦截，防止并发执行两个任务。
    with m.db() as db:
        db.execute("UPDATE tasks SET state='completed' WHERE id=?",(second['id'],))
    third=m.create('west','West',['TRBTSTHP2607S'],[],'closing-c',True)
    with m.db() as db:
        db.execute("UPDATE tasks SET state='paused' WHERE id=?",(third['id'],))
    import pytest
    with pytest.raises(RuntimeError):
        m.create('west','West',['TRBTSTHP2607S'],[],'closing-d',True)



def test_row_watchdog_kills_stuck_runner(tmp_path,monkeypatch):
    """A dead browser makes the sync call block forever; the watchdog must kill
    the automation processes so the blocked call can wake and the row finish."""
    import threading
    from app import ilog_tasks, ilog_session
    monkeypatch.setattr(ilog_tasks,'ROW_MAX_SECONDS',0.3)
    monkeypatch.setattr(ilog_tasks,'ROW_WATCHDOG_POLL_SECONDS',0.05)
    monkeypatch.setattr(ilog_tasks,'ROW_KILL_GRACE_SECONDS',1.0)
    killed=threading.Event()
    def fake_kill():
        killed.set(); return [12345]
    monkeypatch.setattr(ilog_session,'kill_browser_processes',fake_kill)
    def runner(account,bill,kind,**kwargs):
        assert killed.wait(5),'看门狗应在超时后强杀自动化进程'
        return {'status':'needs_verification','reason':'killed'}
    m=manager(tmp_path, runner)
    job=m.create('west','West',['TRBTSTZH2606S'],[],'row-watchdog',True)
    worker=threading.Thread(target=m.drain,args=(job['id'],)); worker.start(); worker.join(10)
    assert not worker.is_alive()
    result=m.get(job['id'])
    assert result['state']=='completed'
    assert result['rows'][0]['attempts'][-1].get('watchdog_killed')==[12345]


def test_session_pool_exit_abandons_hanging_close(tmp_path,monkeypatch):
    """close() can block forever on a dead driver; __exit__ must stay bounded."""
    import threading,time
    from app import ilog_session
    monkeypatch.setattr(ilog_session,'CLOSE_TIMEOUT_SECONDS',0.3)
    monkeypatch.setattr(ilog_session,'CLOSE_KILL_GRACE_SECONDS',0.1)
    release=threading.Event()
    class StuckRuntime:
        def stop(self):release.wait(5)
    pool=ilog_session.SessionPool()
    pool._runtime=StuckRuntime()
    started=time.monotonic()
    with pool:
        pass
    assert time.monotonic()-started < 2,'close 挂起时 __exit__ 不应超过看门狗时长'
    release.set()


def test_row_watchdog_kills_stuck_runner(tmp_path,monkeypatch):
    """A dead browser makes the sync call block forever; the watchdog must kill
    the automation processes so the blocked call can wake and the row finish."""
    import threading
    from app import ilog_tasks, ilog_session
    monkeypatch.setattr(ilog_tasks,'ROW_MAX_SECONDS',0.3)
    monkeypatch.setattr(ilog_tasks,'ROW_WATCHDOG_POLL_SECONDS',0.05)
    monkeypatch.setattr(ilog_tasks,'ROW_KILL_GRACE_SECONDS',1.0)
    killed=threading.Event()
    def fake_kill():
        killed.set(); return [12345]
    monkeypatch.setattr(ilog_session,'kill_browser_processes',fake_kill)
    def runner(account,bill,kind,**kwargs):
        assert killed.wait(5),'看门狗应在超时后强杀自动化进程'
        return {'status':'needs_verification','reason':'killed'}
    m=manager(tmp_path, runner)
    job=m.create('west','West',['TRBTSTZH2606S'],[],'row-watchdog',True)
    worker=threading.Thread(target=m.drain,args=(job['id'],)); worker.start(); worker.join(10)
    assert not worker.is_alive()
    result=m.get(job['id'])
    assert result['state']=='completed'
    assert result['rows'][0]['attempts'][-1].get('watchdog_killed')==[12345]


def test_session_pool_exit_abandons_hanging_close(tmp_path,monkeypatch):
    """close() can block forever on a dead driver; __exit__ must stay bounded."""
    import threading,time
    from app import ilog_session
    monkeypatch.setattr(ilog_session,'CLOSE_TIMEOUT_SECONDS',0.3)
    monkeypatch.setattr(ilog_session,'CLOSE_KILL_GRACE_SECONDS',0.1)
    release=threading.Event()
    class StuckRuntime:
        def stop(self):release.wait(5)
    pool=ilog_session.SessionPool()
    pool._runtime=StuckRuntime()
    started=time.monotonic()
    with pool:
        pass
    assert time.monotonic()-started < 2,'close 挂起时 __exit__ 不应超过看门狗时长'
    release.set()


def test_session_pool_page_failure_close_is_bounded(tmp_path,monkeypatch):
    """The exception path of page() also closes the browser; a half-dead browser
    must not block the task thread there either."""
    import threading,time
    from app import ilog_session
    monkeypatch.setattr(ilog_session,'CLOSE_TIMEOUT_SECONDS',0.3)
    monkeypatch.setattr(ilog_session,'CLOSE_KILL_GRACE_SECONDS',0.1)
    release=threading.Event()
    class StuckBrowser:
        def new_page(self,*a,**k): raise RuntimeError('boom')
        def close(self): release.wait(5)
    class FakeChromium:
        def launch(self,*a,**k): return StuckBrowser()
    class FakeRuntime:
        chromium=FakeChromium()
    pool=ilog_session.SessionPool()
    pool._runtime=FakeRuntime()
    started=time.monotonic()
    with pytest.raises(RuntimeError):
        pool.page('a',{'username':'u'},'p')
    assert time.monotonic()-started < 2,'登录失败的收尾同样不应超过看门狗时长'
    release.set()


def test_session_pool_page_failure_close_is_bounded(tmp_path,monkeypatch):
    """The exception path of page() also closes the browser; a half-dead browser
    must not block the task thread there either."""
    import threading,time
    from app import ilog_session
    monkeypatch.setattr(ilog_session,'CLOSE_TIMEOUT_SECONDS',0.3)
    monkeypatch.setattr(ilog_session,'CLOSE_KILL_GRACE_SECONDS',0.1)
    release=threading.Event()
    class StuckBrowser:
        def new_page(self,*a,**k): raise RuntimeError('boom')
        def close(self): release.wait(5)
    class FakeChromium:
        def launch(self,*a,**k): return StuckBrowser()
    class FakeRuntime:
        chromium=FakeChromium()
    pool=ilog_session.SessionPool()
    pool._runtime=FakeRuntime()
    started=time.monotonic()
    with pytest.raises(RuntimeError):
        pool.page('a',{'username':'u'},'p')
    assert time.monotonic()-started < 2,'登录失败的收尾同样不应超过看门狗时长'
    release.set()
