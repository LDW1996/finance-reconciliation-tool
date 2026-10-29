"""Idempotent, bounded migration. No mail, payment, review or original is deleted."""
from hashlib import sha256
import shutil
import sqlite3

from .mail_assets import PreviewCache,without_previews,save_blob


class MailMaintenance:
    def __init__(self,store):self.store=store

    def chunks(self):
        last=''
        while True:
            with self.store.db() as db:
                rows=db.execute('''SELECT a.id,a.mail_id,a.metadata,LENGTH(a.content),COALESCE(LENGTH(b.content),0)
                    FROM mail_attachment a LEFT JOIN mail_attachment_blob_ref r ON r.attachment_id=a.id
                    LEFT JOIN mail_blob b ON b.digest=r.digest WHERE a.id>? ORDER BY a.id LIMIT 1''',(last,)).fetchall()
            if not rows:return
            yield rows
            last=rows[-1][0]

    def count(self):
        with self.store.db() as db:return db.execute('SELECT COUNT(*) FROM mail_attachment').fetchone()[0]

    def preview(self,progress=lambda **x:None):
        report=dict(attachments=self.count(),preview_pages=0,metadata_bytes=0,duplicate_bytes=0,protected_preview_pages=0,errors=0)
        with self.store.db() as db:seen={row[0] for row in db.execute('SELECT digest FROM mail_blob')}
        processed=0
        progress(processed=0,total=report['attachments'],phase='检查历史附件')
        for rows in self.chunks():
            for identity,mail_id,payload,legacy_bytes,shared_bytes in rows:
                try:
                    asset=self.store.decode(payload)
                    pages=sum(bool(p.get('preview')) for p in (asset.get('result') or {}).get('pages',[]))
                    if max(legacy_bytes,shared_bytes)>100:
                        report['preview_pages']+=pages
                        report['metadata_bytes']+=max(0,len(payload)-len(self.store.encode(without_previews(asset))))
                    else:report['protected_preview_pages']+=pages
                    digest=asset.get('sha256')
                    if digest and legacy_bytes>100:
                        if digest in seen:report['duplicate_bytes']+=legacy_bytes
                        seen.add(digest)
                except Exception:report['errors']+=1
                processed+=1
            if processed%25==0 or processed==report['attachments']:
                progress(processed=processed,total=report['attachments'],phase='检查历史附件')
        report['cache_bytes']=PreviewCache(self.store).used_bytes()
        report['estimated_reclaim_bytes']=report['metadata_bytes']+report['duplicate_bytes']+report['cache_bytes']
        return report

    def optimize(self,compact=True,progress=lambda **x:None):
        before=self.store.path.stat().st_size
        report=dict(attachments=self.count(),processed=0,preview_pages=0,protected_preview_pages=0,errors=0,compacted=False)
        progress(processed=0,total=report['attachments'],phase='去重并清理可重建预览')
        for rows in self.chunks():
            for identity,mail_id,payload,_,_ in rows:
                if shutil.disk_usage(self.store.path.parent).free<max(64*1024**2,2*len(payload)+16*1024**2):
                    raise ValueError('磁盘剩余空间不足以安全优化下一份附件，已完成部分保留；请释放空间后重新预览继续。')
                try:
                    # All per-attachment changes are one transaction; interruption
                    # never leaves an original without a durable shared reference.
                    with self.store.db() as db:
                        row=db.execute('SELECT metadata,content FROM mail_attachment WHERE id=?',(identity,)).fetchone()
                        if row is None:continue
                        asset=self.store.decode(row[0])
                        original=self.store.cipher.decrypt(row[1]) if row[1] else b''
                        if original:
                            if asset.get('sha256') and sha256(original).hexdigest()!=asset['sha256']:
                                raise ValueError('Original digest mismatch')
                            save_blob(self.store,db,identity,original)
                            db.execute('UPDATE mail_attachment SET content=? WHERE id=?',(b'',identity))
                        raw_available=bool(original) or bool(db.execute('''SELECT 1 FROM mail_attachment_blob_ref r JOIN mail_blob b ON b.digest=r.digest WHERE r.attachment_id=? AND LENGTH(b.content)>100''',(identity,)).fetchone())
                        pages=sum(bool(p.get('preview')) for p in (asset.get('result') or {}).get('pages',[]))
                        if raw_available:
                            clean=without_previews(asset)
                            if pages:db.execute('UPDATE mail_attachment SET metadata=? WHERE id=?',(self.store.encode(clean),identity))
                            report['preview_pages']+=pages
                        else:report['protected_preview_pages']+=pages
                except Exception:
                    report['errors']+=1
                report['processed']+=1
            if report['processed']%25==0 or report['processed']==report['attachments']:
                progress(processed=report['processed'],total=report['attachments'],phase='去重并清理可重建预览')
        with self.store.db() as db:
            db.execute('DELETE FROM mail_attachment_blob_ref WHERE attachment_id NOT IN (SELECT id FROM mail_attachment)')
            db.execute('DELETE FROM mail_blob WHERE digest NOT IN (SELECT digest FROM mail_attachment_blob_ref)')
        report['cache_released_bytes']=PreviewCache(self.store).clear()
        if compact:
            required=2*self.store.path.stat().st_size+max(1,self.store.get_state('mail_storage_settings',{}).get('min_free_gb',5))*1024**3
            if shutil.disk_usage(self.store.path.parent).free<required:
                report['compact_note']='去重和预览清理已完成，但数据库整理所需临时空间不足；请释放空间后再次优化。'
            else:
                progress(processed=report['processed'],total=report['attachments'],phase='整理数据库以释放磁盘空间')
                with sqlite3.connect(self.store.path,timeout=30,isolation_level=None) as db:
                    db.execute('VACUUM')
                    if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('数据库检查未通过，请保留数据并联系维护人员。')
                report['compacted']=True
        report['database_released_bytes']=max(0,before-self.store.path.stat().st_size)
        report['note']='邮件、原件、识别文字、付款索引和已确认关联均保留。'
        return report
