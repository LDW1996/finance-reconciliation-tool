"""Persistent sequential tasks. No automatic retry of uncertain operations."""

from contextlib import contextmanager
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
from uuid import uuid4

from .accounts import account_data_dir


def runtime():
    import shutil
    import sys
    paths = {
        'chrome': ['/Applications/Google Chrome.app', shutil.which('google-chrome')],
        'msedge': ['/Applications/Microsoft Edge.app', shutil.which('microsoft-edge')],
    }
    if sys.platform == 'win32':
        for name, suffix in [('chrome','Google/Chrome/Application/chrome.exe'), ('msedge','Microsoft/Edge/Application/msedge.exe')]:
            paths[name] = [str(Path(os.environ.get(root,'')) / suffix) for root in ('PROGRAMFILES','PROGRAMFILES(X86)','LOCALAPPDATA')]
    channels = [name for name, files in paths.items() if any(p and Path(p).exists() for p in files)]
    available = bool(channels) and all(importlib.util.find_spec(name) for name in ('playwright','json5'))
    return {'available': bool(available), 'channel': channels[0] if channels else None,
            'message': '真实任务已接入，确认后会修改 iLog' if available else '缺少浏览器或自动化依赖，暂不能执行真实任务'}


@contextmanager
def worker_lock(directory, blocking=False):
    path = directory / 'worker.lock'
    with path.open('a+b') as handle:
        try:
            if os.name == 'nt':
                import msvcrt
                handle.seek(0)
                if not handle.read(1):
                    handle.write(b'0'); handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            if os.name == 'nt':
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


