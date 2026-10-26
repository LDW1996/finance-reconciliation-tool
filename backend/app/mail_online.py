"""Online ledger adapter with encrypted durable write-ahead audit records."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from decimal import Decimal, InvalidOperation
from threading import RLock
from uuid import uuid4

import openpyxl

from .google_sheets import SheetsClient, sheet_source, validate_credentials
from .mail_matching import parse_ledger, _header, _ALIASES
from .mail_workspace import signature, translated


def now():return datetime.now(timezone.utc).isoformat()


def cell(values,row,col):
    return values[row][col] if row<len(values) and col<len(values[row]) else ''


def same_values(left,right):
    def normalized(values):
        result=[str(v if v is not None else '') for v in values]
        if len(result)>1:
            try:result[1]=format(Decimal(result[1]).normalize(),'f')
            except InvalidOperation:pass
        return result
    return normalized(left)==normalized(right)


def fingerprint(receipt):
    return [str(receipt.get(k) or '') for k in ('ref','date','amount','currency','payer','description','status')]


def dataset(snapshot,currency):
    workbook=openpyxl.Workbook();sheet=workbook.active;sheet.title='Online Ledger'
    for values in snapshot['values']:
        sheet.append(values)
    # Google display text must not be reinterpreted as Excel formulas.
    for row in sheet:
        for item in row:
            if isinstance(item.value,str):item.data_type='s'
    out=BytesIO();workbook.save(out);workbook.close()
    parsed=parse_ledger(out.getvalue(),'online.xlsx','Online Ledger',currency)
    parsed['sheet']=snapshot['title'];parsed['available_sheets']=[snapshot['title']]
    if not parsed['entries']:raise ValueError('在线表中未找到可识别的日期和收款金额，请检查表头及数据。')
    for receipt in parsed['entries']:
        receipt['id']=hashlib.sha256(json.dumps(fingerprint(receipt),ensure_ascii=False).encode()).hexdigest()
    return parsed,out.getvalue()


class OnlineLedger:
    def __init__(self,workspace,client=None):
        self.workspace=workspace;self.store=workspace.store;self.lock=RLock()
        self.client=client or SheetsClient(lambda:self.store.get_state('online_credentials',None))
        with self.store.db() as db:db.execute('CREATE TABLE IF NOT EXISTS mail_online_audit (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        for record in self._records():
            if record.get('status')=='prepared':
                record.update(status='uncertain',message='上次写入执行中断，是否已写入尚未确认，请先读取在线表核对。')
                self._audit(record)

    def config(self):
        config=self.store.get_state('online_config',{})
        credentials=self.store.get_state('online_credentials',{})
        return {**{k:config.get(k) for k in ('spreadsheet_url','sheet_id','currency','last_read_at')},
                'configured':bool(config and credentials),'service_email':credentials.get('client_email','')}

    def configure(self,url,currency,credential):
        config=sheet_source(url)
        if len(currency)!=3 or not currency.isalpha():raise ValueError('请选择有效的三位币种代码。')
        config['currency']=currency.upper()
        credentials=validate_credentials(credential) if credential else None
        with self.lock:
            if credentials:self.store.set_state('online_credentials',credentials)
            self.store.set_state('online_config',config)
        return self.config()

    def _config(self):
        config=self.store.get_state('online_config',{})
        if not config:raise ValueError('请先配置在线表链接和授权文件。')
        return config

    def refresh(self):
        with self.lock,self.workspace.lock:
            config=self._config();snapshot=self.client.read(config)
            parsed,raw=dataset(snapshot,config['currency'])
            identity=hashlib.sha256(f"online:{config['spreadsheet_id']}:{config['sheet_id']}:{config['currency']}".encode()).hexdigest()
            counts=Counter(r['id'] for r in parsed['entries'])
            for receipt in parsed['entries']:
                if counts[receipt['id']]>1:receipt['id']+=':'+str(receipt['row'])
            data={**parsed,'id':identity,'name':'在线流水.xlsx','entries_count':len(parsed['entries']),'imported_at':now(),
                  'online':{'spreadsheet_id':config['spreadsheet_id'],'sheet_id':config['sheet_id'],'url':config['spreadsheet_url'],'export_sheet':'Online Ledger'}}
            data['warnings']=[translated(w) for w in data.get('warnings',[])]
            with self.store.db() as db:
                previous=db.execute('SELECT id FROM mail_ledger LIMIT 1').fetchone()
                if not previous or previous[0]!=identity:db.execute('DELETE FROM mail_review')
                db.execute('DELETE FROM mail_ledger')
                db.execute('INSERT INTO mail_ledger VALUES(?,?,?)',(identity,self.store.encode(data),self.store.cipher.encrypt(raw)))
            self.workspace.cache=None
            config['last_read_at']=data['imported_at'];self.store.set_state('online_config',config)
            return self.workspace.ledger()

    def _audit(self,record):
        with self.store.db() as db:
            db.execute('INSERT INTO mail_online_audit VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                       (record['id'],self.store.encode(record)))

    def _records(self):
        with self.store.db() as db:return [self.store.decode(r[0]) for r in db.execute('SELECT payload FROM mail_online_audit ORDER BY rowid DESC')]

    def history(self):return {'items':self._records()[:200]}

    def writeback(self,ledger_id,day_window=3,expected_count=None,confirmation_token=None):
        try:return self._writeback(ledger_id,day_window,expected_count,confirmation_token)
        except Exception as error:
            config=self.store.get_state('online_config',{})
            message=str(error) if isinstance(error,ValueError) else '在线表读取或处理失败，本批未完成；请检查连接后重读。'
            self._audit(dict(id=str(uuid4()),created_at=now(),ref='',row=None,before=None,after=None,status='failed',
                             spreadsheet_id=config.get('spreadsheet_id'),sheet_id=config.get('sheet_id'),message=message))
            raise ValueError(message) from None

    def _writeback(self,ledger_id,day_window=3,expected_count=None,confirmation_token=None):
        with self.lock,self.workspace.lock:
            config=self._config();data=self.workspace._dataset()
            source=data.get('online') if data else None
            if not source or data['id']!=ledger_id:raise ValueError('请先读取在线流水，不能将本地文件直接回写。')
            if any(source[k]!=config[k] for k in ('spreadsheet_id','sheet_id')):raise ValueError('在线表配置已更换，请重新读取并核对。')
            result=self.workspace.matching(scope='all',page_size=20000,day_window=day_window)
            if confirmation_token is not None and confirmation_token!=result['confirmation_token']:raise ValueError('确认内容已变化，请刷新后重新确认回写。')
            confirmed=[r for r in result['rows'] if r['review']['state']=='confirmed']
            if expected_count is not None and len(confirmed)!=expected_count:raise ValueError('已确认关联数量已变化，请刷新后重新确认回写。')
            if not confirmed:raise ValueError('没有已确认的邮件关联，请先核对并确认。')
            current=self.client.read(config);parsed,_=dataset(current,config['currency'])
            header=parsed['header_row']-1;values=current['values'];formulas=current['formulas']
            headers=values[header];columns=[];header_updates=[]
            ref_columns=[i for i,v in enumerate(headers) if _header(v) in {_header(a) for a in _ALIASES['ref']}]
            if len(ref_columns)!=1:raise ValueError('在线表需有唯一的流水参考号列（REF NO）。')
            raw_refs=Counter(str(cell(values,r,ref_columns[0])).strip() for r in range(header+1,len(values)))
            aliases=[('邮件关联备注','邮件关联备注（候选）'),('匹配分数',),('邮件检索信息','来源邮件')]
            next_col=max((len(row) for row in values),default=0)
            for names in aliases:
                hits=[i for i,v in enumerate(headers) if str(v).strip() in names]
                if len(hits)>1:raise ValueError('在线表存在重复邮件备注表头，请先整理后回写。')
                if hits:columns.append(hits[0])
                else:
                    columns.append(next_col);header_updates.append((header,next_col,names[0]));next_col+=1
            records=self._records();summary=dict(written=0,unchanged=0,failed=0,uncertain=0,items=[])
            pending=[];updates=[]
            for row in confirmed:
                receipt=row['receipt'];candidate=next((c for c in row['candidates'] if c['payment_id']==row['review']['payment_id']),None)
                record=dict(id=str(uuid4()),created_at=now(),spreadsheet_id=config['spreadsheet_id'],sheet_id=config['sheet_id'],
                            ref=receipt['ref'],row=None,before=None,after=None,status='failed',message='')
                matches=[r for r in parsed['entries'] if r['ref']==receipt['ref']]
                try:
                    if not receipt['ref'] or len(matches)!=1 or raw_refs[receipt['ref']]!=1:raise ValueError('流水参考号缺失、重复或已删除，请人工核对。')
                    target=matches[0];record['row']=target['row']
                    if fingerprint(target)!=fingerprint(receipt):raise ValueError('在线流水日期、金额、付款人、描述或核销状态已变化，请重新读取核对。')
                    if not candidate:raise ValueError('已确认的邮件证据不存在，请重新核对。')
                    r=target['row']-1;before=[cell(values,r,c) for c in columns]
                    after=[f"已确认关联：{candidate['remark']}",candidate['score'],candidate['search_info']]
                    record.update(before=before,after=after)
                    if any(len(str(v))>45000 for v in after):raise ValueError('备注内容超过在线单元格长度限制，请缩短邮件信息。')
                    owned=any(old.get('spreadsheet_id')==config['spreadsheet_id'] and old.get('sheet_id')==config['sheet_id']
                              and old.get('ref')==receipt['ref'] and same_values(old.get('after') or [],before)
                              and old.get('owns_output') is True and old.get('status') in ('written','unchanged','uncertain') for old in records)
                    if same_values(before,after):
                        record.update(status='unchanged',owns_output=owned,message='在线内容已一致，无需重复写入。')
                    else:
                        if any(str(cell(formulas,r,c)).startswith('=') for c in columns):raise ValueError('目标备注含公式，未覆盖，请人工核对。')
                        if any(v not in ('',None) for v in before) and not owned:raise ValueError('目标备注已有其他内容，已保留，未覆盖。')
                        record.update(status='prepared',message='已保存写入前快照，等待提交。')
                        pending.append((record,r,receipt));updates.extend((r,c,v) for c,v in zip(columns,after))
                except ValueError as error:record['message']=str(error)
                self._audit(record)
                if record['status']!='prepared':
                    summary[record['status']]+=1;summary['items'].append({k:record[k] for k in ('ref','status','message')})
            if pending:
                # Re-read immediately before submitting; Sheets has no row-level CAS.
                # Abort the whole batch if either identity rows or cells changed.
                try:
                    latest=self.client.read(config)
                    if latest['values']!=values or latest['formulas']!=formulas:
                        raise ValueError('在线表在准备写入期间发生变化，本批未提交，请重新读取核对。')
                except Exception as error:
                    for record,_,_ in pending:
                        record.update(status='failed',message=str(error) if isinstance(error,ValueError) else '写入前核对失败，本批未提交。');self._audit(record)
                        summary['failed']+=1;summary['items'].append({k:record[k] for k in ('ref','status','message')})
                    return summary
                try:
                    for record,_,_ in pending:
                        record.update(attempted=True,owns_output=True);self._audit(record)
                    self.client.write(config,header_updates+updates,current['column_count'])
                    verified=self.client.read(config)
                    checked,_=dataset(verified,config['currency'])
                    for record,_,receipt in pending:
                        matches=[r for r in checked['entries'] if r['ref']==receipt['ref']]
                        good=len(matches)==1 and fingerprint(matches[0])==fingerprint(receipt)
                        if good:
                            number=matches[0]['row']-1
                            good=same_values([cell(verified['values'],number,c) for c in columns],record['after'])
                        record.update(status='written' if good else 'uncertain',message='已写入并读取核对一致。' if good else '已提交，但回读内容或流水位置不一致，请核对在线表；未自动重试。')
                except Exception as error:
                    for record,_,_ in pending:
                        detail=str(error) if isinstance(error,ValueError) else type(error).__name__
                        record.update(status='uncertain',message=f'写入或回读未完成（{detail}），结果待核对；再次操作会先读取，未自动重试。')
                for record,_,_ in pending:
                    self._audit(record);summary[record['status']]+=1;summary['items'].append({k:record[k] for k in ('ref','status','message')})
            return summary
