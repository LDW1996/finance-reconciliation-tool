"""Bounded local diagnostics; never serialize mail bodies, credentials or locals."""
import hashlib
import io
import json
import threading
import time
import traceback
import zipfile
from datetime import datetime, timezone
from .accounts import account_data_dir

LOCK=threading.RLock()
MAX_FILE=10*1024*1024
MAX_TOTAL=100*1024*1024
KEEP_DAYS=14
_seen={}

def directory():
    path=account_data_dir()/'logs'
    path.mkdir(parents=True,exist_ok=True,mode=0o700)
    return path

def prune(path):
    files=sorted(path.glob('diagnostic-*.jsonl'),key=lambda f:f.stat().st_mtime)
    total=sum(f.stat().st_size for f in files)
    for file in files:
        if file.stat().st_mtime<time.time()-KEEP_DAYS*86400 or total>MAX_TOTAL:
            size=file.stat().st_size;file.unlink();total-=size

def record(stage,error=None,**context):
    try:
        with LOCK:
            path=directory();prune(path)
            frames=[dict(file=f.filename.replace('\\','/').split('/')[-1],line=f.lineno,function=f.name) for f in traceback.extract_tb(error.__traceback__)] if error else []
            # Fingerprint identifiers instead of storing email addresses or subjects.
            safe={k:(v if isinstance(v,(int,float,bool)) else hashlib.sha256(str(v).encode()).hexdigest()[:16]) for k,v in context.items()}
            kind=type(error).__name__ if error else 'summary'
            attribute=getattr(error,'name',None) if isinstance(error,AttributeError) else None
            key=(stage,kind,str(attribute),str(frames[-1:]),str(safe))
            now=time.time();previous,count=_seen.get(key,(0,0))
            if now-previous<60:
                _seen[key]=(previous,count+1);return
            _seen[key]=(now,0)
            if len(_seen)>2000:_seen.clear()
            row=dict(time=datetime.now(timezone.utc).isoformat(),stage=stage,error_type=kind,attribute=attribute,frames=frames,context=safe,suppressed=count)
            data=(json.dumps(row,ensure_ascii=False)+'\n').encode()
            files=sorted(path.glob('diagnostic-*.jsonl'))
            target=files[-1] if files and files[-1].stat().st_size+len(data)<=MAX_FILE else path/f'diagnostic-{time.time_ns()}.jsonl'
            with target.open('ab') as stream:stream.write(data)
            prune(path)
    except Exception:
        pass  # Diagnostic failures must never interrupt business tasks.

def export(days=3):
    with LOCK:
        path=directory();prune(path);output=io.BytesIO()
        cutoff=datetime.fromtimestamp(time.time()-days*86400,timezone.utc).isoformat()
        with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('说明.txt','日志仅含步骤、异常类型、属性名和调用位置；标识已脱敏，不包含邮件正文、附件或密码。\n大小10MB/份，总上限100MB，保留14天。')
            for file in sorted(path.glob('diagnostic-*.jsonl')):
                lines=[line for line in file.read_text(encoding='utf-8').splitlines() if json.loads(line)['time']>=cutoff]
                if lines:archive.writestr(file.name,'\n'.join(lines)+'\n')
        return output.getvalue()

_started=False

def start_cleanup():
    global _started
    with LOCK:
        if _started:return
        _started=True
    def run():
        while True:
            try:
                with LOCK:prune(directory())
            except Exception:pass
            time.sleep(3600)
    threading.Thread(target=run,name='diagnostic-cleanup',daemon=True).start()
