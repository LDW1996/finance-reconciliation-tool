"""Rebuildable list metadata; payment text remains encrypted at rest.

Only changed/legacy rows are decrypted for indexing. Keyword searches scan small
search documents rather than full bodies; substring matching remains exact.
"""
from threading import RLock


class MailListIndex:
    def __init__(self, store):
        self.store = store
        self.lock = RLock()
        with store.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS mail_list_revision (mail_id TEXT PRIMARY KEY, revision INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS mail_list_index (mail_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, status TEXT NOT NULL, search TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS mail_list_status ON mail_list_index(status,mail_id)')
            db.execute('CREATE INDEX IF NOT EXISTS mail_list_order ON mail(seen DESC,id)')
            for table, key in [('mail', 'id'), ('mail_payment_summary', 'mail_id'), ('mail_payments', 'mail_id'), ('mail_attachment', 'mail_id')]:
                for operation in ('INSERT', 'UPDATE', 'DELETE'):
                    record = 'OLD' if operation == 'DELETE' else 'NEW'
                    db.execute(f'''CREATE TRIGGER IF NOT EXISTS list_{table}_{operation.lower()}
                        AFTER {operation} ON {table} BEGIN
                        INSERT INTO mail_list_revision VALUES({record}.{key},1)
                        ON CONFLICT(mail_id) DO UPDATE SET revision=revision+1;
                        DELETE FROM mail_list_index WHERE mail_id={record}.{key};
                        DELETE FROM mail_list_revision WHERE mail_id={record}.{key}
                        AND NOT EXISTS(SELECT 1 FROM mail WHERE id={record}.{key}); END''')

    def refresh(self):
        from .mail_insights import mail_summary
        with self.lock:
            while True:
                with self.store.db(write=False) as db:
                    db.execute('BEGIN')
                    batch = db.execute('''SELECT m.id,m.payload,s.payload,p.payload,COALESCE(v.revision,0)
                        FROM mail m LEFT JOIN mail_payment_summary s ON s.mail_id=m.id
                        LEFT JOIN mail_payments p ON p.mail_id=m.id
                        LEFT JOIN mail_list_revision v ON v.mail_id=m.id
                        LEFT JOIN mail_list_index i ON i.mail_id=m.id
                        WHERE i.mail_id IS NULL OR i.revision!=COALESCE(v.revision,0) LIMIT 200''').fetchall()
                    if not batch:
                        return
                    legacy = [r[0] for r in batch if not r[2]]
                    attachments = {}
                    if legacy:
                        values = db.execute('SELECT mail_id,metadata FROM mail_attachment WHERE mail_id IN (' + ','.join('?' for _ in legacy) + ') ORDER BY position', legacy)
                        for identity, metadata in values:
                            attachments.setdefault(identity, []).append(metadata)
                prepared = []
                for identity, payload, summary_payload, payments, revision in batch:
                    row = self.store.decode(payload)
                    if summary_payload:
                        summary = self.store.decode(summary_payload)
                    else:
                        row['attachment_details'] = [self.store.decode(v) for v in attachments.get(identity, [])]
                        summary = mail_summary(row, self.store.decode(payments) if payments else [], indexed=payments is not None)
                    fields = [row.get('subject',''), row.get('sender',''), *row.get('candidates',[]),
                              *summary.get('payers',[]), *summary.get('dates',[]), *summary.get('business_ids',[])]
                    fields.extend(str(a.get('currency',''))+' '+str(a.get('amount','')) for a in summary.get('amounts',[]))
                    prepared.append((identity, revision, summary.get('status','pending'), self.store.encode(' '.join(fields).casefold())))
                with self.store.db() as db:
                    for identity, revision, status, search in prepared:
                        # A concurrent save invalidates its index. Never publish stale metadata.
                        db.execute('''INSERT OR REPLACE INTO mail_list_index
                            SELECT ?,?,?,? WHERE EXISTS(SELECT 1 FROM mail WHERE id=?)
                            AND COALESCE((SELECT revision FROM mail_list_revision WHERE mail_id=?),0)=?''',
                            (identity, revision, status, search, identity, identity, revision))

    def page(self, page, page_size, keyword, payment_filter):
        self.refresh()
        clause = '' if payment_filter == 'all' else ' AND i.status=?'
        params = [] if payment_filter == 'all' else ['ready' if payment_filter == 'payment' else payment_filter]
        offset = (page-1)*page_size
        with self.store.db(write=False) as db:
            db.execute('BEGIN')
            total_saved = db.execute('SELECT COUNT(*) FROM mail').fetchone()[0]
            base = ' FROM mail m JOIN mail_list_index i ON i.mail_id=m.id WHERE 1=1' + clause
            if keyword:
                # Full mail payloads/attachments are never read during this scan.
                ids = []
                for identity, search in db.execute('SELECT m.id,i.search'+base+' ORDER BY m.seen DESC,m.id', params):
                    if keyword in self.store.decode(search):
                        ids.append(identity)
                total = len(ids)
                selected = ids[offset:offset+page_size]
                values = {identity: payload for identity, payload in db.execute(
                    'SELECT id,payload FROM mail WHERE id IN ('+','.join('?' for _ in selected)+')', selected)} if selected else {}
                rows = [self.store.decode(values[identity]) for identity in selected]
            else:
                total = db.execute('SELECT COUNT(*)'+base, params).fetchone()[0]
                values = db.execute('SELECT m.payload'+base+' ORDER BY m.seen DESC,m.id LIMIT ? OFFSET ?', [*params,page_size,offset])
                rows = [self.store.decode(payload) for payload, in values]
        for row in rows:
            for field in ('body','latest_body','signature','quoted_body'):
                row.pop(field,None)
        return dict(rows=rows,total=total,total_saved=total_saved,page=page,page_size=page_size)
