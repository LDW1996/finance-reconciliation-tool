import pytest
from app.ilog_voyage import build_groups, validate_target, voyage_reply_status


def record(identity='1',ship='ORIENTAL BRIGHT',voyage='2619S',lock='A'):
    return dict(id=identity,booking='JOB'+identity,mbl='BILL',hbl='BILL',lock=lock,ship=ship,voyage=voyage)


def test_preview_groups_same_voyage_and_keeps_different_voyages():
    queries=[dict(account_id='west',account_name='巴淡',bill='BL1',kind='fee',records=[record(),record('2'),record('3',voyage='2620N')]),
             dict(account_id='west',account_name='巴淡',bill='BL2',kind='fee',records=[record('2')]),
             dict(account_id='west',account_name='巴淡',bill='BL1',kind='order',records=[record()])]
    groups=build_groups(queries)
    assert len(groups)==3
    group=groups[0]
    assert group['bills']==['BL1','BL2']
    assert len(group['records'])==2 and group['affected_count'] is None


def test_missing_voyage_never_yields_a_target():
    with pytest.raises(ValueError):build_groups([dict(account_id='a',account_name='a',bill='BL',kind='fee',records=[record(voyage='')])])


def test_execution_target_changed_is_rejected():
    target=build_groups([dict(account_id='a',account_name='a',bill='BL',kind='fee',records=[record()])])[0]
    validate_target([record()],target)
    with pytest.raises(ValueError):validate_target([record(voyage='OTHER')],target)
    with pytest.raises(ValueError):validate_target([record('9')],target)


def test_voyage_success_does_not_claim_all_orders_verified():
    assert voyage_reply_status({'success':True})=='voyage_platform_confirmed'
    assert voyage_reply_status({'success':False})=='rejected'
    assert voyage_reply_status(None)=='needs_verification'


def test_voyage_submit_uses_scope_not_record_id_and_never_repeats(tmp_path, monkeypatch):
    from test_ilog_batch import FakePage
    from types import SimpleNamespace
    from urllib.parse import urlencode
    from app.ilog_voyage import execute_voyage
    class Page(FakePage):
        def expect_response(self, predicate):
            req=SimpleNamespace(method='POST',post_data=urlencode({'query':'44','ids':'ORIENTAL BRIGHT/2619S'}))
            response=SimpleNamespace(url='https://example.com/order_unlock',request=req,ok=True,status=200,text=lambda:'{"success":true}')
            assert predicate(response)
            assert not predicate(SimpleNamespace(url=response.url,request=SimpleNamespace(method='POST',post_data='query=4&ids=1')))
            class Context:
                value=response
                def __enter__(self):return self
                def __exit__(self,*a):pass
            return Context()
    found=[record()];target=build_groups([dict(account_id='a',account_name='a',bill='BILL',kind='order',records=found)])[0]
    result=dict(account='a',bill='BILL',kind='order',submitted=False)
    page=Page(['1'])
    monkeypatch.setattr('app.ilog_unlock.search_bill',lambda *a,**k:[record(lock='U')])
    execute_voyage(page,found,result,tmp_path/'report',tmp_path,lambda s:result.update(stage=s),target)
    assert result['status']=='voyage_platform_confirmed'
    assert result['verification_scope']=='representative_only'
    assert page.events.count(('click','Yes'))==1


def test_changed_voyage_never_clicks(tmp_path):
    from test_ilog_batch import FakePage
    from app.ilog_voyage import execute_voyage
    target=build_groups([dict(account_id='a',account_name='a',bill='BILL',kind='order',records=[record()])])[0]
    page=FakePage(['1']);result=dict(account='a',bill='BILL',kind='order',submitted=False)
    with pytest.raises(ValueError):execute_voyage(page,[record(voyage='new')],result,tmp_path/'r',tmp_path,lambda s:None,target)
    assert not page.events and not result['submitted']
