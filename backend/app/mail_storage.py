"""Bound mail ingestion without removing user data; durable recognition backlog."""
import shutil

GIB=1024**3
DEFAULTS=dict(max_gb=10,min_free_gb=5,queue_limit=500)


class MailStorage:
    def __init__(self,store):
        self.store=store
        self._cached=None;self._checked=0

    def settings(self):
        return {**DEFAULTS,**self.store.get_state('mail_storage_settings',{})}

    def configure(self,values):
        settings={**self.settings(),**values}
        for key,low,high in [('max_gb',1,1000),('min_free_gb',1,100),('queue_limit',100,5000)]:
            value=settings[key]
            if isinstance(value,bool) or not isinstance(value,int) or not low<=value<=high:
                raise ValueError('保存容量、剩余空间或队列上限超出允许范围。')
        self.store.set_state('mail_storage_settings',settings)
        return settings

    def disk_free(self):
        return shutil.disk_usage(self.store.path.parent).free

    def used_bytes(self):
        return sum(p.stat().st_size for p in self.store.path.parent.glob(self.store.path.name+'*') if p.is_file())

    def block_reason(self,reserve_bytes=0):
        settings=self.settings()
        if self.disk_free()-reserve_bytes<settings['min_free_gb']*GIB:
            return '磁盘剩余空间不足，已暂停新增读取；请释放空间后重试。'
        if self.used_bytes()+reserve_bytes>=settings['max_gb']*GIB:
            return '邮件存储达到容量上限，已暂停新增读取；请调整容量或释放空间后重试。'
        return ''

    def snapshot(self):
        # SQL byte sums are explicit management operations, never every status poll.
        with self.store.db() as db:
            count=db.execute('SELECT COUNT(*) FROM mail').fetchone()[0]
            attachment_count,raw,metadata=db.execute('SELECT COUNT(*),COALESCE(SUM(LENGTH(content)),0),COALESCE(SUM(LENGTH(metadata)),0) FROM mail_attachment').fetchone()
            reusable=db.execute('PRAGMA freelist_count').fetchone()[0]*db.execute('PRAGMA page_size').fetchone()[0]
        settings=self.settings();used=self.used_bytes()
        return dict(settings=settings,mail_count=count,attachment_count=attachment_count,used_bytes=used,
                    attachment_bytes=raw,recognition_bytes=metadata,reusable_bytes=reusable,
                    free_bytes=self.disk_free(),data_directory=str(self.store.path.parent),
                    warning=used>=settings['max_gb']*GIB*.8,blocked_reason=self.block_reason())


class AnalysisQueue:
    """Claim then acknowledge: an interrupted item stays durable until completed."""
    def __init__(self,store):
        self.store=store
        with store.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS mail_analysis_queue (mail_id TEXT PRIMARY KEY,retry INTEGER NOT NULL DEFAULT 0,claimed INTEGER NOT NULL DEFAULT 0)')
            db.execute('UPDATE mail_analysis_queue SET claimed=0')

    def __len__(self):
        with self.store.db() as db:return db.execute('SELECT COUNT(*) FROM mail_analysis_queue WHERE claimed=0').fetchone()[0]

    def append(self,item):
        with self.store.db() as db:
            db.execute('INSERT INTO mail_analysis_queue(mail_id,retry) VALUES(?,?) ON CONFLICT(mail_id) DO UPDATE SET retry=MAX(retry,excluded.retry)',(item[0],int(item[1])))

    def extend(self,items):
        with self.store.db() as db:
            db.executemany('INSERT OR IGNORE INTO mail_analysis_queue(mail_id,retry) VALUES(?,?)',items)

    def popleft(self):
        with self.store.db() as db:
            row=db.execute('SELECT mail_id,retry FROM mail_analysis_queue WHERE claimed=0 ORDER BY rowid LIMIT 1').fetchone()
            if row is None:raise IndexError('empty queue')
            db.execute('UPDATE mail_analysis_queue SET claimed=1 WHERE mail_id=?',(row[0],))
            return row[0],bool(row[1])

    def done(self,identity):
        with self.store.db() as db:db.execute('DELETE FROM mail_analysis_queue WHERE mail_id=?',(identity,))

    def release(self, identity):
        with self.store.db() as db:
            db.execute('UPDATE mail_analysis_queue SET claimed=0 WHERE mail_id=?',(identity,))
