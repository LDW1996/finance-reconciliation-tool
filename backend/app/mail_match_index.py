"""Rebuildable encrypted match generations, shared by every read path.

Rows are compressed and encrypted separately so full candidate evidence is never
retained for the whole ledger in memory. No mail, review or scan data is changed.
"""
from datetime import datetime, timezone
from contextlib import contextmanager
import hashlib
import json
import logging
from threading import Event, RLock, Thread
import uuid
import zlib
from time import monotonic

log=logging.getLogger(__name__)


class MatchResourceError(ValueError):
    pass


class MatchIndex:
    BATCH_SIZE=200
    VERSION=3

    def __init__(self,workspace):
        self.workspace=workspace;self.store=workspace.store
        self.lock=RLock();self.done=Event();self.done.set()
        self.load_status=lambda:dict(active=0,pending=0,workers=0)
        self.quiet_seconds=5;self.last_busy=0
        self.running=None;self.failure=None;self.completed=0;self.total=0;self.readers={}
        with self.store.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS mail_match_generation (day_window INTEGER PRIMARY KEY, signature TEXT NOT NULL, generation TEXT NOT NULL, updated_at TEXT NOT NULL, ledger_id TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS mail_match_row (generation TEXT NOT NULL, ordinal INTEGER NOT NULL, payload BLOB NOT NULL, preview BLOB, receipt_key TEXT, PRIMARY KEY(generation,ordinal))')
            columns={row[1] for row in db.execute('PRAGMA table_info(mail_match_row)')}
            if 'preview' not in columns:db.execute('ALTER TABLE mail_match_row ADD COLUMN preview BLOB')
            if 'receipt_key' not in columns:db.execute('ALTER TABLE mail_match_row ADD COLUMN receipt_key TEXT')
            db.execute('CREATE INDEX IF NOT EXISTS mail_match_receipt_key ON mail_match_row(generation,receipt_key)')
            db.execute('CREATE TABLE IF NOT EXISTS mail_match_related (generation TEXT NOT NULL, mail_key TEXT NOT NULL, ordinal INTEGER NOT NULL, PRIMARY KEY(generation,mail_key,ordinal))')
            db.execute('CREATE TABLE IF NOT EXISTS mail_match_source_revision (source TEXT PRIMARY KEY, revision INTEGER NOT NULL)')
            for operation in ('INSERT','UPDATE','DELETE'):
                db.execute(f"CREATE TRIGGER IF NOT EXISTS mail_match_ledger_{operation.lower()} AFTER {operation} ON mail_ledger BEGIN INSERT INTO mail_match_source_revision VALUES('ledger',1) ON CONFLICT(source) DO UPDATE SET revision=revision+1; END")
                record='OLD' if operation=='DELETE' else 'NEW'
                db.execute(f"CREATE TRIGGER IF NOT EXISTS mail_match_mapping_{operation.lower()} AFTER {operation} ON mail_state WHEN {record}.key='customer_mapping' BEGIN INSERT INTO mail_match_source_revision VALUES('mapping',1) ON CONFLICT(source) DO UPDATE SET revision=revision+1; END")

    def inputs(self,window,payments=False):
        # The same read transaction binds source revisions to their inputs. Polls
        # read only small scalars; full encrypted data is read once by the worker.
        with self.store.db(write=False) as db:
            db.execute('BEGIN')
            ledger=db.execute('SELECT id'+(',metadata' if payments else '')+' FROM mail_ledger LIMIT 1').fetchone()
            data=(self.store.decode(ledger[1]) if payments else {'id':ledger[0]}) if ledger else None
            revision_row=db.execute("SELECT payload FROM mail_state WHERE key='match_revision'").fetchone()
            revision=self.store.decode(revision_row[0]) if revision_row else '0'
            versions=dict(db.execute('SELECT source,revision FROM mail_match_source_revision'))
            mapping_row=db.execute("SELECT payload FROM mail_state WHERE key='customer_mapping'").fetchone() if payments else None
            mapping=self.store.decode(mapping_row[0]) if mapping_row else {}
            indexed=[self.store.decode(payload) for payload, in db.execute('SELECT payload FROM mail_payments')] if payments else []
        facts=[self.VERSION,data['id'] if data else '',versions.get('ledger',0),revision,versions.get('mapping',0),window]
        return hashlib.sha256(json.dumps(facts).encode()).hexdigest(),data,mapping,indexed

    def identity(self,window):
        key,data,_,_=self.inputs(window)
        return key,data

    def metadata(self,window):
        with self.store.db(write=False) as db:
            row=db.execute('SELECT signature,generation,updated_at,ledger_id FROM mail_match_generation WHERE day_window=?',(window,)).fetchone()
        return dict(zip(('signature','generation','updated_at','ledger_id'),row)) if row else None

    def background_load(self):
        load=self.load_status()
        if load.get('active',0) or load.get('busy',load.get('pending',0)):self.last_busy=monotonic()
        waiting=bool(load.get('active',0) or load.get('busy',load.get('pending',0)) or (self.last_busy and monotonic()-self.last_busy<self.quiet_seconds))
        return load,waiting

    def state(self,window=3):
        key,data=self.identity(window);meta=self.metadata(window)
        load,waiting=self.background_load()
        with self.lock:
            current=self.running
            error=self.failure if self.failure and self.failure['signature']==key else None
            stale=bool(data and (not meta or meta['signature']!=key))
            return dict(state='failed' if error else 'waiting' if waiting and (stale or current) else 'running' if current else 'stale' if stale else 'ready',
                        stale=stale,available=bool(meta and data and meta['ledger_id']==data['id']),
                        revision=meta['signature'] if meta else '',target_revision=key,
                        updated_at=meta['updated_at'] if meta else None,
                        recognition_active=load.get('active',0),recognition_pending=load.get('pending',0),recognition_workers=load.get('workers',0),
                        completed=self.completed,total=self.total,error=error['message'] if error else '')

    def request(self,window=3,wait=False,retry=False):
        # Identity checks are cheap; capture the payment index only in the worker.
        while True:
            key,data=self.identity(window);meta=self.metadata(window)
            if not data or (meta and meta['signature']==key):return self.state(window)
            if self.background_load()[1]:
                if wait:raise ValueError('正在处理邮件识别，匹配将在识别空闲后统一更新，请稍后再试。')
                return self.state(window)
            with self.lock:
                if self.failure and self.failure['signature']==key and not retry:
                    if wait:raise ValueError(self.failure['message'])
                    return self.state(window)
                if not self.running:
                    self.running=(key,window);self.failure=None;self.completed=0;self.total=0;self.done.clear()
                    Thread(target=self._build,args=(key,window,data),name='mail-matching',daemon=True).start()
            if not wait:return self.state(window)
            self.done.wait()
            retry=False

    def wait(self,timeout=None):return self.done.wait(timeout)

    def _build(self,key,window,data):
        generation=uuid.uuid4().hex
        try:
            # Merge rapid recognition updates before capturing one consistent version.
            Event().wait(0.2)
            key,data,contacts,indexed=self.inputs(window,payments=True)
            if not data:return
            with self.lock:
                active=list(self.readers)
                extra=' AND generation NOT IN ('+','.join('?' for _ in active)+')' if active else ''
                with self.store.db() as db:
                    db.execute('DELETE FROM mail_match_row WHERE generation NOT IN (SELECT generation FROM mail_match_generation)'+extra,active)
                    db.execute('DELETE FROM mail_match_related WHERE generation NOT IN (SELECT generation FROM mail_match_generation)'+extra,active)
            from .mail_matching import rank_receipts, _group_payments, effective_payment_date, _date
            payments=[p for batch in indexed for p in batch]
            groups=_group_payments([effective_payment_date(p) for p in payments])
            by_date={}
            for group in groups:
                day=_date(group[0].get('date'))
                if day and not group[0].get('ambiguous'):by_date.setdefault(day,[]).append(group)
            from .customer_mapping import MappingIndex
            mapping=MappingIndex(contacts.get('entries',[])) if contacts.get('enabled') else None
            with self.lock:self.running=(key,window);self.total=len(data['entries'])
            derived_bytes=0
            entries=data['entries']
            for start in range(0,len(entries),self.BATCH_SIZE):
                while self.background_load()[1]:Event().wait(.5)
                rows=rank_receipts(entries[start:start+self.BATCH_SIZE],[],day_window=window,limit=None,mapping=mapping,_groups=groups,_by_date=by_date)
                from .mail_workspace import signature
                encoded=[];links=[]
                for i,row in enumerate(rows):
                    candidates=[dict(id=c['id'],payment_id=c['payment_id'],score=c['score'],warnings=c.get('warnings',[]),_snapshot_signature=signature(c)) for c in row['candidates'][:20]]
                    preview=dict(receipt=row['receipt'],candidates=candidates,candidate_count=row['candidate_count'],warnings=row['warnings'])
                    pack=lambda value:self.store.cipher.encrypt(zlib.compress(json.dumps(value,ensure_ascii=False).encode()))
                    encoded.append((generation,start+i,pack(row),pack(preview),hashlib.sha256(row['receipt']['id'].encode()).hexdigest()))
                    mails={mail for c in row['candidates'] for mail in c.get('related_mail_ids',[c.get('mail_id')]) if mail}
                    links.extend((generation,hashlib.sha256(mail.encode()).hexdigest(),start+i) for mail in mails)
                with self.store.db() as db:
                    db.executemany('INSERT INTO mail_match_row VALUES(?,?,?,?,?)',encoded)
                    db.executemany('INSERT INTO mail_match_related VALUES(?,?,?)',links)
                with self.lock:self.completed=min(start+len(rows),len(entries))
                derived_bytes+=sum(len(item[2])+len(item[3]) for item in encoded)
                if derived_bytes>512*1024**2:raise MatchResourceError('匹配候选过多，临时结果超过 512MB，请缩小付款日期容差后重试。')
                if start % self.BATCH_SIZE == 0:
                    from .mail_storage import MailStorage
                    reason=MailStorage(self.store).block_reason(reserve_bytes=4*1024**2)
                    if reason:raise MatchResourceError('匹配结果保存空间不足，请检查保存容量和磁盘剩余空间后重试。')
            current=self.workspace._dataset()
            if not current or current['id']!=data['id']:return
            now=datetime.now(timezone.utc).isoformat()
            with self.lock:
                active=list(self.readers)
            with self.store.db() as db:
                db.execute('INSERT INTO mail_match_generation VALUES(?,?,?,?,?) ON CONFLICT(day_window) DO UPDATE SET signature=excluded.signature,generation=excluded.generation,updated_at=excluded.updated_at,ledger_id=excluded.ledger_id',(window,key,generation,now,data['id']))
                # Retain at most three date-window variants. All are derived data.
                db.execute('DELETE FROM mail_match_generation WHERE day_window NOT IN (SELECT day_window FROM mail_match_generation ORDER BY updated_at DESC LIMIT 3)')

        except Exception as error:
            log.exception('Receipt matching generation failed')
            from .diagnostics import record
            record('mail_matching_failed',error,phase='compute_or_save_matches')
            kind='可用内存不足' if isinstance(error,MemoryError) else '匹配结果保存或计算失败'
            with self.lock:self.failure=dict(signature=key,message=str(error) if isinstance(error,MatchResourceError) else f'{kind}（{type(error).__name__}）。上次结果保留，请检查本地资源后重试。')
        finally:
            # Never publish or retain a partially generated result.
            try:
                with self.store.db() as db:
                    db.execute('DELETE FROM mail_match_row WHERE generation=? AND generation NOT IN (SELECT generation FROM mail_match_generation)',(generation,))
                    db.execute('DELETE FROM mail_match_related WHERE generation=? AND generation NOT IN (SELECT generation FROM mail_match_generation)',(generation,))
            except Exception:log.exception('Could not clean partial matching generation')
            with self.lock:self.running=None;self.done.set()

    @contextmanager
    def view(self,window=3):
        with self.lock:
            meta=self.metadata(window)
            if meta:self.readers[meta['generation']]=self.readers.get(meta['generation'],0)+1
        try:yield meta
        finally:
            if meta:
                with self.lock:
                    self.readers[meta['generation']]-=1
                    if not self.readers[meta['generation']]:del self.readers[meta['generation']]

    def full_row(self,receipt_id,generation):
        with self.store.db(write=False) as db:
            row=db.execute('SELECT payload FROM mail_match_row WHERE generation=? AND receipt_key=?',(generation,hashlib.sha256(receipt_id.encode()).hexdigest())).fetchone()
        return json.loads(zlib.decompress(self.store.cipher.decrypt(row[0]))) if row else None

    def rows(self,window=3,wait=True,metadata=None,full=False,mail_id=None):
        self.request(window,wait=wait)
        with self.view(window) as current:
            meta=metadata or current;data=self.workspace._dataset()
            if not meta or not data or meta['ledger_id']!=data['id']:return
            generation=meta['generation'];offset=0
            while True:
                with self.store.db(write=False) as db:
                    column='r.payload' if full else 'COALESCE(r.preview,r.payload)'
                    join=' JOIN mail_match_related m ON r.generation=m.generation AND r.ordinal=m.ordinal' if mail_id else ''
                    clause=' AND m.mail_key=?' if mail_id else ''
                    params=[generation,offset]+([hashlib.sha256(mail_id.encode()).hexdigest()] if mail_id else [])
                    block=db.execute(f'SELECT r.ordinal,{column} FROM mail_match_row r{join} WHERE r.generation=? AND r.ordinal>=?{clause} ORDER BY r.ordinal LIMIT 16',params).fetchall()
                if not block:break
                for ordinal,payload in block:
                    row=json.loads(zlib.decompress(self.store.cipher.decrypt(payload)))
                    row['_generation']=generation
                    yield row
                offset=block[-1][0]+1
