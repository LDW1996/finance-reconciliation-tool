import base64
import json

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.google_sheets import SheetsClient, validate_credentials


def test_google_requests_follow_system_https_proxy(monkeypatch):
    from app import google_sheets
    monkeypatch.setattr(google_sheets,'getproxies',lambda:{'https':'http://127.0.0.1:7897'})
    client=SheetsClient(lambda:None);client._token=lambda:'token'
    captured=[]
    def request(*args,**kwargs):
        captured.append(kwargs)
        return httpx.Response(200,json={'sheets':[]})
    monkeypatch.setattr(httpx,'request',request)
    client._request('GET','test')
    assert captured[0]['proxy']=='http://127.0.0.1:7897'


def test_finance_machine_without_proxy_connects_directly(monkeypatch):
    from app import google_sheets
    monkeypatch.setattr(google_sheets,'getproxies',lambda:{})
    client=SheetsClient(lambda:None);client._token=lambda:'token'
    captured=[]
    def request(*args,**kwargs):
        captured.append(kwargs)
        return httpx.Response(200,json={'sheets':[]})
    monkeypatch.setattr(httpx,'request',request)
    client._request('GET','test')
    assert google_sheets.google_proxy() is None
    assert captured[0]['proxy'] is None


def test_proxy_selection_does_not_require_socks_dependency(monkeypatch):
    from app import google_sheets
    monkeypatch.setattr(google_sheets,'getproxies',lambda:{'https':'socks5://127.0.0.1:7897'})
    with pytest.raises(ValueError,match='HTTP'):google_sheets.google_proxy()


@pytest.mark.parametrize('value,expected',[('31 Agu 2026','2026-08-31'),('13 Mei 2026','2026-05-13'),
                                        ('21 Okt 2026','2026-10-21'),('5 Des 2026','2026-12-05'),
                                        ('12 Agustus 2026','2026-08-12'),('3 Januari 2026','2026-01-03')])
def test_indonesian_google_date_display_is_parsed(value,expected):
    from app.mail_matching import _date
    assert _date(value)==expected


def test_assertion_is_signed_locally_and_tokens_do_not_follow_file_urls(monkeypatch):
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    secret=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode()
    raw=json.dumps(dict(type='service_account',client_email='finance@test.iam.gserviceaccount.com',private_key=secret,token_uri='https://evil.invalid'))
    credentials=validate_credentials(raw);calls=[]
    def exchange(url,**kwargs):
        assert url=='https://oauth2.googleapis.com/token' and kwargs['trust_env'] is False
        assertion=kwargs['data']['assertion'];head,payload,signed=assertion.split('.')
        key.public_key().verify(base64.urlsafe_b64decode(signed+'='*(-len(signed)%4)),f'{head}.{payload}'.encode(),padding.PKCS1v15(),hashes.SHA256())
        claims=json.loads(base64.urlsafe_b64decode(payload+'='*(-len(payload)%4)))
        assert claims['scope']=='https://www.googleapis.com/auth/spreadsheets'
        assert secret not in assertion
        calls.append(url);return httpx.Response(200,json={'access_token':'TOKEN','expires_in':3600})
    monkeypatch.setattr(httpx,'post',exchange)
    client=SheetsClient(lambda:credentials)
    assert client._token()=='TOKEN' and client._token()=='TOKEN'
    assert len(calls)==1


def test_literal_write_uses_stable_sheet_id_and_only_selected_cells():
    client=SheetsClient(lambda:None);calls=[]
    client._request=lambda method,path,**kwargs:calls.append((method,path,kwargs)) or {}
    client.write({'sheet_id':7,'spreadsheet_id':'test'},[(2,26,'=IMPORTXML("bad")'),(2,27,100)],26)
    requests=calls[0][2]['json']['requests']
    assert requests[0]['appendDimension']['length']==2
    first=requests[1]['updateCells']
    assert first['range']['sheetId']==7 and first['range']['startRowIndex']==2
    assert first['rows'][0]['values'][0]['userEnteredValue']=={'stringValue':'=IMPORTXML("bad")'}
    assert first['fields']=='userEnteredValue'


def test_read_preserves_formatted_references_and_fetches_formulas():
    client=SheetsClient(lambda:None);calls=[]
    def request(method,path,**kwargs):
        calls.append((path,kwargs))
        if path=='test':return {'sheets':[{'properties':{'sheetId':7,'title':"Bank's",'gridProperties':{'columnCount':26}}}]}
        if kwargs['params']['valueRenderOption']=='FORMULA':return {'values':[['=A1']]}
        return {'values':[['000123']]}
    client._request=request
    data=client.read({'sheet_id':7,'spreadsheet_id':'test'})
    assert data['values'][0][0]=='000123' and data['formulas'][0][0]=='=A1'
    assert '%27Bank%27%27s%27' in calls[1][0]


def test_read_permission_error_is_actionable_and_hides_server_details(monkeypatch):
    client=SheetsClient(lambda:None);client._token=lambda:'secret'
    monkeypatch.setattr(httpx,'request',lambda *args,**kwargs:httpx.Response(403,text='private server details'))
    with pytest.raises(ValueError,match='共享') as error:client.read({'spreadsheet_id':'test','sheet_id':7})
    assert 'secret' not in str(error.value) and 'private server' not in str(error.value)
