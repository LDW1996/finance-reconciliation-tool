"""Read-only classic Outlook adapter and local mail parsing for connection trials."""

import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path

from cryptography.fernet import Fernet
from .mail_attachments import capture_attachment, MAX_MAIL_BYTES, MAX_ATTACHMENT_BYTES, MAX_ATTACHMENTS


def candidates(text):
    found = []
    for value in re.findall(r'(?<![A-Za-z0-9])[A-Za-z0-9]{8,40}(?![A-Za-z0-9])', text.upper()):
        if any(c.isdigit() for c in value) and (not value.isdigit() or len(value) >= 10) and value not in found:
            found.append(value)
    return found


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]; self.hidden=0
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style'): self.hidden+=1
        if tag in ('br','p','div','tr'): self.parts.append('\n')
        if tag == 'td': self.parts.append(' ')
    def handle_endtag(self, tag):
        if tag in ('script','style'): self.hidden=max(0,self.hidden-1)
    def handle_data(self, data):
        if not self.hidden: self.parts.append(data)


def mail_sections(body):
    """Conservative boundaries; retain the original separately for review."""
    lines = body.splitlines(keepends=True)
    boundary = len(lines)
    for i, line in enumerate(lines):
        if re.match(r'^\s*(?:-{2,}\s*(?:Original Message|Forwarded message|原始邮件)|On .+wrote:|在.+写道[：:]|>\s*\S)', line, re.I):
            boundary = i
            break
        if re.match(r'^\s*(?:\*\s*)?(?:From|发件人)\s*[:：]', line, re.I):
            following = ''.join(lines[i+1:i+9])
            if re.search(r'^\s*(?:\*\s*)?(?:Sent|Date|发送时间|日期)\s*[:：]', following, re.I | re.M) and re.search(r'^\s*(?:\*\s*)?(?:Subject|To|主题|收件人)\s*[:：]', following, re.I | re.M):
                boundary = i
                while boundary and (not lines[boundary-1].strip() or re.fullmatch(r'\s*(?:Original:|_{3,})\s*', lines[boundary-1], re.I)):
                    boundary -= 1
                break
    latest = lines[:boundary]
    signature = len(latest)
    for i, line in enumerate(latest):
        if re.fullmatch(r'\s*(?:--|(?:Thanks\s*(?:&|and)\s*)?(?:(?:Best|Kind|Warm)\s+)?Regards[,!.。]*|此致[,，]?|Sent from my (?:iPhone|iPad))\s*', line, re.I):
            signature = i
            break
    return dict(latest_body=''.join(latest[:signature]).strip(),
                signature=''.join(latest[signature:]).strip(),
                quoted_body=''.join(lines[boundary:]).strip())


def parse_msg(raw):
    if len(raw) > MAX_MAIL_BYTES: raise ValueError('邮件不能超过 25 MB')
    if not raw.startswith(bytes.fromhex('D0CF11E0A1B11AE1')):
        raise ValueError('无法读取 MSG：文件格式不正确或文件已损坏。')
    import extract_msg
    try:
        with extract_msg.openMsg(raw) as mail:
            body = mail.body or ''
            if not body and mail.htmlBody:
                parser = PlainHTML()
                parser.feed(mail.htmlBody.decode('utf-8', errors='replace'))
                body = ''.join(parser.parts)
            subject = str(mail.subject or '')
            assets = []
            for a in mail.attachments[:MAX_ATTACHMENTS]:
                name = str(a.longFilename or a.shortFilename or '未命名附件')
                try:
                    if name.lower().endswith(('.pdf', '.png', '.jpg', '.jpeg', '.bmp', '.webp', '.tif', '.tiff')):
                        content = a.data
                        assets.append(capture_attachment(name, content if isinstance(content, bytes) else None))
                    else: assets.append(capture_attachment(name))
                except Exception:
                    assets.append(capture_attachment(name))
            return dict(id=hashlib.sha256(raw).hexdigest(), source='msg', folder='导入样例',
                        subject=subject, sender=str(mail.sender or ''), received=str(mail.date or ''),
                        body=body[:200000], truncated=len(body)>200000,
                        attachments=[str(a.longFilename or a.shortFilename or '未命名附件') for a in mail.attachments],
                        _attachments=assets, attachment_warning='仅保留前 20 个附件。' if len(mail.attachments)>MAX_ATTACHMENTS else '',
                        candidates=candidates(subject+'\n'+body[:200000]), **mail_sections(body[:200000]))
    except Exception as error:
        raise ValueError('无法读取 MSG：文件可能损坏、加密或不是普通邮件，请从 Outlook 重新另存为 MSG。') from error


