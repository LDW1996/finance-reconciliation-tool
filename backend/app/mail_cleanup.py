"""Conservative, explicitly selected local cleanup with encrypted recovery copies."""
from hashlib import sha256
import json
import re
import zlib
from uuid import uuid4
from datetime import datetime, timezone

from .mail_matching import _business_ids

# Deliberately broad: false positives retain mail. A negative result is only a
# review candidate, never permission for unattended deletion.
CLUES = re.compile(r'\d|[$€£¥￥]|付款|收款|支付|汇款|转账|银行|账户|账号|金额|发票|凭证|货款|运费|提单|订单|结算|对账|合同|payment|paid|payable|receivable|remit|transfer|bank|account|amount|invoice|receipt|currency|balance|swift|iban|booking|freight|bill|refund|credit|debit|settle|idr|usd|rmb|cny|eur', re.I)
TABLES = ('mail', 'mail_identity', 'mail_payments', 'mail_payment_summary')
KEYS = dict(mail='id', mail_identity='mail_id', mail_payments='mail_id', mail_payment_summary='mail_id')


class MailCleanup:
    def __init__(self, store):
        self.store = store
        self.directory = store.path.parent / 'mail-cleanup-backups'

    def review_protection(self, db):
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mail_review'").fetchone():
            return False, set()
        reviews=[self.store.decode(row[0]) for row in db.execute('SELECT payload FROM mail_review')]
        references={r['payment_id'] for r in reviews if r.get('payment_id')}
        protected={r['mail_id'] for r in reviews if r.get('mail_id')}
        if references:
            resolved=set()
            for identity,payload in db.execute('SELECT mail_id,payload FROM mail_payments'):
                for payment in self.store.decode(payload):
                    key=payment.get('id') or payment.get('payment_id')
                    if key in references:
                        protected.add(identity);resolved.add(key)
            # A stale reference cannot safely be attributed to a particular mail.
            if references-resolved:return True, protected
        return False, protected

    def eligible(self, db, identity, protection=None):
        row = db.execute('SELECT payload FROM mail WHERE id=?', (identity,)).fetchone()
        if not row:
            return False
        mail = self.store.decode(row[0])
        if mail.get('read_complete') is not True or mail.get('outlook_read_version') != 1:
            return False
        if any(mail.get(k) for k in ('truncated', 'attachment_warning', 'warnings', 'errors', 'candidates', 'attachments', 'attachment_details')):
            return False
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mail_analysis_queue'").fetchone() and db.execute('SELECT 1 FROM mail_analysis_queue WHERE mail_id=?', (identity,)).fetchone():
            return False
        if db.execute('SELECT 1 FROM mail_attachment WHERE mail_id=?', (identity,)).fetchone():
            return False
        payments = db.execute('SELECT payload FROM mail_payments WHERE mail_id=?', (identity,)).fetchone()
        summary = db.execute('SELECT payload FROM mail_payment_summary WHERE mail_id=?', (identity,)).fetchone()
        if not payments or self.store.decode(payments[0]) != [] or not summary:
            return False
        info = self.store.decode(summary[0])
        if info.get('status') != 'empty' or any(info.get(k) for k in ('count', 'business_ids', 'payers', 'amounts', 'dates')):
            return False
        text = '\n'.join(str(mail.get(k) or '') for k in ('subject', 'body', 'latest_body', 'quoted_body', 'signature'))
        if not text.strip() or len(text)>10000 or CLUES.search(text) or _business_ids(text):
            return False
        blocked, protected = protection if protection is not None else self.review_protection(db)
        if blocked or identity in protected:
            return False
        return True

    def snapshot(self, db, identity):
        return {table: [list(row) for row in db.execute(f'SELECT * FROM {table} WHERE {KEYS[table]}=?', (identity,))] for table in TABLES}

    @staticmethod
    def token(records):
        return sha256(json.dumps(records, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def preview(self, progress=lambda **x: None):
        candidates = []; protected = 0; errors = 0
        with self.store.db() as db:
            total = db.execute('SELECT COUNT(*) FROM mail').fetchone()[0]
            try:protection=self.review_protection(db)
            except Exception:
                return dict(candidates=[],protected=total,errors=1,total=total,note='关联记录无法核验，全部保留，请检查数据后重试。')
            # Bounded output and keyset iteration; no original attachment is read.
            last = ''
            processed = 0
            while True:
                rows = db.execute('SELECT id,payload FROM mail WHERE id>? ORDER BY id LIMIT 200', (last,)).fetchall()
                if not rows:
                    break
                for identity, payload in rows:
                    try:
                        if len(candidates) < 200 and self.eligible(db, identity, protection):
                            mail = self.store.decode(payload)
                            candidates.append(dict(id=identity, subject=mail.get('subject') or '（无主题）', sender=mail.get('sender') or '', body='\n'.join(str(mail.get(k) or '') for k in ('body','latest_body','quoted_body','signature')), token=self.token(self.snapshot(db, identity))))
                        else:
                            protected += 1
                    except Exception:
                        errors += 1
                    processed += 1
                last = rows[-1][0]
                progress(processed=processed, total=total, phase='检查无附件且无付款线索的邮件')
        return dict(candidates=candidates, protected=protected, errors=errors, total=total, note='仅为待人工核对候选，不代表确定无用；最多展示200封。所有附件均保留。')

    def delete(self, candidates, selected):
        wanted = set(selected)
        offered = {item['id']: item['token'] for item in candidates}
        if not wanted or len(wanted) > 200 or not wanted <= offered.keys():
            raise ValueError('请从本次预览中逐封选择要清理的邮件。')
        backup_id = uuid4().hex
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            records = []
            protection=self.review_protection(db)
            for identity in sorted(wanted):
                record = self.snapshot(db, identity)
                if not self.eligible(db, identity, protection) or self.token(record) != offered[identity]:
                    raise ValueError('邮件内容或关联已变化，本批未删除，请重新预览。')
                records.append(record)
            # Persist and fsync recovery bytes before committing any deletion.
            import os
            content = self.store.cipher.encrypt(zlib.compress(json.dumps(records, ensure_ascii=False).encode()))
            with (self.directory / (backup_id + '.backup')).open('xb') as output:
                os.chmod(output.name, 0o600)
                output.write(content); output.flush(); os.fsync(output.fileno())
            if os.name=='posix':
                directory_fd=os.open(self.directory,os.O_RDONLY)
                try:os.fsync(directory_fd)
                finally:os.close(directory_fd)
            for identity in wanted:
                for table in reversed(TABLES):
                    db.execute(f'DELETE FROM {table} WHERE {KEYS[table]}=?', (identity,))
            db.execute('INSERT OR REPLACE INTO mail_state VALUES(?,?)', ('match_revision', self.store.encode(datetime.now(timezone.utc).isoformat())))
        return dict(deleted=len(wanted), backup_id=backup_id, backup_bytes=len(content), note='仅清理本地所选邮件；Outlook原件、附件和读取进度保留。备份仍占空间，数据库文件需另行整理；重新扫描可能再次读入。')

    def backups(self):
        return [dict(backup_id=path.stem, bytes=path.stat().st_size, at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat())
                for path in sorted(self.directory.glob('*.backup'), key=lambda p:p.stat().st_mtime, reverse=True)
                if re.fullmatch('[a-f0-9]{32}', path.stem)]

    def restore(self, backup_id):
        if not re.fullmatch('[a-f0-9]{32}', backup_id):
            raise ValueError('备份编号无效。')
        path = self.directory / (backup_id + '.backup')
        if not path.is_file():
            raise ValueError('未找到清理备份，请确认数据目录和密钥。')
        try:
            records = json.loads(zlib.decompress(self.store.cipher.decrypt(path.read_bytes())))
        except Exception as error:
            raise ValueError('恢复备份无法解密或内容不完整，本批未恢复；请保留备份和原密钥。') from error
        restored = 0; conflicts = 0
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            for record in records:
                identity = record['mail'][0][0]
                if db.execute('SELECT 1 FROM mail WHERE id=?', (identity,)).fetchone() or any(db.execute('SELECT 1 FROM mail_identity WHERE fingerprint=?', (r[0],)).fetchone() for r in record['mail_identity']):
                    conflicts += 1
                    continue
                for table in TABLES:
                    for row in record[table]:
                        db.execute(f'INSERT INTO {table} VALUES({",".join("?" for _ in row)})', row)
                restored += 1
            db.execute('INSERT OR REPLACE INTO mail_state VALUES(?,?)', ('match_revision', self.store.encode(datetime.now(timezone.utc).isoformat())))
        return dict(restored=restored, conflicts=conflicts, backup_id=backup_id, note='恢复不会覆盖后来读取的数据；备份继续保留。')
