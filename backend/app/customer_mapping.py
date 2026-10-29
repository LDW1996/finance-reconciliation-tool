"""Explicit customer/payer/email relationships; never modify original evidence."""
from collections import defaultdict
import csv
from email.utils import getaddresses
from hashlib import sha256
from io import BytesIO, StringIO
import json
from pathlib import Path
import re
from zipfile import ZipFile

from .mail_matching import _name

MAX_BYTES=5*1024**2
MAX_ROWS=20000
HEADERS={
    'customer':('Customer Name','客户名称','客户公司','客户'),
    'bank_name':('Customer Bank Name Reff','Customer Bank Name Ref','Customer Bank Name','银行付款人','付款人','银行户名'),
    'email':('Email','E-mail','邮箱','客户邮箱'),
}


def emails(value):
    text=str(value or '').replace(';',',').replace('；',',').replace('\n',',')
    return list(dict.fromkeys(address.casefold() for _,address in getaddresses([text])
                             if re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+',address)))


def header(value):return re.sub(r'[^\w]','',str(value or '').casefold())


def parse_contacts(raw,name,sheet_name=None):
    if not raw or len(raw)>MAX_BYTES:raise ValueError('客户台账不能为空，且不能超过5MB。')
    suffix=Path(name).suffix.lower();book=None
    try:
        if suffix=='.xlsx':
            from openpyxl import load_workbook
            with ZipFile(BytesIO(raw)) as archive:
                if sum(item.file_size for item in archive.infolist())>100*1024**2:
                    raise ValueError('客户台账解压后过大，请拆分文件。')
            book=load_workbook(BytesIO(raw),read_only=True,data_only=True)
            sheets=book.sheetnames
            if sheet_name and sheet_name not in sheets:raise ValueError('找不到指定的客户台账工作表。')
            selected=sheet_name or sheets[0]
            rows=book[selected].iter_rows(max_col=30,values_only=True)
        elif suffix=='.csv':
            sheets=['CSV'];selected='CSV';text=None
            for encoding in ('utf-8-sig','gb18030'):
                try:text=raw.decode(encoding);break
                except UnicodeDecodeError:pass
            if text is None:raise ValueError('客户台账编码无法识别，请导出UTF-8 CSV或XLSX。')
            try:dialect=csv.Sniffer().sniff(text[:8192],delimiters=',;\t')
            except csv.Error:dialect=csv.excel
            rows=csv.reader(StringIO(text),dialect)
        else:raise ValueError('请选择XLSX或CSV客户台账文件。')
        columns=None;entries=[];skipped=0;seen=set()
        aliases={header(alias):key for key,values in HEADERS.items() for alias in values}
        for number,row in enumerate(rows,1):
            if number>MAX_ROWS+20:raise ValueError('客户台账超过20000行，请拆分文件。')
            if columns is None:
                found={aliases[header(value)]:index for index,value in enumerate(row) if header(value) in aliases}
                if 'customer' in found and 'bank_name' in found:columns=found
                elif number>=20:raise ValueError('未找到客户名称和银行付款人列，请检查表头。')
                continue
            if not any(str(v or '').strip() for v in row):continue
            def value(key):
                index=columns.get(key)
                return str(row[index] or '').strip() if index is not None and index<len(row) else ''
            customer,bank,email=value('customer'),value('bank_name'),value('email')
            addresses=emails(email)
            if not customer or not bank or len(customer)>300 or len(bank)>300 or (email and not addresses):
                skipped+=1;continue
            signature=(customer,bank,tuple(addresses))
            if signature in seen:continue
            seen.add(signature)
            entries.append(dict(customer=customer,bank_name=bank,emails=addresses,row=number,sheet=selected))
        if not columns or not entries:raise ValueError('客户台账没有有效映射；每行至少需要客户名称和银行付款人，邮箱可留空。')
        revision=sha256(json.dumps(entries,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        return dict(name=name,sheet=selected,available_sheets=sheets,entries=entries,entries_count=len(entries),skipped=skipped,revision=revision,enabled=True)
    except ValueError:raise
    except Exception as error:raise ValueError('客户台账无法解析，请检查文件格式或重新导出。') from error
    finally:
        if book:book.close()


class MappingIndex:
    def __init__(self,entries):
        self.by_payer=defaultdict(list);self.by_email=defaultdict(set)
        for row in entries:
            for key in {_name(row['bank_name']),_name(row['customer'])}:
                if key:self.by_payer[key].append(row)
            for address in row['emails']:self.by_email[address].add((_name(row['customer']),_name(row['bank_name'])))

    def match(self,payer,group):
        rows=self.by_payer.get(_name(payer),[])
        if not rows:return None
        shared_payer=len({(_name(row['customer']),_name(row['bank_name'])) for row in rows})>1
        matches=[];points=0;ambiguous=False
        for payment in group:
            name=_name(payment.get('payer'));senders=set(emails(payment.get('sender')))
            for row in rows:
                aliases={_name(row['customer']),_name(row['bank_name'])}
                # A concrete, unrelated payer must not be overridden by email.
                if name and name not in aliases:continue
                matched=senders.intersection(row['emails'])
                if not name and not matched:continue
                shared=shared_payer or any(len(self.by_email[address])>1 for address in matched)
                score=10 if shared else 22 if name else 24
                if row not in matches:matches.append(row)
                points=max(points,score);ambiguous=ambiguous or shared
        if not matches:return None
        # Any ambiguous path lowers the whole relationship, not just one row.
        return dict(points=10 if ambiguous else points,ambiguous=ambiguous,rows=matches,
                    label='客户台账映射（共享关系，需核对）' if ambiguous else '客户台账映射')
