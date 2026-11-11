import sys
import types
from copy import deepcopy
import pytest
from app.ilog_tasks import TaskManager


@pytest.fixture
def manager(tmp_path, monkeypatch):
    # Contract stub: the independently owned voyage module is not required here.
    def build_groups(queries):
        return [dict(q, key=str(i), bills=[q['bill']], ship='SHIP', voyage='V1',
                     ship_id='S', voyage_id='V', affected_count=None) for i,q in enumerate(queries)]
    monkeypatch.setitem(sys.modules,'app.ilog_voyage',types.SimpleNamespace(build_groups=build_groups))
    monkeypatch.setattr('app.ilog_tasks.runtime',lambda: {'channel':'chrome','available':True})
    calls=[]
    def runner(account,bill,kind,*,execute=False,channel='chrome'):
        calls.append((account,bill,execute))
        return {'status':'not_found'} if account=='primary' else {'status':'inspected','matches':[{'id':'1'}]}
    return TaskManager(tmp_path,runner=runner,auto_start=False),calls


def preview(m):
    return m.preview('primary','Primary',[' bl1 '],[],fallback_account='backup',fallback_name='Backup')


def create(m, token, **kwargs):
    options=dict(fallback_account='backup',fallback_name='Backup',scope='voyage',preview_token=token)
    options.update(kwargs)
    return m.create('primary','Primary',['BL1'],[],'request',True,**options)


def test_read_only_fallback_and_server_targets(manager):
    m,calls=manager
    p=preview(m)
    assert calls==[('primary','BL1',False),('backup','BL1',False)]
    assert p['can_execute'] and p['groups'][0]['affected_count'] is None
    original=deepcopy(p['groups'][0])
    p['groups'][0]['account_id']='forged'
    job=create(m,p['token'],screenshot_policy='none',record_timings=True)
    assert job['rows'][0]['target']==original
    assert job['rows'][0]['actualAccount']=='backup'
    assert job['scope']=='voyage' and job['record_timings'] is True
    assert job['screenshot_policy']=='none'
    assert create(m,p['token'],screenshot_policy='none',record_timings=True)['id']==job['id']


def test_token_expiration_binding_and_required(manager):
    m,_=manager
    p=preview(m)
    for kwargs in [{'fallback_account':None,'fallback_name':None},{'preview_token':'forged'}]:
        with pytest.raises(ValueError): create(m,p['token'],**kwargs)
    with m.db() as db: db.execute('UPDATE voyage_previews SET expires=0')
    with pytest.raises(ValueError): create(m,p['token'])
    with pytest.raises(ValueError): create(m,None)


@pytest.mark.parametrize('status',['ambiguous','needs_verification','rejected'])
def test_only_not_found_falls_back(manager,status):
    m,_=manager
    calls=[]
    def runner(*args,**kwargs):
        calls.append(args[0]);assert kwargs['execute'] is False
        return {'status':status}
    m.runner=runner
    p=preview(m)
    assert calls==['primary'] and p['issues'] and not p['can_execute']
    with pytest.raises(ValueError): create(m,p['token'])


def test_preview_api_and_task_options(manager,monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    m,_=manager
    monkeypatch.setattr(main.ilog_tasks,'manager',lambda:m)
    monkeypatch.setattr(main.AccountStore,'list_accounts',lambda self:[
        {'id':'primary','name':'Primary','hasPassword':True},
        {'id':'backup','name':'Backup','hasPassword':True}])
    monkeypatch.setattr(main.Retention,'maybe_cleanup',lambda self:None)
    client=TestClient(main.app)
    body={'account_id':'primary','fallback_account_id':'backup','fee_bills':['BL1']}
    response=client.post('/api/ilog/voyages/preview',json=body,headers={'X-Finance-Local':'1'})
    assert response.status_code==200,response.text
    result=client.post('/api/ilog/tasks',json=dict(account_id='primary',fallback_account_id='backup',fee=['BL1'],order=[],request_id='api',confirmed=True,scope='voyage',preview_token=response.json()['token'],screenshot_policy='all',record_timings=True),headers={'X-Finance-Local':'1'})
    assert result.status_code==201,result.text
    assert result.json()['rows'][0]['actualAccount']=='backup'


def test_token_binds_account_bill_and_kind(manager):
    m,_=manager
    p=preview(m)
    for account,fee,order in [('other',['BL1'],[]),('primary',['BL2'],[]),('primary',[],['BL1'])]:
        with pytest.raises(ValueError):
            m.create(account,'Name',fee,order,'changed',True,scope='voyage',preview_token=p['token'],fallback_account='backup',fallback_name='Backup')


def test_preview_expiry_is_ten_minutes(manager):
    from datetime import datetime
    import time
    m,_=manager
    before=time.time()
    p=preview(m)
    assert before+599 <= datetime.fromisoformat(p['expires_at']).timestamp() <= time.time()+601


def test_unknown_options_rejected(manager):
    m,_=manager
    for options in [{'scope':'invalid'},{'screenshot_policy':'invalid'},{'record_timings':'yes'}]:
        with pytest.raises(ValueError): m.create('a','A',['BL'],[],'key',True,**options)


@pytest.mark.parametrize('fails',[False,True])
def test_preview_shares_pool_across_all_queries_and_closes(manager,monkeypatch,fails):
    m,_=manager
    events=[];pools=[]
    class Pool:
        def __init__(self, *, login_url):
            assert login_url.startswith('https://')

        def __enter__(self): events.append('enter');return self
        def __exit__(self,*exc): events.append('close')
    monkeypatch.setattr('app.ilog_session.SessionPool',Pool)
    def runner(account,bill,kind,**kwargs):
        pools.append(kwargs['session_pool'])
        assert events==['enter']
        assert kwargs['execute'] is False and kwargs['screenshot_policy']=='none'
        if fails: raise RuntimeError('query failed')
        return {'status':'not_found'} if account=='primary' else {'status':'inspected','before':{'id':'1'}}
    m.runner=runner
    p=m.preview('primary','Primary',['BL1','BL2'],['BL3'],fallback_account='backup',fallback_name='Backup')
    assert len(pools)==(3 if fails else 6)
    assert all(pool is pools[0] for pool in pools)
    assert events==['enter','close']
    assert p['can_execute'] is not fails


def test_legacy_preview_runner_never_creates_pool(manager,monkeypatch):
    m,_=manager
    def forbidden(): raise AssertionError('legacy runner must not create pool')
    monkeypatch.setattr('app.ilog_session.SessionPool',forbidden)
    assert preview(m)['can_execute']
