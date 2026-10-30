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
_write_error=""

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

def memory_snapshot():
    """Best-effort numeric diagnostics; never inspect process command lines."""
    import os
    snapshot={'pid':os.getpid(),'available_bytes':None,'process_bytes':None}
    try:
        from .mail_workers import available_memory
        snapshot['available_bytes']=available_memory()
        import sys
        if sys.platform=='win32':
            import ctypes
            from ctypes import wintypes
            class Counters(ctypes.Structure):
                _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[
                    (name,ctypes.c_size_t) for name in ('PeakWorkingSetSize','WorkingSetSize',
                    'QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage',
                    'QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage','PrivateUsage')]
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.GetCurrentProcess.restype=wintypes.HANDLE
            api=ctypes.WinDLL('psapi',use_last_error=True).GetProcessMemoryInfo
            api.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
            counters=Counters();counters.cb=ctypes.sizeof(counters)
            if api(kernel.GetCurrentProcess(),ctypes.byref(counters),counters.cb):
                snapshot['process_bytes']=counters.WorkingSetSize
                snapshot['private_bytes']=counters.PrivateUsage
    except Exception:
        pass
    return snapshot


def record(stage,error=None,**context):
    global _write_error
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
            if now-previous<60 and not _write_error:
                _seen[key]=(previous,count+1);return not bool(_write_error)
            _seen[key]=(now,0)
            if len(_seen)>2000:_seen.clear()
            engine_reason=None
            if error and ('onnx' in kind.lower() or 'onnxruntime' in type(error).__module__.lower()):
                message=str(error).lower()
                engine_reason=next((label for needle,label in [('allocate','memory_allocation'),('out of memory','memory_allocation'),('invalid dimension','invalid_dimensions'),('invalid rank','invalid_dimensions'),('invalid input','invalid_input'),('no such file','model_file_missing'),('not implemented','unsupported_operator')] if needle in message),'unclassified_engine_error')
            row=dict(memory=memory_snapshot(),engine_reason=engine_reason,time=datetime.now(timezone.utc).isoformat(),stage=stage,error_type=kind,attribute=attribute,frames=frames,context=safe,suppressed=count)
            data=(json.dumps(row,ensure_ascii=False)+'\n').encode()
            files=sorted(path.glob('diagnostic-*.jsonl'))
            target=files[-1] if files and files[-1].stat().st_size+len(data)<=MAX_FILE else path/f'diagnostic-{time.time_ns()}.jsonl'
            with target.open('ab') as stream:stream.write(data)
            prune(path)
            _write_error=''
            return True
    except Exception as failure:
        _write_error=type(failure).__name__
        return False  # Report failure through health without interrupting business tasks.

def health():
    # No database reads or expensive storage walk; usable during storage faults.
    root=account_data_dir()
    return dict(log_directory=str(root/'logs'),write_error=_write_error)


def export(days=3):
    with LOCK:
        path=directory();prune(path);output=io.BytesIO()
        cutoff=datetime.fromtimestamp(time.time()-days*86400,timezone.utc).isoformat()
        with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('说明.txt','日志仅含步骤、异常类型、属性名和调用位置；标识已脱敏，不包含邮件正文、附件或密码。\n大小10MB/份，总上限100MB，保留14天。')
            for file in sorted(path.glob('diagnostic-*.jsonl')):
                lines=[line for line in file.read_text(encoding='utf-8').splitlines() if json.loads(line)['time']>=cutoff]
                if lines:archive.writestr(file.name,'\n'.join(lines)+'\n')
            # Explicit allowlist: never include the database, key or attachments.
            for name in ('mail-errors.log','mail-errors.log.1','mail-errors.log.2'):
                try:
                    with (account_data_dir()/'mail'/name).open('rb') as stream:
                        data=stream.read(256*1024)
                    archive.writestr(name,data)
                except FileNotFoundError:
                    pass
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