def parse_eml(raw):
    if len(raw) > MAX_MAIL_BYTES: raise ValueError('邮件不能超过 25 MB')
    mail=BytesParser(policy=policy.default).parsebytes(raw)
    part=mail.get_body(preferencelist=('plain','html'))
    body=str(part.get_content()) if part else ''
    if part and part.get_content_subtype()=='html':
        parser=PlainHTML(); parser.feed(body); body=''.join(parser.parts)
    subject=str(mail.get('Subject',''))
    # Include inline images; payment screenshots are often embedded in the body.
    parts=[p for p in mail.walk() if not p.is_multipart() and (p.get_filename() or p.get_content_maintype()=='image')]
    assets=[capture_attachment(p.get_filename() or f'inline-{i}.{p.get_content_subtype()}', p.get_payload(decode=True))
            for i,p in enumerate(parts[:MAX_ATTACHMENTS])]
    return dict(id=hashlib.sha256(raw).hexdigest(), source='eml', folder='导入样例',
                subject=subject, sender=str(mail.get('From','')), received=str(mail.get('Date','')),
                body=body[:200000], truncated=len(body)>200000,
                attachments=[str(p.get_filename()) for p in mail.iter_attachments() if p.get_filename()],
                _attachments=assets, attachment_warning='仅保留前 20 个附件。' if len(parts)>MAX_ATTACHMENTS else '',
                candidates=candidates(subject+'\n'+body[:200000]), **mail_sections(body[:200000]))


