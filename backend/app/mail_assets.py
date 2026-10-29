"""Encrypted shared originals and bounded, disposable page previews."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import base64
import os
from pathlib import Path
import shutil
import tempfile
from threading import RLock
import time

_CACHE_LOCK=RLock()
MIB=1024**2


def without_previews(metadata):
    result=deepcopy(metadata)
    for page in (result.get('result') or {}).get('pages') or []:
        page.pop('preview',None)
    return result


def save_blob(store,db,identity,raw):
    if not raw:
        db.execute('DELETE FROM mail_attachment_blob_ref WHERE attachment_id=?',(identity,))
        return b''
    digest=sha256(raw).hexdigest()
    existing=db.execute('SELECT content FROM mail_blob WHERE digest=?',(digest,)).fetchone()
    if existing:
        if sha256(store.cipher.decrypt(existing[0])).hexdigest()!=digest:raise ValueError('共享原件校验失败，已保留旧原件。')
    else:
        db.execute('INSERT INTO mail_blob VALUES(?,?)',(digest,store.cipher.encrypt(raw)))
    db.execute('INSERT INTO mail_attachment_blob_ref VALUES(?,?) ON CONFLICT(attachment_id) DO UPDATE SET digest=excluded.digest',(identity,digest))
    return b''


def render_page(raw,name,number):
    from PIL import Image,ImageOps
    from .mail_attachments import MAX_ATTACHMENT_BYTES,MAX_PIXELS
    if not raw or len(raw)>MAX_ATTACHMENT_BYTES:raise ValueError('原始附件不可用或超过预览大小限制。')
    if name.lower().endswith('.pdf'):
        import pypdfium2 as pdfium
        document=pdfium.PdfDocument(raw)
        try:
            if not 1<=number<=min(10,len(document)):raise ValueError('预览页码不存在。')
            page=document[number-1]
            bitmap=None
            try:
                width,height=page.get_size()
                if width<=0 or height<=0:raise ValueError('PDF 页面尺寸无效。')
                bitmap=page.render(scale=min(2,1200/width,1600/height))
                image=bitmap.to_pil().convert('RGB')
                try:return png(image)
                finally:image.close()
            finally:
                if bitmap:bitmap.close()
                page.close()
        finally:document.close()
    if number!=1:raise ValueError('图片只有一页。')
    with Image.open(BytesIO(raw)) as source:
        if source.width*source.height>MAX_PIXELS:raise ValueError('图片超过预览像素限制。')
        image=ImageOps.exif_transpose(source).convert('RGB')
        try:return png(image)
        finally:image.close()


def png(image):
    image.thumbnail((1200,1600))
    output=BytesIO();image.save(output,format='PNG')
    return output.getvalue()


class PreviewCache:
    def __init__(self,store):
        self.store=store
        self.directory=store.path.parent/'mail-preview-cache'

    def limit_bytes(self):return self.store.get_state('mail_storage_settings',{}).get('preview_cache_mb',256)*MIB

    def files(self):return list(self.directory.glob('*.cache')) if self.directory.exists() else []

    def used_bytes(self):return sum(p.stat().st_size for p in self.files())

    def prune(self,reserve=0):
        with _CACHE_LOCK:
            # Temporary files can survive a process interruption; they are disposable.
            if self.directory.exists():
                for path in self.directory.glob('*.tmp'):path.unlink(missing_ok=True)
            files=sorted(self.files(),key=lambda p:p.stat().st_mtime)
            total=sum(p.stat().st_size for p in files)
            for path in files:
                if path.stat().st_mtime<time.time()-7*86400 or total+reserve>self.limit_bytes():
                    size=path.stat().st_size;path.unlink();total-=size

    def clear(self):
        with _CACHE_LOCK:
            size=0
            for path in self.files():size+=path.stat().st_size;path.unlink()
            return size

    def get(self,mail_id,attachment_id,number):
        with _CACHE_LOCK:
            asset=self.store.attachment(mail_id,attachment_id)
            if not asset:raise LookupError('附件不存在。')
            raw=asset['raw']
            key=sha256((sha256(raw).hexdigest()+'|'+('pdf' if asset['name'].lower().endswith('.pdf') else 'image')+'|'+str(number)+'|preview-v1').encode()).hexdigest()
            # Legacy preview-only evidence must not be discarded or mixed by empty digest.
            if not raw:
                old=next((p.get('preview') for p in (asset.get('result') or {}).get('pages',[]) if p.get('number')==number),None)
                if old:return base64.b64decode(old,validate=True)
                raise ValueError('原始附件未保存，不能重建预览；已有识别文字仍保留。')
            self.prune()
            path=self.directory/(key+'.cache')
            if path.exists():
                try:
                    value=self.store.cipher.decrypt(path.read_bytes());os.utime(path,None);return value
                except Exception:path.unlink(missing_ok=True)
            if shutil.disk_usage(self.store.path.parent).free<32*MIB:raise ValueError('磁盘空间不足，暂不能生成预览。')
            value=render_page(raw,asset['name'],number)
            encrypted=self.store.cipher.encrypt(value)
            self.prune(reserve=len(encrypted))
            if len(encrypted)<=self.limit_bytes():
                self.directory.mkdir(mode=0o700,parents=True,exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=self.directory,delete=False,suffix='.tmp') as out:
                    temporary=Path(out.name)
                    try:
                        out.write(encrypted);out.flush();os.fsync(out.fileno())
                    except Exception:temporary.unlink(missing_ok=True);raise
                try:os.replace(temporary,path)
                finally:temporary.unlink(missing_ok=True)
            return value
