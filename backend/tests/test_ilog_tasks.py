import pytest
from app.ilog_tasks import TaskManager


def manager(tmp_path, runner=None):
    return TaskManager(tmp_path, runner=runner, auto_start=False)


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