class TaskManager:
    def __init__(self, directory=None, runner=None, auto_start=True):
        self.directory = Path(directory) if directory else account_data_dir() / 'ilog-tasks'
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / 'tasks.sqlite3'
        self.runner = runner
        self.auto_start = auto_start
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, request_key TEXT UNIQUE, payload TEXT, state TEXT, data TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,value TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS expired_requests (request_key TEXT PRIMARY KEY,task_id TEXT)')
            # Old tasks did not have a completion timestamp. Prefer their row times.
            now=datetime.now(timezone.utc).isoformat()
            for row in db.execute('SELECT id,data FROM tasks').fetchall():
                data=json.loads(row['data'])
                changed=False
                times=[r['time'] for r in data['rows'] if r.get('time')]
                if 'createdAt' not in data:
                    data['createdAt']=max(times) if times else now
                    changed=True
                if data['state']=='completed' and 'completedAt' not in data:
                    data['completedAt']=max(times) if times else data['createdAt']
                    changed=True
                if changed:
                    db.execute('UPDATE tasks SET data=? WHERE id=?',(json.dumps(data,ensure_ascii=False),row['id']))
        if os.name != 'nt': self.path.chmod(0o600)
        with worker_lock(self.directory) as acquired:
            if acquired: self.recover()

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            with conn: yield conn
        finally: conn.close()

    def recover(self):
        with self.db() as db:
            for row in db.execute("SELECT * FROM tasks WHERE state='running'").fetchall():
                data = json.loads(row['data'])
                for item in data['rows']:
                    if item['status'] == 'processing':
                        item.update(status='attention', message='服务中断，执行结果待核实；不会自动重试', detail={'status':'needs_verification'})
                data['state'] = 'paused'
                self.save(db, data)

    @staticmethod
    def save(db, data):
        data['updatedAt']=datetime.now(timezone.utc).isoformat()
        if data['state']=='completed': data.setdefault('completedAt',data['updatedAt'])
        db.execute('UPDATE tasks SET state=?,data=? WHERE id=?', (data['state'],json.dumps(data,ensure_ascii=False),data['id']))

    def history(self,limit=20,offset=0):
        if not 1<=limit<=100 or offset<0: raise ValueError('分页参数无效')
        with self.db() as db:
            total=db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
            rows=db.execute('SELECT data FROM tasks ORDER BY rowid DESC LIMIT ? OFFSET ?',(limit,offset)).fetchall()
        items=[]
        for row in rows:
            data=json.loads(row[0])
            items.append({key:data.get(key) for key in ('id','accountName','fallbackAccountName','state','createdAt','completedAt')} | {
                'total':len(data['rows']),'attention':sum(r['status']=='attention' for r in data['rows'])})
        return {'items':items,'total':total}

    def get(self, task_id=None):
        with self.db() as db:
            row = db.execute('SELECT data FROM tasks WHERE id=?', (task_id,)).fetchone() if task_id else db.execute('SELECT data FROM tasks ORDER BY rowid DESC LIMIT 1').fetchone()
        if row is None:
            if task_id: raise LookupError('任务不存在')
            return None
        data=json.loads(row['data'])
        def present(value):
            if isinstance(value,dict):
                filename=value.get('screenshot')
                if isinstance(filename,str) and not Path(filename).is_file():
                    value.pop('screenshot');value['screenshotExpired']=True
                for item in value.values(): present(item)
            elif isinstance(value,list):
                for item in value: present(item)
        present(data)
        return data

    def create(self, account, name, fee, order, key, confirmed, *, fallback_account=None, fallback_name=None):
        if not confirmed or not key or len(key) > 100:
            raise ValueError('请先核对清单并确认真实操作')
        if fallback_account and (fallback_account == account or not fallback_name):
            raise ValueError('兜底账号必须与主账号不同，并有有效名称')
        inputs = []
        for kind, bills in [('fee',fee),('order',order)]:
            if not isinstance(bills, list): raise ValueError('单号必须为列表')
            seen = set()
            for bill in bills:
                if not isinstance(bill,str): raise ValueError('单号格式异常')
                bill = bill.strip().upper()
                if not re.fullmatch(r'(?=[A-Z0-9]{8,40}$)(?=.*[A-Z])(?=.*[0-9])[A-Z0-9]+',bill):
                    raise ValueError('存在格式异常的单号')
                if bill not in seen:
                    inputs.append({'bill':bill,'kind':kind,'status':'waiting','message':'等待执行','time':''})
                    seen.add(bill)
        if not inputs or len(inputs)>2000: raise ValueError('每批需要 1–2000 项')
        confirmation = [account,[(r['bill'],r['kind']) for r in inputs]]
        if fallback_account: confirmation.append(fallback_account)
        payload = json.dumps(confirmation)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM expired_requests WHERE request_key=?',(key,)).fetchone():
                raise RuntimeError('该确认请求对应的历史任务已清理，不允许重放；请重新核对后新建任务')
            old = db.execute('SELECT * FROM tasks WHERE request_key=?',(key,)).fetchone()
            if old:
                if old['payload'] != payload: raise ValueError('确认请求内容已变化，请重新核对')
                return json.loads(old['data'])
            if db.execute("SELECT 1 FROM tasks WHERE state IN ('running','paused')").fetchone():
                raise RuntimeError('已有未结束任务，请先处理或取消剩余项')
            data = {'id':uuid4().hex,'account':account,'accountName':name,'state':'running','rows':inputs,'createdAt':datetime.now(timezone.utc).isoformat()}
            if fallback_account:
                data.update(fallbackAccount=fallback_account,fallbackAccountName=fallback_name)
            db.execute('INSERT INTO tasks VALUES (?,?,?,?,?)',(data['id'],key,payload,'running',json.dumps(data,ensure_ascii=False)))
        self.launch(data['id'])
        return data

    def launch(self, task_id):
        if self.auto_start:
            threading.Thread(target=self.drain,args=(task_id,),daemon=True).start()

    def control(self, task_id, action):
        if action not in ('pause','resume','cancel'): raise ValueError('未知操作')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT data FROM tasks WHERE id=?',(task_id,)).fetchone()
            if not row: raise LookupError('任务不存在')
            data = json.loads(row['data'])
            if data['state'] == 'completed': return data
            data['state'] = 'running' if action=='resume' else 'paused'
            if action=='cancel':
                for item in data['rows']:
                    if item['status']=='waiting': item.update(status='cancelled',message='已取消，未执行')
                if not any(r['status']=='processing' for r in data['rows']): data['state']='completed'
            self.save(db,data)
        if action=='resume': self.launch(task_id)
        return data

    def drain(self, task_id):
        from .ilog_unlock import run
        with worker_lock(self.directory, blocking=True) as acquired:
            if not acquired: return
            while True:
                with self.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    data = json.loads(db.execute('SELECT data FROM tasks WHERE id=?',(task_id,)).fetchone()[0])
                    if data['state']!='running': return
                    index = next((i for i,r in enumerate(data['rows']) if r['status']=='waiting'),None)
                    if index is None:
                        data['state']='completed'; self.save(db,data); return
                    item = data['rows'][index]
                    item.update(status='processing',message='正在查询、执行并复查')
                    self.save(db,data)
                attempts=[]
                actual_account, actual_name = data['account'], data['accountName']
                candidates=[(actual_account,actual_name)]
                if data.get('fallbackAccount'):
                    candidates.append((data['fallbackAccount'],data['fallbackAccountName']))
                for actual_account, actual_name in candidates:
                    # Persist the selected account before an attempt, including fallback.
                    with self.db() as db:
                        db.execute('BEGIN IMMEDIATE')
                        progress=json.loads(db.execute('SELECT data FROM tasks WHERE id=?',(task_id,)).fetchone()[0])
                        progress['rows'][index].update(actualAccount=actual_account,actualAccountName=actual_name,
                            attempts=attempts,message=f'正在使用 {actual_name} 查询、执行并复查')
                        self.save(db,progress)
                    try:
                        result = (self.runner or run)(actual_account,item['bill'],item['kind'],execute=True,channel=runtime()['channel'] or 'chrome')
                    except Exception:
                        result = {'status':'needs_verification','reason':'执行异常，未自动重试'}
                    attempts.append({'account':actual_account,'accountName':actual_name,**result})
                    with self.db() as db:
                        db.execute('BEGIN IMMEDIATE')
                        progress=json.loads(db.execute('SELECT data FROM tasks WHERE id=?',(task_id,)).fetchone()[0])
                        progress['rows'][index].update(attempts=attempts,detail=result)
                        self.save(db,progress)
                    if not (result.get('status')=='not_found' and result.get('submitted') is False):
                        break
                ok = result.get('status') in ('verified_unlocked','verified_fee_unlocked','already_unlocked','already_fee_unlocked')
                notes = {'verified_fee_unlocked':'费用小锁已解开',
                    'verified_unlocked':'复查已无锁',
                    'already_unlocked':'原记录已无锁，未重复提交',
                    'already_fee_unlocked':'小锁已处于解锁后状态，未重复提交',
                    'blocked_duplicate':'有历史提交，未再次解锁；请核对历史记录',
                    'needs_verification':'执行结果不确定，请人工核实，未自动重试',
                    'platform_confirmed':'平台确认成功，但目标状态尚未充分核实',
                    'not_found':'当前账号未找到单号，未提交',
                    'ambiguous':'查询结果不唯一或单号不精确匹配，未提交',
                    'rejected':'平台拒绝解锁，请查看平台说明'}
                with self.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    latest = json.loads(db.execute('SELECT data FROM tasks WHERE id=?',(task_id,)).fetchone()[0])
                    latest['rows'][index].update(status='success' if ok else 'attention', detail=result,
                        actualAccount=actual_account,actualAccountName=actual_name,attempts=attempts,
                        message=('历史已成功，当前再次有锁，本次重新解锁；' if result.get('relocked_resubmission') else '') + notes.get(result.get('status'),'执行异常，请人工核实') + ('；'+str(result['message']) if not ok and result.get('message') else ''),
                        time=datetime.now(timezone.utc).isoformat())
                    if not any(r['status']=='waiting' for r in latest['rows']): latest['state']='completed'
                    elif not ok: latest['state']='paused'
                    self.save(db,latest)


_manager = None
_manager_lock = threading.Lock()
def manager():
    global _manager
    with _manager_lock:
        if _manager is None: _manager = TaskManager()
    return _manager
