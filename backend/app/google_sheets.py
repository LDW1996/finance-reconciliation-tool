"""Small fixed-endpoint Sheets client. Credentials never leave the local store.

Service-account assertions are signed locally; only the assertion is exchanged
at Google's token endpoint. Writes use literal userEnteredValue, never formulas.
"""
import base64
import json
import re
import time
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import getproxies

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from openpyxl.utils import get_column_letter


def google_proxy():
    # urllib also discovers macOS system proxies / Windows Internet Settings.
    # httpx trust_env alone does not discover these desktop settings.
    proxies=getproxies()
    proxy=proxies.get('https') or proxies.get('http') or proxies.get('all')
    if proxy:
        if urlparse(proxy).scheme not in ('http','https'):
            raise ValueError('Google 连接需要 HTTP/HTTPS 代理，请在代理软件中启用系统 HTTP/HTTPS 代理。')
        return proxy
    if proxies.get('socks'):
        raise ValueError('当前仅检测到 SOCKS 代理，请在代理软件中启用系统 HTTP/HTTPS 代理。')
    return None


def sheet_source(url):
    parsed=urlparse(url.strip())
    match=re.fullmatch(r'/spreadsheets/d/([A-Za-z0-9_-]+)/?(?:edit/?)?',parsed.path)
    if parsed.scheme!='https' or parsed.netloc!='docs.google.com' or not match:
        raise ValueError('请填写 Google 表格链接，不能使用其他网站地址。')
    gid=(parse_qs(parsed.fragment).get('gid') or parse_qs(parsed.query).get('gid') or [''])[0]
    if not re.fullmatch(r'\d{1,12}',gid):raise ValueError('链接需包含工作表 gid，请复制目标工作表的完整链接。')
    return dict(spreadsheet_id=match[1],sheet_id=int(gid),spreadsheet_url=f'https://docs.google.com/spreadsheets/d/{match[1]}/edit#gid={int(gid)}')


def validate_credentials(raw):
    try:
        data=json.loads(raw)
        assert isinstance(data,dict) and data.get('type')=='service_account'
        assert re.fullmatch(r'[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+\.gserviceaccount\.com',data['client_email'])
        key=serialization.load_pem_private_key(data['private_key'].encode(),password=None)
        assert isinstance(key,rsa.RSAPrivateKey) and key.key_size>=2048
        return {k:data[k] for k in ('type','client_email','private_key')}
    except Exception:raise ValueError('授权文件无效，请选择 Google 服务账号的 JSON 密钥文件。') from None


