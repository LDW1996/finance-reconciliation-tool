from copy import deepcopy

import pytest

from app.outlook_probe import MailStore
from app.mail_workspace import MailWorkspace
from test_mail_workspace import payment

URL='https://docs.google.com/spreadsheets/d/test-sheet_123/edit#gid=7'


class FakeSheets:
    def __init__(self):
        self.values=[['NO','EFF DATE','DESCRIPTION','CREDIT','BUSINESS TYPE','REF NO','CLOSING STATUS'],
                     [1,'25 Sep 2026','BFS FR BANK UN TEST PAYER','1,400,000','FUEL','001234567890','UNIDENTIFIED']]
        self.calls=[];self.fail=False

    def read(self,config):
        return dict(title='Bank Transactions',values=deepcopy(self.values),formulas=deepcopy(self.values),column_count=26)

    def write(self,config,updates,column_count):
        self.calls.append(deepcopy(updates))
        for row,col,value in updates:
            while len(self.values)<=row:self.values.append([])
            while len(self.values[row])<=col:self.values[row].append('')
            self.values[row][col]=value
        if self.fail:raise TimeoutError('network')


def setup_online(tmp_path):
    from app.mail_online import OnlineLedger
    store=MailStore(tmp_path/'mail.sqlite3');workspace=MailWorkspace(store);client=FakeSheets()
    online=OnlineLedger(workspace,client)
    online.configure(URL,'IDR',None)
    meta=online.refresh();store.set_payments('mail',[payment()])
    row=workspace.matching()['rows'][0]
    workspace.review(meta['id'],row['receipt']['id'],'payment','confirmed',3)
    return online,workspace,client


def test_moved_row_writeback_preserves_status_and_is_idempotent(tmp_path):
    online,workspace,client=setup_online(tmp_path)
    client.values.insert(1,['extra','24 Sep 2026','OTHER','10','FUEL','other-ref','CLOSED'])
    result=online.writeback(workspace.ledger()['id'],3)
    assert result['written']==1
    assert client.values[2][6]=='UNIDENTIFIED'
    assert 'Not a formula' in client.values[2][7]
    assert client.values[2][8]==100
    assert online.writeback(workspace.ledger()['id'],3)['unchanged']==1
    assert len(client.calls)==1
    history=online.history()['items']
    written=next(item for item in history if item['status']=='written')
    assert written['before']!=written['after']


@pytest.mark.parametrize('change',['amount','duplicate','invalid_duplicate','status','remark'])
def test_conflicts_never_overwrite(tmp_path,change):
    online,workspace,client=setup_online(tmp_path)
    if change=='amount':client.values[1][3]='1,500,000'
    if change=='duplicate':client.values.append(deepcopy(client.values[1]))
    if change=='invalid_duplicate':client.values.append([1,'bad date','OTHER','oops','FUEL','001234567890','CLOSED'])
    if change=='status':client.values[1][6]='CLOSED'
    if change=='remark':
        client.values[0]+= ['邮件关联备注','匹配分数','邮件检索信息']
        client.values[1]+= ['人工备注',80,'old source']
    result=online.writeback(workspace.ledger()['id'],3)
    assert result['failed']==1 and not client.calls
    assert online.history()['items'][0]['message']


def test_timeout_after_write_is_uncertain_then_read_reconciles(tmp_path):
    online,workspace,client=setup_online(tmp_path);client.fail=True
    first=online.writeback(workspace.ledger()['id'],3)
    assert first['uncertain']==1 and len(client.calls)==1
    client.fail=False
    assert online.writeback(workspace.ledger()['id'],3)['unchanged']==1
    assert len(client.calls)==1


def test_refresh_keeps_unchanged_review_after_row_moved(tmp_path):
    online,workspace,client=setup_online(tmp_path)
    client.values.insert(1,['extra','24 Sep 2026','OTHER','10','FUEL','other-ref','CLOSED'])
    online.refresh()
    assert workspace.matching()['rows'][0]['review']['state']=='confirmed'


def test_source_validation(tmp_path):
    from app.mail_online import OnlineLedger
    online=OnlineLedger(MailWorkspace(MailStore(tmp_path/'mail.sqlite3')),FakeSheets())
    for bad in ['http://127.0.0.1/test','https://evil.invalid/spreadsheets/d/test/edit#gid=7',URL.replace('#gid=7','')]:
        with pytest.raises(ValueError):online.configure(bad,'IDR',None)
    with pytest.raises(ValueError):online.configure(URL,'IDR',b'{"private_key":"secret"}')


