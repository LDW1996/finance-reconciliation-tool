from datetime import datetime, timedelta, timezone

import pytest

from app.outlook_probe import MailStore


NOW=datetime(2026,10,25,12,tzinfo=timezone.utc)


def test_history_checkpoint_then_incremental_window(tmp_path):
    from app.mail_sync import MailSync
    store=MailStore(tmp_path/'mail.sqlite3')
    sync=MailSync(store)
    plan=sync.plan('a','inbox',90,NOW)
    assert plan['phase']=='history' and plan['cursor'] is None
    cursor={'before':(NOW-timedelta(days=1)).isoformat(),'seen':['one']}
    sync.commit(plan,dict(cursor=cursor,partial=True,read=2))
    resumed=MailSync(store).plan('a','inbox',90,NOW+timedelta(hours=1))
    assert resumed['cursor']==cursor and resumed['end']==plan['end']
    sync.commit(resumed,dict(cursor=None,partial=False,read=1))
    incremental=sync.plan('a','inbox',90,NOW+timedelta(hours=2))
    assert incremental['phase']=='incremental'
    assert incremental['start']==(NOW-timedelta(days=1)).isoformat()
    assert incremental['end']==(NOW+timedelta(hours=2)).isoformat()


def test_failed_page_does_not_advance_and_source_changes_reset(tmp_path):
    from app.mail_sync import MailSync
    sync=MailSync(MailStore(tmp_path/'mail.sqlite3'))
    old=sync.plan('a','inbox',90,NOW)
    # Not committing a failed page keeps the exact snapshot retryable.
    assert sync.plan('a','inbox',90,NOW+timedelta(hours=1))['end']==old['end']
    other=sync.plan('b','inbox',90,NOW+timedelta(hours=2))
    assert other['phase']=='history' and other['cursor'] is None
    assert other['end']!=old['end']


def test_nonadvancing_page_is_rejected(tmp_path):
    from app.mail_sync import MailSync
    sync=MailSync(MailStore(tmp_path/'mail.sqlite3'))
    plan=sync.plan('a','inbox',90,NOW)
    with pytest.raises(ValueError,match='进度'):
        sync.commit(plan,dict(cursor=None,partial=True,read=0))


def test_equal_timestamp_paging_does_not_skip_mail(tmp_path):
    from app.outlook_probe import scan_folder_page
    from test_outlook_probe import Mail
    class Items:
        def __init__(self,items): self.items=items;self.Count=len(items)
        def Restrict(self,query): return self
        def Sort(self,*args): pass
        def Item(self,index): return self.items[index-1]
    messages=[Mail(str(i)) for i in range(5)]
    folder=type('Folder',(),{'Items':Items(messages)})()
    start=datetime(2026,10,1,tzinfo=timezone.utc)
    end=datetime(2026,11,1,tzinfo=timezone.utc)
    page=scan_folder_page(folder,'store',start,end,2)
    second=scan_folder_page(folder,'store',start,end,2,page['cursor'])
    third=scan_folder_page(folder,'store',start,end,2,second['cursor'])
    all_ids=[r['id'] for p in (page,second,third) for r in p['rows']]
    assert len(all_ids)==len(set(all_ids))==5
    assert page['partial'] and second['partial'] and not third['partial']


def test_encrypted_state_and_payments_are_not_limited_to_ui_list(tmp_path):
    store=MailStore(tmp_path/'mail.sqlite3')
    store.set_state('private',{'value':'SENSITIVE_STATE'})
    assert store.get_state('private')['value']=='SENSITIVE_STATE'
    for i in range(501): store.set_payments(str(i),[{'id':str(i),'payer':'SENSITIVE_PAYER'}])
    assert len(store.payment_rows())==501
    raw=(tmp_path/'mail.sqlite3').read_bytes()
    assert b'SENSITIVE_STATE' not in raw and b'SENSITIVE_PAYER' not in raw
