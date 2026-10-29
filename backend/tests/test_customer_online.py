from copy import deepcopy
import pytest
from app.outlook_probe import MailStore
from app.mail_workspace import MailWorkspace
from app.customer_online import OnlineCustomerMapping

URL='https://docs.google.com/spreadsheets/d/customer_123/edit#gid=8'
BANK='https://docs.google.com/spreadsheets/d/bank_123/edit#gid=7'


class FakeSheets:
    def __init__(self):
        self.values=[['Customer Name','Customer Bank Name Reff','Email'],['FAF','Nani Desman','dian@faf.test']]
        self.calls=[];self.error=None;self.during_read=None
    def read(self,config):
        self.calls.append(deepcopy(config))
        if self.during_read:self.during_read()
        if self.error:raise self.error
        return dict(title='Customer Contacts',values=deepcopy(self.values),column_count=6)
    def write(self,*args):raise AssertionError('Customer table must never be written')


def setup(tmp_path):
    store=MailStore(tmp_path/'mail.sqlite3');ws=MailWorkspace(store);client=FakeSheets()
    store.set_state('online_credentials',dict(client_email='shared@example.test',private_key='not-used-by-fake'))
    online=OnlineCustomerMapping(ws,client)
    online.configure(URL,True,None)
    return online,ws,client


def test_customer_online_independent_source_and_mapping_refresh(tmp_path):
    online,ws,client=setup(tmp_path)
    ws.store.set_state('online_config',dict(spreadsheet_url=BANK,sheet_id=7,currency='IDR'))
    before=ws.store.get_state('online_config')
    metadata=online.refresh()
    assert metadata['entries_count']==1 and metadata['enabled']
    assert metadata['sheet']=='Customer Contacts'
    assert client.calls[0]['sheet_id']==8
    assert ws.store.get_state('online_config')==before
    assert online.config()['last_read_at']==metadata['imported_at']
    assert online.config()['service_email']=='shared@example.test'
    assert b'dian@faf.test' not in ws.store.path.read_bytes()


@pytest.mark.parametrize('mode',['network','format','empty'])
def test_failed_refresh_preserves_previous_mapping_and_last_success(tmp_path,mode):
    online,ws,client=setup(tmp_path);old=online.refresh();saved=ws.store.get_state('customer_mapping')
    if mode=='network':client.error=ValueError('无访问权限，请检查共享权限。')
    elif mode=='format':client.values=[['bad','header'],['a','b']]
    else:client.values=[]
    with pytest.raises(ValueError):online.refresh()
    assert ws.store.get_state('customer_mapping')==saved
    assert online.config()['last_read_at']==old['imported_at']
    assert online.config()['last_error']


def test_changed_source_keeps_active_data_until_success(tmp_path):
    online,ws,client=setup(tmp_path);old=online.refresh()
    online.configure(URL.replace('gid=8','gid=9'),True,None)
    assert ws.customer_mapping()==old
    assert online.config()['last_read_at'] is None
    assert ws.customer_mapping()['online']['sheet_id']==8


def test_stale_refresh_cannot_overwrite_mapping_changed_during_network_read(tmp_path):
    online,ws,client=setup(tmp_path);online.refresh()
    client.during_read=lambda:ws.enable_customer_mapping(False)
    with pytest.raises(ValueError,match='已变化'):online.refresh()
    assert not ws.customer_mapping()['enabled']


def test_read_only_client_forbids_write_and_does_not_fetch_formulas():
    from app.google_sheets import SheetsClient
    client=SheetsClient(lambda:None,read_only=True);calls=[]
    def request(method,path,**kwargs):
        calls.append((method,kwargs))
        if len(calls)==1:return dict(sheets=[dict(properties=dict(sheetId=8,title='Contacts',gridProperties=dict(columnCount=3,rowCount=3)))])
        return dict(values=[['Customer Name']])
    client._request=request
    assert client.read(dict(spreadsheet_id='test',sheet_id=8))['values']
    assert len(calls)==2 and all(method=='GET' for method,_ in calls)
    with pytest.raises(ValueError):client.write({},[],3)


def test_independent_authorization_never_replaces_bank_credentials(tmp_path,monkeypatch):
    import app.customer_online as module
    online,ws,client=setup(tmp_path);bank=ws.store.get_state('online_credentials')
    own=dict(client_email='own@example.test',private_key='own-private')
    monkeypatch.setattr(module,'validate_credentials',lambda raw:own)
    result=online.configure(URL,False,b'valid-test-key')
    assert result['service_email']=='own@example.test' and not result['use_shared']
    assert ws.store.get_state('online_credentials')==bank
    assert online._credentials()==own
    assert 'private_key' not in str(result)
    online.configure(URL,True,None)
    assert online._credentials()==bank


def test_failed_atomic_commit_rolls_back_mapping_and_success_timestamp(tmp_path,monkeypatch):
    online,ws,client=setup(tmp_path);online.refresh()
    old=ws.store.get_state('customer_mapping');last=online.config()['last_read_at']
    client.values.append(['Other','Another Payer','other@test.com'])
    original=ws.store.encode;failed=False
    def fail(value):
        nonlocal failed
        if isinstance(value,dict) and value.get('last_read_at') not in (None,last) and value.get('last_error')=='' and not failed:
            failed=True;raise RuntimeError('simulated write failure')
        return original(value)
    monkeypatch.setattr(ws.store,'encode',fail)
    with pytest.raises(ValueError):online.refresh()
    assert failed and ws.store.get_state('customer_mapping')==old
    assert online.config()['last_read_at']==last


def test_customer_oauth_scope_is_readonly(monkeypatch):
    import base64,json,httpx
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    from app.google_sheets import SheetsClient
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    secret=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode()
    credentials=dict(client_email='customer@test.iam.gserviceaccount.com',private_key=secret)
    def exchange(url,**kwargs):
        payload=kwargs['data']['assertion'].split('.')[1]
        claims=json.loads(base64.urlsafe_b64decode(payload+'='*(-len(payload)%4)))
        assert claims['scope']=='https://www.googleapis.com/auth/spreadsheets.readonly'
        return httpx.Response(200,json={'access_token':'read-token','expires_in':3600})
    monkeypatch.setattr(httpx,'post',exchange)
    assert SheetsClient(lambda:credentials,read_only=True)._token()=='read-token'


def test_online_customer_api_and_failure_retention(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app import mail_tasks
    online,ws,sheets=setup(tmp_path)
    manager=mail_tasks.MailManager(ws.store,reader=lambda *x:dict(rows=[],errors=[],partial=False))
    manager.customer_online=online
    monkeypatch.setattr(mail_tasks,'_manager',manager)
    client=TestClient(app);headers={'X-Finance-Local':'1'};prefix='/api/mail/customer-mapping/online'
    assert client.get(prefix+'/config').status_code==403
    assert client.post(prefix+'/config',headers=headers,data=dict(spreadsheet_url=URL,use_shared='true')).status_code==200
    success=client.post(prefix+'/refresh',headers=headers)
    assert success.status_code==200 and success.json()['entries_count']==1
    sheets.error=ValueError('无读取权限')
    assert client.post(prefix+'/refresh',headers=headers).status_code==409
    assert client.get('/api/mail/customer-mapping',headers=headers).json()==success.json()
    response=client.get(prefix+'/config',headers=headers)
    assert response.json()['last_error']=='无读取权限'
    assert 'private_key' not in response.text
    assert client.post(prefix+'/config',headers=headers,data=dict(spreadsheet_url='https://evil.test/',use_shared='true')).status_code==400
