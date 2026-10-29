"""Read-only online contact imports, independent from bank receipts/writeback."""
import csv
from datetime import datetime,timezone
from hashlib import sha256
from io import StringIO
import json
from threading import RLock

from .customer_mapping import parse_contacts
from .google_sheets import SheetsClient,sheet_source,validate_credentials


def now():return datetime.now(timezone.utc).isoformat()


class OnlineCustomerMapping:
    def __init__(self,workspace,client=None):
        self.workspace=workspace;self.store=workspace.store;self.lock=RLock()
        self.client=client or SheetsClient(self._credentials,read_only=True)

    def _credentials(self):
        config=self.store.get_state('customer_online_config',{})
        key='online_credentials' if config.get('use_shared',True) else 'customer_online_credentials'
        return self.store.get_state(key,None)

    def config(self):
        config=self.store.get_state('customer_online_config',{})
        credentials=self._credentials() or {}
        return {**{k:config.get(k) for k in ('spreadsheet_url','sheet_id','last_read_at','last_attempt_at','last_error')},
                'use_shared':config.get('use_shared',True),'configured':bool(config and credentials),
                'service_email':credentials.get('client_email','')}

    def configure(self,url,use_shared=True,credential=None):
        config=sheet_source(url)
        if use_shared and credential:raise ValueError('共用银行流水授权时无需上传独立密钥。')
        credentials=validate_credentials(credential) if credential else None
        with self.lock:
            key='online_credentials' if use_shared else 'customer_online_credentials'
            if not (credentials or self.store.get_state(key,None)):
                raise ValueError('请先配置银行流水授权，或选择独立授权并上传服务账号JSON。')
            previous=self.store.get_state('customer_online_config',{})
            if all(previous.get(k)==config[k] for k in ('spreadsheet_id','sheet_id')):
                config['last_read_at']=previous.get('last_read_at')
            config.update(use_shared=use_shared,last_error='')
            with self.store.db() as db:
                if credentials:db.execute('INSERT OR REPLACE INTO mail_state VALUES(?,?)',('customer_online_credentials',self.store.encode(credentials)))
                db.execute('INSERT OR REPLACE INTO mail_state VALUES(?,?)',('customer_online_config',self.store.encode(config)))
        return self.config()

    def refresh(self):
        with self.lock:
            config=self.store.get_state('customer_online_config',{})
            if not config or not self._credentials():raise ValueError('请先配置客户台账的在线来源和授权。')
            previous=self.store.get_state('customer_mapping',{})
            try:
                snapshot=self.client.read(config)
                text=StringIO();csv.writer(text).writerows(snapshot['values'])
                data=parse_contacts(text.getvalue().encode('utf-8'),'在线客户台账.csv')
                data['sheet']=snapshot['title'];data['available_sheets']=[snapshot['title']]
                for entry in data['entries']:entry['sheet']=snapshot['title']
                data['revision']=sha256(json.dumps(data['entries'],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
                data['imported_at']=now()
                data['online']={k:config[k] for k in ('spreadsheet_id','sheet_id','spreadsheet_url')}
                config.update(last_read_at=data['imported_at'],last_attempt_at=data['imported_at'],last_error='')
                with self.workspace.lock:
                    if self.store.get_state('customer_mapping',{})!=previous:
                        raise ValueError('客户映射在读取期间已变化，本次未替换；请核对当前台账后重新读取。')
                    with self.store.db() as db:
                        db.execute('INSERT OR REPLACE INTO mail_state VALUES(?,?)',('customer_mapping',self.store.encode(data)))
                        db.execute('INSERT OR REPLACE INTO mail_state VALUES(?,?)',('customer_online_config',self.store.encode(config)))
                    self.workspace.cache=None
                return {k:v for k,v in data.items() if k!='entries'}
            except Exception as error:
                # Never expose raw HTTP responses or credentials. Persist error
                # status separately; last successful data/timestamp stay intact.
                message=str(error) if isinstance(error,ValueError) else '在线客户台账读取失败，请检查网络、共享权限和表头后重试。'
                saved=self.store.get_state('customer_online_config',{})
                saved.update(last_attempt_at=now(),last_error=message)
                self.store.set_state('customer_online_config',saved)
                raise ValueError(message) from None