def test_changed_sheet_during_preflight_never_submits(tmp_path):
    online,workspace,client=setup_online(tmp_path)
    original=client.read;calls=0
    def changing(config):
        nonlocal calls
        calls+=1
        if calls==2:client.values.insert(1,['inserted row'])
        return original(config)
    client.read=changing
    assert online.writeback(workspace.ledger()['id'],3)['failed']==1
    assert not client.calls


def test_failed_read_is_audited(tmp_path):
    online,workspace,client=setup_online(tmp_path)
    client.read=lambda config: (_ for _ in ()).throw(ValueError('无访问权限'))
    with pytest.raises(ValueError,match='无访问权限'):online.writeback(workspace.ledger()['id'],3)
    assert online.history()['items'][0]['message']=='无访问权限'


def test_confirmed_count_changed_requires_new_confirmation(tmp_path):
    online,workspace,client=setup_online(tmp_path)
    with pytest.raises(ValueError,match='数量已变化'):online.writeback(workspace.ledger()['id'],3,2)
    assert not client.calls


def test_changed_confirmation_with_same_count_rejects_preview(tmp_path):
    online,workspace,client=setup_online(tmp_path)
    token=workspace.matching(scope='all')['confirmation_token']
    changed={**payment(),'id':'second','payment_id':'second','remark':'other remark'}
    online.store.set_payments('mail',[payment(),changed])
    row=workspace.matching()['rows'][0]
    workspace.review(workspace.ledger()['id'],row['receipt']['id'],'second','confirmed',3)
    with pytest.raises(ValueError,match='确认内容已变化'):online.writeback(workspace.ledger()['id'],3,1,token)
    assert not client.calls


def test_equal_existing_notes_do_not_become_owned(tmp_path):
    online,workspace,client=setup_online(tmp_path)
    client.values[0]+=['邮件关联备注','匹配分数','邮件检索信息']
    client.values[1]+=['已确认关联：'+payment()['remark'],100,payment()['search_info']]
    assert online.writeback(workspace.ledger()['id'],3)['unchanged']==1
    changed={**payment(),'remark':'new remark'};online.store.set_payments('mail',[changed])
    row=workspace.matching()['rows'][0];workspace.review(workspace.ledger()['id'],row['receipt']['id'],'payment','confirmed',3)
    assert online.writeback(workspace.ledger()['id'],3)['failed']==1
    assert not client.calls


def test_google_title_not_valid_in_excel_can_still_import(tmp_path):
    online,workspace,client=setup_online(tmp_path)
    original=client.read
    client.read=lambda config:{**original(config),'title':'Bank / USD'}
    assert online.refresh()['sheet']=='Bank / USD'
    assert workspace.export()[0]


def test_formatted_score_is_idempotent_and_audit_is_encrypted(tmp_path):
    online,workspace,client=setup_online(tmp_path)
    online.writeback(workspace.ledger()['id'],3)
    client.values[1][8]='100.00'
    assert online.writeback(workspace.ledger()['id'],3)['unchanged']==1
    assert len(client.calls)==1
    with online.store.db() as db:
        stored=db.execute('SELECT payload FROM mail_online_audit LIMIT 1').fetchone()[0]
    assert 'TEST PAYER' not in stored and '001234567890' not in stored


def test_crash_prepared_record_is_uncertain_after_restart(tmp_path):
    from app.mail_online import OnlineLedger
    online,workspace,client=setup_online(tmp_path)
    online._audit(dict(id='crashed',created_at='2026-10-25',status='prepared',ref='001234567890',before=[],after=[],message=''))
    recovered=OnlineLedger(workspace,client)
    assert recovered.history()['items'][0]['status']=='uncertain'


def test_online_routes_require_local_origin_and_return_no_secrets(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app import mail_tasks
    manager=mail_tasks.MailManager(MailStore(tmp_path/'routes.sqlite3'))
    manager.online.client=FakeSheets();monkeypatch.setattr(mail_tasks,'_manager',manager)
    client=TestClient(app);headers={'X-Finance-Local':'1','Origin':'http://127.0.0.1:5173'}
    assert client.get('/api/mail/online/config').status_code==403
    assert client.post('/api/mail/online/config',headers=headers,data={'spreadsheet_url':URL}).status_code==200
    manager.store.set_state('online_credentials',{'client_email':'test@example.iam.gserviceaccount.com','private_key':'SECRET'})
    response=client.get('/api/mail/online/config',headers=headers)
    assert response.json()['configured'] is True and 'SECRET' not in response.text
    meta=client.post('/api/mail/online/refresh',headers=headers).json()
    assert meta['online']['sheet_id']==7
    assert client.post('/api/mail/online/writeback',headers=headers,json={'ledger_id':meta['id'],'confirmation_token':'0'*64}).status_code==409
    assert client.get('/api/mail/online/history',headers=headers).json()['items']