class MailStore:
    def __init__(self, path):
        self.path=Path(path); self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        key=self.path.with_suffix('.key')
        if not key.exists():
            if self.path.exists(): raise ValueError('邮件加密密钥缺失，请恢复原密钥，不能直接替换。')
            try:
                descriptor=os.open(key,os.O_CREAT | os.O_EXCL | os.O_WRONLY,0o600)
            except FileExistsError: pass
            else:
                with os.fdopen(descriptor,'wb') as handle: handle.write(Fernet.generate_key())
        self.cipher=Fernet(key.read_bytes())
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS mail (id TEXT PRIMARY KEY, payload TEXT NOT NULL, seen TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS mail_attachment (id TEXT PRIMARY KEY, mail_id TEXT NOT NULL, position INTEGER NOT NULL, metadata TEXT NOT NULL, content BLOB NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS attachment_mail ON mail_attachment(mail_id)')
            db.execute('CREATE TABLE IF NOT EXISTS mail_identity (fingerprint TEXT PRIMARY KEY, mail_id TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS mail_state (key TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS mail_payments (mail_id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS mail_payment_summary (mail_id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        try: self.path.chmod(0o600)
        except OSError: pass
    @contextmanager
    def db(self):
        connection=sqlite3.connect(self.path, timeout=10)
        try:
            with connection: yield connection
        finally: connection.close()
    def save(self, rows):
        new=0
        with self.db() as db:
            for row in rows:
                assets=row.get('_attachments', [])
                row={key:value for key,value in row.items() if key not in ('_attachments','attachment_details')}
                # Keep copies from distinct PST stores in one matching index. Never
                # merge using amount/business IDs, or incomplete attachment data.
                origin=dict(folder=row.get('folder',''),store=row.get('outlook_store',''))
                fingerprint=None
                if row.get('source')=='outlook' and row.get('internet_message_id') and not row.get('truncated') and not row.get('attachment_warning') and len(assets)==len(row.get('attachments',[])) and all(a.get('raw') for a in assets):
                    facts=[row.get(k,'') for k in ('internet_message_id','sender','subject','body')]
                    facts.append([(a['name'],hashlib.sha256(a['raw']).hexdigest()) for a in assets])
                    fingerprint=hashlib.sha256(json.dumps(facts,ensure_ascii=False).encode()).hexdigest()
                    match=db.execute('SELECT mail_id FROM mail_identity WHERE fingerprint=?',(fingerprint,)).fetchone()
                    if match:
                        canonical=db.execute('SELECT payload FROM mail WHERE id=?',(match[0],)).fetchone()
                        if canonical:
                            previous=self.decode(canonical[0])
                            sources=previous.get('sources',[dict(folder=previous.get('folder',''),store=previous.get('outlook_store',''))])
                            if origin not in sources: sources.append(origin)
                            previous['sources']=sources
                            db.execute('UPDATE mail SET payload=? WHERE id=?',(self.encode(previous),match[0]))
                            continue
                row.setdefault('sources',[origin])
                existed=db.execute('SELECT 1 FROM mail WHERE id=?',(row['id'],)).fetchone()
                new+=not bool(existed)
                db.execute('INSERT INTO mail VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, seen=excluded.seen',
                           (row['id'],self.cipher.encrypt(json.dumps(row,ensure_ascii=False).encode()).decode(),datetime.now(timezone.utc).isoformat()))
                if fingerprint:
                    db.execute('INSERT OR REPLACE INTO mail_identity VALUES(?,?)',(fingerprint,row['id']))
                for position, asset in enumerate(assets):
                    raw=asset.get('raw', b'')
                    digest=hashlib.sha256(raw).hexdigest()
                    identity=hashlib.sha256(f"{row['id']}|{position}|{asset['name']}|{digest}".encode()).hexdigest()
                    metadata={key:value for key,value in asset.items() if key!='raw'}
                    metadata.update(id=identity, sha256=digest)
                    previous=[(key,self.decode(value)) for key,value in db.execute('SELECT id,metadata FROM mail_attachment WHERE mail_id=? AND position=?',(row['id'],position))]
                    existing=next(((key,value) for key,value in previous if value['name']==asset['name']),None)
                    if existing:
                        key, old=existing
                        if not raw or old.get('sha256')==digest: continue
                        metadata['id']=key
                        db.execute('UPDATE mail_attachment SET metadata=?,content=? WHERE id=?',
                                   (self.encode(metadata),self.cipher.encrypt(raw),key))
                        continue
                    db.execute('INSERT OR IGNORE INTO mail_attachment VALUES(?,?,?,?,?)',
                               (identity,row['id'],position,self.encode(metadata),self.cipher.encrypt(raw)))
        return new
    def encode(self, value): return self.cipher.encrypt(json.dumps(value,ensure_ascii=False).encode()).decode()
    def decode(self, value): return json.loads(self.cipher.decrypt(value.encode()).decode('utf-8'))
    def rows(self, include_body=True):
        with self.db() as db:
            result=[]
            for value, in db.execute('SELECT payload FROM mail ORDER BY seen DESC, id LIMIT 500'):
                row=self.decode(value)
                if not include_body:
                    for field in ('body', 'latest_body', 'signature', 'quoted_body'): row.pop(field,None)
                result.append(row)
            return result
    def mail_count(self):
        with self.db() as db:return db.execute('SELECT COUNT(*) FROM mail').fetchone()[0]
    def message_page(self,page=1,page_size=50,keyword='',payment_filter='all'):
        keyword=keyword.strip().casefold()
        offset=(page-1)*page_size
        with self.db() as db:
            total_saved=db.execute('SELECT COUNT(*) FROM mail').fetchone()[0]
            if not keyword and payment_filter=='all':
                items=db.execute('SELECT payload FROM mail ORDER BY seen DESC,id LIMIT ? OFFSET ?',(page_size,offset)).fetchall()
                rows=[self.decode(payload) for payload, in items];total=total_saved
            else:
                rows=[];total=0
                items=db.execute('SELECT m.payload,s.payload FROM mail m LEFT JOIN mail_payment_summary s ON m.id=s.mail_id ORDER BY m.seen DESC,m.id')
                for payload,summary_payload in items:
                    summary=self.decode(summary_payload) if summary_payload else {}
                    status=summary.get('status','pending')
                    matches=(payment_filter=='all' or (payment_filter=='payment' and summary.get('count',0)>0) or payment_filter==status)
                    if not matches:continue
                    row=self.decode(payload)
                    fields=[row.get('subject',''),row.get('sender',''),*row.get('candidates',[]),*summary.get('payers',[]),*summary.get('dates',[]),*summary.get('business_ids',[])]
                    fields.extend(str(a.get('currency',''))+' '+str(a.get('amount','')) for a in summary.get('amounts',[]))
                    if keyword and keyword not in ' '.join(fields).casefold():continue
                    if offset<=total<offset+page_size:rows.append(row)
                    total+=1
            for row in rows:
                for field in ('body','latest_body','signature','quoted_body'):row.pop(field,None)
            return dict(rows=rows,total=total,total_saved=total_saved,page=page,page_size=page_size)
    def get(self, identity):
        with self.db() as db:
            row=db.execute('SELECT payload FROM mail WHERE id=?',(identity,)).fetchone()
            if not row: return None
            result=self.decode(row[0])
            result['attachment_details']=[self.decode(value) for value, in db.execute('SELECT metadata FROM mail_attachment WHERE mail_id=? ORDER BY position',(identity,))]
            return result
    def has_mail(self,identity):
        with self.db() as db:return db.execute('SELECT 1 FROM mail WHERE id=?',(identity,)).fetchone() is not None
    def attachment(self, mail_id, identity):
        with self.db() as db:
            row=db.execute('SELECT metadata,content FROM mail_attachment WHERE mail_id=? AND id=?',(mail_id,identity)).fetchone()
            return {**self.decode(row[0]), 'raw':self.cipher.decrypt(row[1])} if row else None
    def update_attachment(self, mail_id, identity, **changes):
        with self.db() as db:
            row=db.execute('SELECT metadata FROM mail_attachment WHERE mail_id=? AND id=?',(mail_id,identity)).fetchone()
            if row:
                db.execute('UPDATE mail_attachment SET metadata=? WHERE id=?', (self.encode({**self.decode(row[0]),**changes}),identity))
    def get_state(self, key, default=None):
        with self.db() as db:
            row=db.execute('SELECT payload FROM mail_state WHERE key=?',(key,)).fetchone()
            return self.decode(row[0]) if row else default
    def set_state(self, key, value):
        with self.db() as db:
            db.execute('INSERT INTO mail_state VALUES(?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload',(key,self.encode(value)))
    def set_payments(self, identity, payments, summary=None):
        with self.db() as db:
            db.execute('INSERT INTO mail_payments VALUES(?,?) ON CONFLICT(mail_id) DO UPDATE SET payload=excluded.payload',(identity,self.encode(payments)))
            db.execute('INSERT INTO mail_state VALUES(?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload',('match_revision',self.encode(datetime.now(timezone.utc).isoformat())))
            if summary is not None:db.execute('INSERT INTO mail_payment_summary VALUES(?,?) ON CONFLICT(mail_id) DO UPDATE SET payload=excluded.payload',(identity,self.encode(summary)))
            else:db.execute('DELETE FROM mail_payment_summary WHERE mail_id=?',(identity,))
    def payment_index(self, identities=None):
        from .mail_matching import effective_payment_date
        with self.db() as db:
            if identities is not None:
                if not identities:return {}
                query='SELECT mail_id,payload FROM mail_payments WHERE mail_id IN ('+','.join('?' for _ in identities)+')'
                rows=db.execute(query,identities)
            else:rows=db.execute('SELECT mail_id,payload FROM mail_payments')
            return {key:[effective_payment_date(p) for p in self.decode(payload)] for key,payload in rows}
    def payment_summaries(self,identities=None):
        with self.db() as db:
            if identities is not None:
                if not identities:return {}
                rows=db.execute('SELECT mail_id,payload FROM mail_payment_summary WHERE mail_id IN ('+','.join('?' for _ in identities)+')',identities)
            else:rows=db.execute('SELECT mail_id,payload FROM mail_payment_summary')
            return {key:self.decode(payload) for key,payload in rows}
    def set_payment_summary(self,identity,summary):
        with self.db() as db:db.execute('INSERT INTO mail_payment_summary VALUES(?,?) ON CONFLICT(mail_id) DO UPDATE SET payload=excluded.payload',(identity,self.encode(summary)))
    def payment_rows(self):
        from .mail_matching import effective_payment_date
        with self.db() as db:
            return [effective_payment_date(payment) for payload, in db.execute('SELECT payload FROM mail_payments') for payment in self.decode(payload)]
    def recoverable_mail_ids(self):
        with self.db() as db:
            unindexed=[key for key, in db.execute('SELECT id FROM mail WHERE id NOT IN (SELECT mail_id FROM mail_payments)')]
            pending=[key for key,payload in db.execute('SELECT mail_id,metadata FROM mail_attachment') if self.decode(payload)['status'] in ('pending','running')]
            return list(dict.fromkeys(unindexed+pending))
    def clear(self):
        with self.db() as db:
            db.execute('DELETE FROM mail_identity')
            db.execute('DELETE FROM mail_attachment')
            db.execute('DELETE FROM mail')
            db.execute('DELETE FROM mail_payments')
            db.execute('DELETE FROM mail_payment_summary')
            db.execute("DELETE FROM mail_state WHERE key IN ('sync_progress','match_revision')")


@contextmanager
def outlook_session():
    if sys.platform!='win32': raise ValueError('连接 Outlook 需要 Windows 和已打开、已登录的经典版 Outlook；当前电脑可导入 EML 样例验证解析。')
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        raise ValueError('缺少 Outlook 连接组件，请使用完整 Windows 打包版本。') from None
    pythoncom.CoInitialize()
    try:
        try:
            application=win32com.client.GetActiveObject('Outlook.Application')
            namespace=application.GetNamespace('MAPI')
        except Exception:
            raise ValueError('未能连接经典版 Outlook。请先打开 Outlook、完成登录，并以同一 Windows 用户及权限运行两个程序。新版 Outlook 不支持此连接方式。') from None
        yield namespace
    finally:
        pythoncom.CoUninitialize()


def list_folders():
    from .mail_scope import folder_tree
    with outlook_session() as session:
        return folder_tree(session)


def scan_folder(folder, store_id, cutoff, limit):
    rows=[]; errors=[]; partial=False; scan_bytes=0
    items=folder.Items
    items.Sort('[ReceivedTime]',True)
    # Restrict scanning work even when a folder contains non-mail items or failures.
    budget=min(items.Count,limit*3+1)
    for index in range(1,budget+1):
        try:
            item=items.Item(index)
            if item.Class!=43: continue
            received=item.ReceivedTime
            if received.tzinfo is None: received=received.astimezone()
            if received<cutoff: break
            if len(rows)>=limit:
                partial=True; break
            identity=str(item.EntryID)
            subject=str(item.Subject or '')
            body=str(item.Body or '')
            attachments=item.Attachments
            assets=[]; total_bytes=0
            for i in range(1,min(attachments.Count,MAX_ATTACHMENTS)+1):
                attachment=attachments.Item(i)
                name=str(attachment.FileName)
                size=int(getattr(attachment,'Size',0))
                asset=capture_attachment(name, size=size)
                if asset['status']=='failed' and scan_bytes+size>100*1024*1024:
                    asset.update(status='skipped',error='本次附件读取达到 100 MB，请缩小邮件读取范围后重试。')
                elif asset['status']=='failed' and size<=MAX_ATTACHMENT_BYTES and total_bytes+size<=MAX_MAIL_BYTES:
                    try:
                        # COM exposes attachment contents via SaveAsFile. The generated path
                        # never uses the supplied filename, and is removed immediately.
                        with tempfile.TemporaryDirectory(prefix='finance-mail-') as temp:
                            path=Path(temp)/'attachment.bin'
                            attachment.SaveAsFile(str(path))
                            if path.stat().st_size<=MAX_ATTACHMENT_BYTES and total_bytes+path.stat().st_size<=MAX_MAIL_BYTES and scan_bytes+path.stat().st_size<=100*1024*1024:
                                content=path.read_bytes()
                                total_bytes+=len(content)
                                scan_bytes+=len(content)
                                asset=capture_attachment(name, content)
                    except Exception: pass
                elif asset['status']=='failed' and total_bytes+size>MAX_MAIL_BYTES:
                    asset.update(status='skipped',error='本封邮件附件累计超过 25 MB。')
                assets.append(asset)
            row=dict(id=hashlib.sha256((store_id+'|'+identity).encode()).hexdigest(),source='outlook',
                     subject=subject,sender=outlook_sender(item),received=received.isoformat(),
                     body=body[:200000],truncated=len(body)>200000,
                     attachments=[str(attachments.Item(i).FileName) for i in range(1,min(attachments.Count,100)+1)],
                     _attachments=assets, attachment_warning='仅保留前 20 个附件。' if attachments.Count>MAX_ATTACHMENTS else '',
                     candidates=candidates(subject+'\n'+body[:200000]), **mail_sections(body[:200000]))
            try:
                row['internet_message_id']=str(item.PropertyAccessor.GetProperty('http://schemas.microsoft.com/mapi/proptag/0x1035001F') or '')
            except Exception: row['internet_message_id']=''
            rows.append(row)
        except Exception as error:
            errors.append(f'第 {index} 项读取失败（{type(error).__name__}）。请在 Outlook 检查该项及权限提示；其余邮件继续读取。')
    else:
        partial=items.Count>budget
    return dict(rows=rows,errors=errors,partial=partial)


def read_outlook(store_id, folder_id, cutoff, limit):
    with outlook_session() as session:
        try:
            folder=session.GetFolderFromID(folder_id,store_id)
            result=scan_folder(folder,store_id,cutoff,limit)
            for row in result['rows']: row['folder']=str(folder.FolderPath)
            return result
        except Exception as error:
            raise ValueError(f'文件夹读取失败（{type(error).__name__}）。请检查 Outlook 是否可正常收信、有无权限弹窗或文件夹是否已移动。') from None


def outlook_sender(item):
    address=str(item.SenderEmailAddress or '')
    if getattr(item,'SenderEmailType','')=='EX':
        try:
            user=item.Sender.GetExchangeUser()
            if user and user.PrimarySmtpAddress: address=str(user.PrimarySmtpAddress)
        except Exception: pass
    return address


def _scan_selected_messages(selected, store_id, start, limit):
    class Subset:
        Count=len(selected)
        def Sort(self,*args):pass
        def Item(self,index):return selected[index-1]
    return scan_folder(type('Page',(),{'Items':Subset()})(),store_id,start,limit)


def _item_resolver(folder,store_id):
    try:
        session=folder.Session
        return lambda identity:session.GetItemFromID(identity,store_id)
    except AttributeError:return None


def scan_folder_page(folder, store_id, start, end, limit, cursor=None):
    """Page fixed time bounds; freeze equal-time IDs so ties do not rescan prefixes."""
    start,end=start.astimezone(timezone.utc),end.astimezone(timezone.utc)
    resolver=_item_resolver(folder,store_id)
    if cursor and cursor.get('pending_ids'):
        if resolver is None:raise ValueError('无法按邮件标识续读，请确认 Outlook 存档仍已打开。')
        selected=[];errors=[]
        for identity in cursor['pending_ids'][:limit]:
            try:selected.append(resolver(identity))
            except Exception as error:errors.append(f'同时间邮件续读失败（{type(error).__name__}），该项已跳过，请检查邮件是否移动或删除并重新补扫。')
        remaining=cursor['pending_ids'][limit:]
        next_cursor={**cursor,'pending_ids':remaining} if remaining else ({'before':cursor['before'],'skip_boundary':True,'seen':[]} if cursor.get('has_older') else None)
        result=_scan_selected_messages(selected,store_id,start,limit)
        result.update(cursor=next_cursor,partial=next_cursor is not None,errors=errors+result['errors'],read=len(result['rows']))
        return result
    boundary=datetime.fromisoformat(cursor['before']).astimezone(timezone.utc) if cursor else end
    seen=set(cursor.get('seen',[])) if cursor else set()
    prop='"http://schemas.microsoft.com/mapi/proptag/0x0E060040"'
    # Widen the server comparison by one second and enforce precise bounds locally.
    query=f"@SQL={prop} >= '{start.strftime('%Y-%m-%d %H:%M:%S')}' AND {prop} <= '{(boundary+timedelta(seconds=1)).strftime('%Y-%m-%d %H:%M:%S')}'"
    items=folder.Items.Restrict(query)
    items.Sort('[ReceivedTime]',True)
    selected=[]; consumed=[]; errors=[]; more=False
    # DASL timestamp comparisons are widened to seconds. In dense imported
    # archives thousands of newer rows can fall in that overlap. Seek past
    # them before spending the per-page budget, otherwise no cursor advances.
    first=1; low=1; high=items.Count+1
    try:
        while low<high:
            middle=(low+high)//2
            received=items.Item(middle).ReceivedTime
            if received.tzinfo is None:received=received.astimezone()
            received=received.astimezone(timezone.utc)
            if received>boundary or received>=end or (cursor and cursor.get('skip_boundary') and received==boundary):low=middle+1
            else:high=middle
        first=low
    except Exception:
        first=1  # Retain per-item error reporting for malformed metadata.
    stop=min(items.Count,first+limit*3+len(seen))
    for index in range(first,stop+1):
        try:
            item=items.Item(index)
            received=item.ReceivedTime
            if received.tzinfo is None: received=received.astimezone()
            received=received.astimezone(timezone.utc)
            identity=str(item.EntryID)
            if received>=end or received>boundary or (received==boundary and (identity in seen or (cursor and cursor.get('skip_boundary')))): continue
            if received<start: break
            if len(selected)>=limit:
                more=True;break
            consumed.append((received,identity))
            if item.Class==43: selected.append(item)
        except Exception as error:
            errors.append(f'第 {index} 项元数据读取失败（{type(error).__name__}），请检查 Outlook；可重新补扫。')
    else: more=items.Count>stop
    result=_scan_selected_messages(selected,store_id,start,limit)
    next_cursor=None
    if more:
        if not consumed: raise ValueError('本批读取进度未推进，请检查 Outlook 或重新补扫。')
        before=consumed[-1][0]
        ids=[identity for when,identity in consumed if when==before]
        if before==boundary: ids=list(seen.union(ids))
        next_cursor={'before':before.isoformat(),'seen':ids}
        if resolver is not None:
            # Snapshot the unread IDs in this timestamp bucket once. Subsequent
            # batches resolve IDs directly, surviving tie reordering and restarts.
            try:
                lower=first;upper=items.Count+1
                while lower<upper:
                    middle=(lower+upper)//2
                    stamp=items.Item(middle).ReceivedTime
                    if stamp.tzinfo is None:stamp=stamp.astimezone()
                    if stamp.astimezone(timezone.utc)>=before:lower=middle+1
                    else:upper=middle
                bucket_end=lower-1
                pending=[];known=set(ids)
                # Only freeze when this batch stopped inside an equal-time bucket.
                for snapshot_index in range(index,bucket_end+1):
                    try:
                        entry=str(items.Item(snapshot_index).EntryID)
                        if entry not in known:pending.append(entry);known.add(entry)
                    except Exception as error:
                        result['errors'].append(f'同时间邮件标识读取失败（{type(error).__name__}），请重新补扫该目录。')
                if pending:
                    next_cursor={'before':before.isoformat(),'pending_ids':pending,'has_older':bucket_end<items.Count,'skip_boundary':True}
            except Exception as error:
                result['errors'].append(f'同时间邮件清单创建失败（{type(error).__name__}），已保留时间游标，后续批次继续读取。')
    result.update(cursor=next_cursor,partial=more,errors=errors+result['errors'],read=len(result['rows']))
    return result


def read_outlook_page(store_id, folder_id, start, end, limit, cursor=None):
    with outlook_session() as session:
        try:
            folder=session.GetFolderFromID(folder_id,store_id)
            result=scan_folder_page(folder,store_id,start,end,limit,cursor)
            for row in result['rows']: row['folder']=str(folder.FolderPath)
            return result
        except ValueError: raise
        except Exception as error:
            raise ValueError(f'邮箱分页读取失败（{type(error).__name__}），进度已保留，下一次将重试本批。') from None


def data_path():
    from .accounts import account_data_dir
    return account_data_dir() / 'mail' / 'mail.sqlite3'
