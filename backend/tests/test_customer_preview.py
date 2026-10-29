import pytest
from app.customer_mapping import parse_contacts, preview_contacts

RAW=('Customer Name,Customer Bank Name Reff,Email\n'
     'FAF,Nani Desman,dian@faf.test\n'
     'Other,Another Payer,dian@faf.test\n'
     'Missing Bank,,contact@test.com\n'
     'Bad Email,Some Payer,invalid-email\n'
     'FAF,Nani Desman,dian@faf.test\n').encode()


def test_parser_records_excluded_reasons_without_changing_valid_matching():
    result=parse_contacts(RAW,'customers.csv')
    assert result['entries_count']==2 and result['skipped']==2
    assert result['duplicates_count']==1
    assert [row['row'] for row in result['excluded_rows']]==[4,5,6]
    assert '银行付款人缺失' in result['excluded_rows'][0]['reasons']
    assert '邮箱格式无法识别' in result['excluded_rows'][1]['reasons']
    assert result['excluded_rows'][2]['duplicate_of']==2


def test_paged_search_and_flags_come_from_local_snapshot():
    snapshot=parse_contacts(RAW,'customers.csv')
    valid=preview_contacts(snapshot,page=1,page_size=1)
    assert valid['total']==2 and len(valid['rows'])==1
    assert '共享邮箱' in valid['rows'][0]['flags']
    second=preview_contacts(snapshot,page=2,page_size=1)
    assert second['rows'][0]['row']==3
    filtered=preview_contacts(snapshot,keyword='another payer')
    assert filtered['total']==1 and filtered['rows'][0]['customer']=='Other'
    excluded=preview_contacts(snapshot,status='excluded',keyword='银行付款人缺失')
    assert excluded['total']==1 and excluded['rows'][0]['row']==4
    assert 'entries' not in valid['metadata'] and 'excluded_rows' not in valid['metadata']


def test_legacy_snapshot_preview_does_not_invent_excluded_details():
    snapshot=parse_contacts(RAW,'customers.csv');snapshot.pop('excluded_rows');snapshot.pop('duplicates_count')
    result=preview_contacts(snapshot,status='excluded')
    assert not result['diagnostics_available']
    assert result['total']==0 and result['metadata']['skipped']==2
    assert '重新读取' in result['notice']
    assert preview_contacts(snapshot)['total']==2


def test_preview_never_changes_input_or_revision():
    from copy import deepcopy
    snapshot=parse_contacts(RAW,'customers.csv');before=deepcopy(snapshot)
    preview_contacts(snapshot);preview_contacts(snapshot,status='excluded')
    assert snapshot==before


def test_api_preview_is_readonly_bounded_and_excludes_rows_from_summary(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app import mail_tasks
    from app.outlook_probe import MailStore
    store=MailStore(tmp_path/'mail.sqlite3')
    manager=mail_tasks.MailManager(store,reader=lambda *x:dict(rows=[],errors=[],partial=False))
    monkeypatch.setattr(mail_tasks,'_manager',manager)
    manager.workspace.import_customer_mapping(RAW,'contacts.csv')
    store.set_state('sync_progress',dict(cursor=852))
    before=store.get_state('customer_mapping')
    def forbidden(*args):raise AssertionError('Preview must not read Google')
    manager.customer_online.client.read=forbidden
    client=TestClient(app);headers={'X-Finance-Local':'1'};url='/api/mail/customer-mapping/preview'
    assert client.get(url).status_code==403
    valid=client.get(url,headers=headers,params=dict(page_size=1)).json()
    assert len(valid['rows'])==1 and valid['total']==2
    excluded=client.get(url,headers=headers,params=dict(status='excluded',keyword='邮箱格式')).json()
    assert excluded['total']==1 and excluded['rows'][0]['email']=='invalid-email'
    assert client.get(url,headers=headers,params=dict(page_size=101)).status_code==422
    assert client.get(url,headers=headers,params=dict(status='invalid')).status_code==422
    summary=client.get('/api/mail/customer-mapping',headers=headers).json()
    assert 'entries' not in summary and 'excluded_rows' not in summary
    assert store.get_state('customer_mapping')==before
    assert store.get_state('sync_progress')==dict(cursor=852)
    assert manager.state['enabled'] is False


def test_online_preview_preserves_original_sheet_and_row_numbers(tmp_path):
    from app.customer_online import OnlineCustomerMapping
    from app.mail_workspace import MailWorkspace
    from app.outlook_probe import MailStore
    class Source:
        def read(self,config):
            return dict(title='Customer Contact List',values=[['report'],['Customer Name','Customer Bank Name Reff','Email'],['FAF','Nani','dian@test.com'],['Missing bank','','invalid@test.com']])
    store=MailStore(tmp_path/'mail.sqlite3');store.set_state('online_credentials',dict(client_email='unused',private_key='unused'))
    ws=MailWorkspace(store);online=OnlineCustomerMapping(ws,Source())
    online.configure('https://docs.google.com/spreadsheets/d/customer_123/edit#gid=8',True,None)
    summary=online.refresh()
    assert 'excluded_rows' not in summary
    preview=ws.customer_mapping_preview(status='excluded')
    assert preview['rows'][0]['row']==4
    assert preview['rows'][0]['sheet']=='Customer Contact List'
    assert preview['rows'][0]['customer']=='Missing bank'
    assert b'Missing bank' not in store.path.read_bytes()


def test_one_to_many_and_empty_email_flags():
    raw=b'Customer Name,Customer Bank Name Reff,Email\nFAF,Nani,\nOther,Nani,\n'
    result=preview_contacts(parse_contacts(raw,'contacts.csv'))
    assert all('一对多关系' in row['flags'] and '未登记邮箱' in row['flags'] for row in result['rows'])