class SheetsClient:
    def __init__(self,credential_provider,read_only=False):
        self.read_only=read_only
        self.credentials=credential_provider;self.token='';self.expires=0;self.identity=None

    def _token(self):
        credentials=self.credentials()
        if not credentials:raise ValueError('请先配置 Google 服务账号授权文件，并将在线表共享给该账号。')
        identity=(credentials['client_email'],credentials['private_key'])
        if self.identity==identity and time.time()<self.expires:return self.token
        def encode(value):return base64.urlsafe_b64encode(value).rstrip(b'=')
        now=int(time.time())
        claims=dict(iss=credentials['client_email'],scope='https://www.googleapis.com/auth/spreadsheets'+('.readonly' if self.read_only else ''),
                    aud='https://oauth2.googleapis.com/token',iat=now,exp=now+3600)
        content=b'.'.join(encode(json.dumps(v,separators=(',',':')).encode()) for v in ({'alg':'RS256','typ':'JWT'},claims))
        key=serialization.load_pem_private_key(credentials['private_key'].encode(),password=None)
        assertion=content+b'.'+encode(key.sign(content,padding.PKCS1v15(),hashes.SHA256()))
        try:
            response=httpx.post('https://oauth2.googleapis.com/token',data={'grant_type':'urn:ietf:params:oauth:grant-type:jwt-bearer','assertion':assertion.decode()},timeout=30,trust_env=False,proxy=google_proxy())
            if response.status_code!=200:raise ValueError('Google 授权失败，请检查服务账号密钥、系统时间和账号状态。')
            data=response.json();self.token=data['access_token'];self.expires=now+min(int(data.get('expires_in',3600)),3600)-60;self.identity=identity
            return self.token
        except httpx.HTTPError as error:raise ValueError(f'无法连接 Google 授权服务（{type(error).__name__}），请检查网络及系统 HTTP/HTTPS 代理。') from None

    def _request(self,method,path,**kwargs):
        token=self._token()
        try:
            response=httpx.request(method,'https://sheets.googleapis.com/v4/spreadsheets/'+path,
                                   headers={'Authorization':'Bearer '+token},timeout=60,trust_env=False,proxy=google_proxy(),**kwargs)
        except httpx.HTTPError:raise ValueError('Google 表格连接超时或中断。写入请求若已发送，需读取在线表核对结果。') from None
        if response.status_code!=200:
            if response.status_code==403 and self.read_only:
                raise ValueError('无读取权限，请启用 Google Sheets API，并将客户台账以查看权限共享给服务账号。')
            if response.status_code==401:self.expires=0
            errors={401:'授权已失效，请检查授权文件。',403:'无访问权限，请启用 Google Sheets API，并将目标表以编辑权限共享给服务账号。',
                    404:'找不到目标表或工作表，请检查链接和共享权限。',429:'Google 请求过于频繁，请稍后重试。'}
            raise ValueError(errors.get(response.status_code,f'Google 表格请求失败（HTTP {response.status_code}），请稍后读取核对。'))
        if len(response.content)>30*1024*1024:raise ValueError('在线表数据超过 30 MB，请拆分工作表。')
        return response.json()

    def read(self,config):
        sid=config['spreadsheet_id']
        metadata=self._request('GET',sid,params={'fields':'sheets.properties'})
        props=next((s['properties'] for s in metadata.get('sheets',[]) if s['properties']['sheetId']==config['sheet_id']),None)
        if not props:raise ValueError('找不到链接中的工作表 gid，请重新选择工作表。')
        columns=props['gridProperties']['columnCount']
        if columns>1000:raise ValueError('在线工作表超过 1000 列，请拆分后读取。')
        last_row=min(props['gridProperties'].get('rowCount',20022),20022)
        title=props['title'];range_name="'"+title.replace("'","''")+f"'!A1:{get_column_letter(columns)}{last_row}"
        path=sid+'/values/'+quote(range_name,safe='')
        values=self._request('GET',path,params={'valueRenderOption':'FORMATTED_VALUE'}).get('values',[])
        if len(values)>20020:raise ValueError('在线工作表超过 20,000 行，请缩小工作表范围。')
        formulas=[] if self.read_only else self._request('GET',path,params={'valueRenderOption':'FORMULA','dateTimeRenderOption':'SERIAL_NUMBER'}).get('values',[])
        return dict(title=title,values=values,formulas=formulas,column_count=columns)

    def write(self,config,updates,column_count):
        if self.read_only:raise ValueError('客户台账为只读来源，不允许写入。')
        needed=max((col+1 for _,col,_ in updates),default=0);requests=[]
        if needed>column_count:requests.append({'appendDimension':{'sheetId':config['sheet_id'],'dimension':'COLUMNS','length':needed-column_count}})
        for row,col,value in updates:
            literal={'numberValue':value} if isinstance(value,(int,float)) else {'stringValue':str(value)}
            requests.append({'updateCells':{'range':{'sheetId':config['sheet_id'],'startRowIndex':row,'endRowIndex':row+1,
                                                    'startColumnIndex':col,'endColumnIndex':col+1},
                                           'rows':[{'values':[{'userEnteredValue':literal}]}],'fields':'userEnteredValue'}})
        if requests:self._request('POST',config['spreadsheet_id']+':batchUpdate',json={'requests':requests})
