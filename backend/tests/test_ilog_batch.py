import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from app.ilog_batch import plan_batch, batch_status, execute_batch


def records():
    return [dict(id='one',mbl='OTHER12345',hbl='BILL123456',booking='JOB1',lock='B'),
            dict(id='two',mbl='BILL123456',hbl='BILL123456',booking='JOB2',lock='A')]


def test_plan_requires_every_record_to_match_and_keeps_per_record_states():
    rows=plan_batch(records(),'BILL123456','fee')
    assert [r['status'] for r in rows]==['already_fee_unlocked','needs_verification']
    assert all(not r['submitted'] for r in rows)
    assert len(plan_batch(records(),'BILL123456','order'))==2


def test_batch_accepts_fuzzy_platform_results():
    found=records()
    found[1].update(mbl='OTHER12345',hbl='OTHER12345')
    assert len(plan_batch(found,'123','order'))==2


@pytest.mark.parametrize('change', ['duplicate','unknown'])
def test_batch_rejects_partial_matches_duplicate_ids_and_unknown_locks(change):
    found=records()
    if change=='partial': found[1].update(mbl='OTHER12345',hbl='OTHER12345')
    if change=='duplicate': found[1]['id']='one'
    if change=='unknown': found[1]['lock']='?'
    with pytest.raises(ValueError): plan_batch(found,'BILL123456','order')


def test_batch_success_requires_every_record_verified():
    assert batch_status([{'status':'verified_unlocked'},{'status':'already_unlocked'}],'order')=='verified_unlocked'
    assert batch_status([{'status':'verified_unlocked'},{'status':'needs_verification'}],'order')=='batch_attention'
    assert batch_status([{'status':'already_fee_unlocked'}],'fee')=='already_fee_unlocked'


class FakePage:
    def __init__(self, selected):
        self.selected=selected; self.events=[]
    def evaluate(self, script): return 'grid' if 'getEl' in script else self.selected
    def locator(self, selector): self.events.append(('locator',selector)); return self
    def nth(self,index): self.events.append(('nth',index)); return self
    def get_by_role(self,role,**kw): self.target=kw['name']; return self
    def get_by_text(self,text,**kw): self.target=text; return self
    def click(self,**kw): self.events.append(('click',getattr(self,'target','checkbox')))
    def bounding_box(self): return {'width':100,'height':30}
    def inner_text(self): return '确认取消锁定吗?'
    def route(self,*args): pass
    def goto(self,*args): self.events.append(('query_navigation',))
    def wait_for(self): pass
    def screenshot(self,**kw): Path(kw['path']).write_bytes(b'test-proof')
    def expect_response(self, predicate):
        request=SimpleNamespace(method='POST',post_data='query=4&ids='+','.join(self.selected))
        response=SimpleNamespace(url='https://example.com/order_unlock',request=request,ok=True,status=200,
                                 text=lambda:'{"success":true,"msg":"success"}')
        assert predicate(response)
        wrong=SimpleNamespace(url=response.url,request=SimpleNamespace(method='POST',post_data='query=3&ids=other'))
        assert not predicate(wrong)
        class Context:
            value=response
            def __enter__(self): return self
            def __exit__(self,*args): pass
        return Context()


@pytest.mark.parametrize('query',['BILL123456','123'])
def test_batch_submits_once_and_verifies_each_record(tmp_path,monkeypatch,query):
    found=records();page=FakePage(['one','two'])
    monkeypatch.setattr('app.ilog_unlock.search_bill',lambda *args,**kw:[{**r,'lock':'U'} for r in found])
    result={'account':'east','bill':query,'kind':'order','submitted':False}
    execute_batch(page,found,result,tmp_path/'report.json',tmp_path,lambda stage:result.update(stage=stage))
    assert result['status']=='verified_unlocked'
    assert [r['status'] for r in result['records']]==['verified_unlocked','verified_unlocked']
    assert page.events.count(('click','Yes'))==1
    assert ('locator','.x-grid3-hd-inner > .x-grid3-hd-checker') in page.events
    saved=[json.loads(path.read_text()) for path in (tmp_path/'submitted').glob('*.json')]
    assert len(saved)==2 and all(r['after']['lock']=='U' for r in saved)


def test_batch_skips_already_unlocked_record(tmp_path,monkeypatch):
    found=records();found[0]['lock']='U';page=FakePage(['two'])
    monkeypatch.setattr('app.ilog_unlock.search_bill',lambda *args,**kw:[{**r,'lock':'U'} for r in found])
    result={'account':'east','bill':'BILL123456','kind':'order','submitted':False}
    execute_batch(page,found,result,tmp_path/'report.json',tmp_path,lambda stage:result.update(stage=stage))
    assert [r['submitted'] for r in result['records']]==[False,True]
    assert len(list((tmp_path/'submitted').glob('*.json')))==1
    assert ('locator','.x-grid3-hd-inner > .x-grid3-hd-checker') not in page.events


def test_selection_mismatch_never_submits(tmp_path):
    page=FakePage(['unexpected']);result={'account':'east','bill':'BILL123456','kind':'order','submitted':False}
    execute_batch(page,records(),result,tmp_path/'report.json',tmp_path,lambda stage:result.update(stage=stage))
    assert result['status']=='batch_attention' and not result['submitted']
    assert ('click','Yes') not in page.events
    assert all(not r['submitted'] for r in result['records'])


def test_batch_verification_retry_never_repeats_submission(tmp_path,monkeypatch):
    found=records();page=FakePage(['one','two']);queries=[]
    def query(*args,**kwargs):
        queries.append(1)
        return [{**r,'lock':'U'} for r in found] if len(queries)==4 else found
    monkeypatch.setattr('app.ilog_unlock.search_bill',query)
    monkeypatch.setattr('app.ilog_unlock.time.sleep',lambda _:None)
    result={'account':'east','bill':'BILL123456','kind':'order','submitted':False}
    execute_batch(page,found,result,tmp_path/'report.json',tmp_path,lambda stage:result.update(stage=stage))
    assert result['status']=='verified_unlocked' and len(queries)==4
    assert page.events.count(('click','Yes'))==1


def test_batch_duplicate_guard_is_per_record(tmp_path,monkeypatch):
    from app.ilog_unlock import reserve_operation
    first=reserve_operation(tmp_path/'submitted','east','one','order')
    old=first.read_text()
    found=records();page=FakePage(['two'])
    monkeypatch.setattr('app.ilog_unlock.search_bill',lambda *a,**kw:[{**r,'lock':'U'} for r in found])
    result={'account':'east','bill':'BILL123456','kind':'order','submitted':False}
    execute_batch(page,found,result,tmp_path/'report.json',tmp_path,lambda stage:result.update(stage=stage))
    assert [r['status'] for r in result['records']]==['blocked_duplicate','verified_unlocked']
    assert first.read_text()==old
    assert page.events.count(('click','Yes'))==1
