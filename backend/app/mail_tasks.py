"""Local, opt-in mail polling. No dependency on iLog task execution."""

from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from concurrent.futures.process import BrokenProcessPool
from contextlib import ExitStack
from copy import deepcopy
import json
import sqlite3
import logging
from logging.handlers import RotatingFileHandler
from contextvars import ContextVar
from time import sleep
from datetime import date
import sys
from datetime import datetime, timedelta, timezone
from threading import Lock, RLock, Thread, Timer
from time import perf_counter, sleep
from uuid import uuid4
from .mail_storage import MailStorage, AnalysisQueue
from .mail_workers import analysis_pool, configured_workers
from .mail_recognition import RecognitionCache, VERSION as RECOGNITION_VERSION
from .mail_performance import MailPerformance
from .diagnostics import record as diagnostic_record, export as export_diagnostics, start_cleanup
from .mail_failures import AnalysisFailure, describe_failure

class FaultLogHandler(RotatingFileHandler):
    def handleError(self, record):
        # Default logging handlers swallow write/rotation failures.
        error=sys.exc_info()[1]
        if error is not None:raise error
        raise OSError('Fault log write failed')

_analysis_stage=ContextVar("mail_analysis_stage",default="读取邮件")

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field, model_validator
from starlette.concurrency import run_in_threadpool

from .outlook_probe import MailStore, data_path, list_folders, parse_eml, parse_msg, read_outlook, read_outlook_page
from .mail_attachments import analyze_attachment, MAX_MAIL_BYTES
from .mail_sync import MailSync
from .mail_workspace import MailWorkspace
from .mail_online import OnlineLedger
from .customer_online import OnlineCustomerMapping
from .mail_insights import mail_summary, related_receipts


class FolderSelection(BaseModel):
    store: str = Field(min_length=1,max_length=4096)
    folder: str = Field(min_length=1,max_length=4096)
    label: str = Field(default='',max_length=8192)


class MailSelection(BaseModel):
    store: str = Field(default='',max_length=4096)
    folder: str = Field(default='',max_length=4096)
    folders: list[FolderSelection] = Field(default_factory=list,max_length=200)
    include_children: bool = False
    start_date: date | None = None
    end_date: date | None = None
    days: int = Field(default=90,ge=1,le=3650)
    limit: int = Field(default=100,ge=1,le=500)
    reset: bool = False

    @model_validator(mode='after')
    def valid_scope(self):
        if not self.folders and not (self.store and self.folder):
            raise ValueError('请至少选择一个邮箱或存档文件夹。')
        if bool(self.start_date)!=bool(self.end_date) or (self.start_date and self.start_date>self.end_date):
            raise ValueError('请选择完整且先后顺序正确的日期范围。')
        return self

    def options(self):
        return dict(folders=[f.model_dump() for f in self.folders],include_children=self.include_children,
                    start_date=self.start_date.isoformat() if self.start_date else None,
                    end_date=self.end_date.isoformat() if self.end_date else None)


class PollSettings(MailSelection):
    enabled: bool
    seconds: int = Field(default=300,ge=60,le=86400)
    resume_on_start: bool = False


class ReceiptReview(BaseModel):
    ledger_id: str = Field(min_length=1,max_length=128)
    receipt_id: str = Field(min_length=1,max_length=128)
    payment_id: str | None = Field(default=None,max_length=128)
    state: str = Field(pattern='^(confirmed|rejected|pending)$')
    day_window: int = Field(default=3,ge=1,le=7)
    expected_signature: str | None = Field(default=None,min_length=64,max_length=64)


class OnlineWriteback(BaseModel):
    ledger_id: str = Field(min_length=1,max_length=128)
    day_window: int = Field(default=3,ge=1,le=7)
    expected_count: int | None = Field(default=None,ge=1,le=20000)
    confirmation_token: str = Field(min_length=64,max_length=64)


class CustomerMappingSetting(BaseModel):
    enabled: bool


class StorageOptimization(BaseModel):
    confirmed: bool = False
    preview_id: str = Field(min_length=1,max_length=64)
    compact: bool = True


class MailCleanupRequest(BaseModel):
    confirmed: bool = False
    preview_id: str = Field(min_length=1,max_length=64)
    selected: list[str] = Field(default_factory=list,max_length=200)


class MailRestoreRequest(BaseModel):
    backup_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class MailManager:
    def __init__(self, store, reader=read_outlook, timer_factory=Timer, analyzer=analyze_attachment, paged_reader=None, analysis_workers=None, analysis_retry_limit=3, retry_wait=sleep, permission_check=None):
        self.permission_check=permission_check;self.platform_suspended=False;self._platform_restore_enabled=False
        self.store=store; self.reader=reader; self.timer_factory=timer_factory
        self.lock=RLock(); self.timer=None; self.generation=0; self.selection=None
        self.queue_resume_scheduled=False
        self.analysis_retry_limit=max(0,min(3,analysis_retry_limit));self.retry_wait=retry_wait
        self.storage=MailStorage(store)
        self.maintenance_active=False
        previous=self.store.get_state('mail_maintenance_job',{})
        if previous.get('status')=='running':
            self.store.set_state('mail_maintenance_job',{**previous,'status':'interrupted','message':'上次存储优化被中断；数据保留，可重新预览并继续。'})
        self.analyzer=analyzer; self.analysis_queue=AnalysisQueue(store); self.analysis_busy=False; self.analysis_current=set()
        self.analysis_requested=analysis_workers if analysis_workers is not None else (4 if store.get_state('recognition_mode','light')=='fast' else 2)
        self.analysis_workers=configured_workers(analysis_workers if analysis_workers is not None else self.analysis_requested)
        self.recognition_cache=RecognitionCache(store)
        self.performance=MailPerformance()
        start_cleanup()
        self.read_progress={}
        self._status_counts={'saved_total':0,'analysis_pending':0};self._status_progress={}
        self.state=dict(busy=False,enabled=False,queue_paused=False,analysis_skipped=0,analysis_halted=False,analysis_recovering=False,seconds=300,last_run=None,next_run=None,error='',errors=[],partial=False,new=0,read=0)
        self.sync=MailSync(store);self.workspace=MailWorkspace(store);
        self.workspace.matches.load_status=lambda:dict(active=len(self.analysis_current),pending=len(self.analysis_queue),workers=self.analysis_workers,busy=bool(self.analysis_busy and not self.state.get('analysis_halted') and not self.maintenance_active and self.platform_permitted()))
        self.online=OnlineLedger(self.workspace);self.customer_online=OnlineCustomerMapping(self.workspace)
        self.paged_reader=paged_reader or (read_outlook_page if reader is read_outlook else None)
        from .mail_poll_settings import PollSettings
        self.poll_settings=PollSettings(self.store)
        self.poll_lock=self.poll_settings.lock
        self._pending_read_generation=None
        self._read_admission_generation=None
        saved=self.poll_settings.load()
        self.state.update({k:v for k,v in saved.items() if k in ('seconds','store','folder','days','limit','resume_on_start','folders','include_children','start_date','end_date')})
        self.analysis_queue.extend((identity,False) for identity in self.store.recoverable_mail_ids())
        if self.analysis_queue and self.platform_permitted():
            self.analysis_busy=True
            self._start_analysis_thread()
        if self.platform_permitted() and saved.get('enabled') and saved.get('resume_on_start') and sys.platform=='win32':
            self.configure(True,saved['store'],saved['folder'],saved['days'],saved['limit'],saved['seconds'],True,**{k:saved.get(k) for k in ('folders','include_children','start_date','end_date')})

    def set_recognition_mode(self,mode):
        if mode not in ('light','fast'):raise ValueError('识别模式无效')
        with self.lock:
            self.store.set_state('recognition_mode',mode)
            self.analysis_requested=4 if mode=='fast' else 2
            if not self.analysis_busy:self.analysis_workers=configured_workers(self.analysis_requested)
        return self.status()

    def platform_permitted(self):
        return not self.platform_suspended and (self.permission_check is None or self.permission_check())

    def suspend_platform(self):
        # Never change durable polling choices or scan/OCR checkpoints on logout.
        with self.lock:
            with self.poll_lock:
                if self.state['busy'] or self.maintenance_active or self.analysis_busy:return False
                if not self.platform_suspended:
                    self._platform_restore_enabled=self.state['enabled']
                self.platform_suspended=True
                self.generation+=1
                if self.timer:self.timer.cancel()
                self.state.update(enabled=False,next_run=None)
                return not self.analysis_busy

    def resume_platform(self):
        with self.lock:
            with self.poll_lock:
                self.platform_suspended=False
                if not self.platform_permitted():return
                restore=self._platform_restore_enabled or self.state['enabled']
                restore_generation=self.generation
                self._platform_restore_enabled=False
                if self.analysis_queue and not self.analysis_busy and not self.state['analysis_halted']:
                    self.analysis_busy=True
                    self._start_analysis_thread()
        if restore:
            saved=self.poll_settings.load()
            if saved.get('enabled'):
                self.configure(True,saved['store'],saved['folder'],saved['days'],saved['limit'],saved['seconds'],saved.get('resume_on_start',False),_expected_generation=restore_generation,**{k:saved.get(k) for k in ('folders','include_children','start_date','end_date')})

    def _read_progress(self, phase, fields=None):
        with self.lock:
            now=perf_counter()
            previous=self.read_progress
            changed=previous.get('phase')!=phase or any(previous.get(k)!=v for k,v in (fields or {}).items())
            self.read_progress={**previous, **(fields or {}), 'phase':phase,
                                'updated_at':now if changed else previous.get('updated_at',now)}
        if changed:
            diagnostic_record('mail_read_'+phase,folder_number=self.read_progress.get('folder_number',0),folders_total=self.read_progress.get('folders_total',0))

    def status(self):
        try:
            progress=self.store.get_state('sync_progress',{})
            counts=dict(saved_total=self.store.mail_count(),analysis_pending=len(self.analysis_queue)+len(self.analysis_current))
            diagnostic=None
        except (sqlite3.Error,OSError) as error:
            progress=self._status_progress
            counts=self._status_counts
            diagnostic=describe_failure(error,phase='读取状态',limit=0)
        with self.poll_lock:
            self._status_progress=progress;self._status_counts=counts
        return self._status_snapshot(diagnostic)

    def _status_snapshot(self, status_diagnostic=None):
        # Poll configuration is command-consistent. OCR fields are an eventual
        # snapshot: their writers retain the separate manager lock.
        with self.poll_lock:
            state=deepcopy(dict(self.state))
            progress=deepcopy(self._status_progress)
            counts=dict(self._status_counts)
            reading=deepcopy(dict(self.read_progress))
            reading['stage_seconds']=max(0,int(perf_counter()-reading.pop('updated_at',perf_counter()))) if state['busy'] else 0
            reading['stalled']=bool(state['busy'] and reading['stage_seconds']>=120)
            analyzing=self.analysis_busy or (counts['analysis_pending']>0 and not state['analysis_halted']
                and not self.maintenance_active and self.platform_permitted())
            return {'read_progress':reading,**state,'outlook_supported':sys.platform=='win32','analysis_busy':analyzing,
                    'errors':state['errors'] or progress.get('errors',[]),
                    'sync_phase':progress.get('phase'),'sync_complete':progress.get('complete',False),
                    'folders_total':progress.get('folders_total'),'folders_done':progress.get('folders_done'),
                    **counts,'status_diagnostic':status_diagnostic,'maintenance_active':self.maintenance_active,
                    'analysis_active':len(self.analysis_current),'analysis_workers':self.analysis_workers,
                    'recognition_mode':'fast' if self.analysis_requested==4 else 'light',
                    'recognition_switching':self.analysis_workers!=configured_workers(self.analysis_requested),
                    'performance':self.performance.snapshot(),
                    'sync_scanned':progress.get('scanned',0),'sync_start':progress.get('start'),'sync_end':progress.get('end')}

    def import_row(self, row):
        with self.lock:
            if self.maintenance_active:raise ValueError('存储优化进行中，请稍后导入邮件。')
        reason=self.storage.block_reason(reserve_bytes=32*1024**2+sum(len(a.get('raw') or b'')*3 for a in row.get('_attachments',[])))
        if reason:raise ValueError(reason)
        save_started=perf_counter()
        with self.lock:
            if self.maintenance_active:raise ValueError('存储优化进行中，请稍后导入邮件。')
            new=self.store.save([row])
            self.analyze(row['id'])
        self.performance.add_save(perf_counter()-save_started)
        return {'new':new,'id':row['id']}

    def _start_analysis_thread(self):
        # Called with the manager RLock held; claims remain durable on failure.
        self.analysis_busy=True
        try:
            Thread(target=self._analyze_queue,daemon=True).start()
        except Exception as error:
            self.analysis_busy=False
            self._record_analysis_failure(AnalysisFailure(error,'启动识别线程'),0,False)
            raise

    def analyze(self, identity, retry=False):
        with self.lock:
            if self.maintenance_active:raise ValueError('存储优化进行中，请稍后识别。')
            row=self.store.get(identity)
            if row is None: raise ValueError('邮件记录不存在，请刷新列表。')
            # Only an explicit, inactive manual request renews exhausted timeouts.
            # Controller recovery calls _drain_analysis_queue, never this reset.
            if retry and identity not in self.analysis_current:
                for asset in row.get('attachment_details', []):
                    if asset.get('status')=='failed' and asset.get('ocr_timeout_attempts'):
                        self.store.update_attachment(identity,asset['id'],
                            expected_sha256=asset.get('sha256'),ocr_timeout_attempts=0)

            summary=mail_summary(row,self.store.payment_index([identity]).get(identity,[]),indexed=False)
            self.store.set_payment_summary(identity,summary)
            if identity not in self.analysis_current: self.analysis_queue.append((identity,retry))
            if not self.analysis_busy:
                self.state['analysis_error']=''
                self.state['analysis_halted']=False
                self.analysis_busy=True
                self._start_analysis_thread()

    def _record_analysis_failure(self,error,attempt,retrying):
        info=describe_failure(error,attempt=attempt,limit=self.analysis_retry_limit)
        cause=error
        while isinstance(cause,AnalysisFailure):cause=cause.cause
        diagnostic_record('analysis_queue_failure',cause,attempt=attempt,retrying=retrying)
        info['status']='recovering' if retrying else 'paused'
        code=('/'+str(info['error_code'])) if info.get('error_code') else ''
        detail=f"阶段：{info['phase']}；原因：{info['reason']}（{info['error_type']}{code}）。"
        if retrying:
            info['message']=detail+f"正在安排自动恢复 {attempt}/{self.analysis_retry_limit} 次，已有结果保留。"
        else:
            suffix=f"自动恢复已用尽 {attempt}/{self.analysis_retry_limit} 次；" if info['retryable'] else ''
            info['message']=detail+suffix+'队列已暂停，已有结果保留。'+info['action']
        with self.lock:
            self.state['analysis_diagnostic']=info
            history=self.state.get('analysis_diagnostics',[])
            self.state['analysis_diagnostics']=(history+[dict(info)])[-20:]
            self.state['analysis_recovering']=retrying
            self.state['analysis_recovery']=info['message'] if retrying else ''
            self.state['analysis_error']='' if retrying else info['message']
            self.state['analysis_halted']=not retrying
        # Diagnostics must remain available even if SQLite cannot be written.
        # Store no raw exception message, email identity, content or credentials.
        try:
            handler=FaultLogHandler(self.store.path.parent/'mail-errors.log',maxBytes=256*1024,backupCount=2,encoding='utf-8')
            try:
                handler.emit(logging.LogRecord('mail',logging.ERROR,'',0,json.dumps(info,ensure_ascii=False),(),None))
            finally:handler.close()
        except Exception as logging_error:
            self.state['queue_log_write_error']=type(logging_error).__name__
        else:
            self.state['queue_log_write_error']=''

    def _analyze_queue(self):
        try:
            for attempt in range(self.analysis_retry_limit+1):
                try:
                    completed=self._drain_analysis_queue()
                    with self.lock:
                        self.state['analysis_recovering']=False
                        self.state['analysis_recovery']=''
                        if completed:
                            self.state['analysis_error']=''
                            self.state['analysis_halted']=False
                            if self.state.get('analysis_diagnostic',{}).get('status') in ('recovering','paused'):
                                self.state['analysis_diagnostic']={**self.state['analysis_diagnostic'],'status':'recovered','message':'任务已恢复，正在继续处理后续邮件。'}
                        else:self.state['analysis_halted']=True
                    break
                except Exception as error:
                    info=describe_failure(error)
                    retrying=info['retryable'] and attempt<self.analysis_retry_limit
                    self._record_analysis_failure(error,attempt+1 if retrying else attempt,retrying)
                    if not retrying:break
                    self.retry_wait(min(4,2**attempt))
        finally:
            final_error=None
            with self.lock:
                self.analysis_busy=False
                if self.platform_permitted() and not self.state['analysis_halted'] and not self.maintenance_active:
                    try:restart=bool(self.analysis_current or self.analysis_queue)
                    except Exception as error:
                        final_error=AnalysisFailure(error,'检查剩余识别队列')
                        restart=False
                    if restart:
                        self.analysis_busy=True
                        self._start_analysis_thread()
            if final_error:self._record_analysis_failure(final_error,0,False)
            try:self._resume_after_backpressure()
            except Exception as error:
                self._record_analysis_failure(AnalysisFailure(error,'恢复自动读取'),0,False)

    def _drain_analysis_queue(self):
        pending={};errors=[];stopping=False;phase='恢复队列断点'
        try:
            with self.lock:
                for identity in list(self.analysis_current):
                    self.analysis_queue.release(identity)
                    self.analysis_current.discard(identity)
            phase='建立复用索引'
            self.recognition_cache.prepare()
            with ExitStack() as stack:
                phase='启动识别进程'
                pool=stack.enter_context(analysis_pool(self.analysis_workers)) if self.analyzer is analyze_attachment else None
                controllers=stack.enter_context(ThreadPoolExecutor(max_workers=self.analysis_workers))
                def analyze(raw,name):
                    return pool.submit(analyze_attachment,raw,name).result() if pool else self.analyzer(raw,name)
                while True:
                    target=configured_workers(self.analysis_requested)
                    if target!=self.analysis_workers and not pending:
                        stack.close()
                        self.analysis_workers=target
                        pool=stack.enter_context(analysis_pool(target)) if self.analyzer is analyze_attachment else None
                        controllers=stack.enter_context(ThreadPoolExecutor(max_workers=target))
                    with self.lock:
                        phase='检查存储空间'
                        reason=self.storage.block_reason(reserve_bytes=32*1024**2)
                        if reason:
                            self.state['analysis_error']=reason
                            stopping=True
                        while self.platform_permitted() and not stopping and not self.maintenance_active and self.analysis_queue and self.analysis_workers==configured_workers(self.analysis_requested) and len(pending)<self.analysis_workers:
                            phase='领取识别任务'
                            identity,retry=self.analysis_queue.popleft()
                            self.analysis_current.add(identity)
                            pending[controllers.submit(self._analyze_mail,identity,retry,analyze)]=identity
                        if not pending:
                            if self.analysis_workers!=configured_workers(self.analysis_requested):continue
                            break
                    completed,_=wait(pending,timeout=.1,return_when=FIRST_COMPLETED)
                    for future in completed:
                        identity=pending.pop(future)
                        try:
                            future.result()
                        except (AnalysisFailure,sqlite3.Error,OSError,MemoryError,BrokenProcessPool) as error:
                            errors.append(error if isinstance(error,AnalysisFailure) else AnalysisFailure(error,'处理邮件'))
                            stopping=True
                            continue
                        except Exception as error:
                            phase='记录并跳过异常邮件'
                            self._skip_bad_mail(identity,error)
                        phase='保存识别队列进度'
                        with self.lock:
                            # Manager lock orders enqueue/current bookkeeping; the
                            # short DB transaction serializes pending check with save.
                            with self.store.db() as db:
                                metadata=db.execute('SELECT metadata FROM mail_attachment WHERE mail_id=?',
                                                    (identity,)).fetchall()
                                if any(self.store.decode(payload).get('status') in ('pending','running')
                                       for payload, in metadata):
                                    from .outlook_probe import StaleAttachmentError
                                    errors.append(AnalysisFailure(StaleAttachmentError(
                                        'Mail changed before queue acknowledgement'),'保存识别队列进度'))
                                    stopping=True
                                    continue
                                # Same write transaction as pending check: concurrent
                                # save cannot slip new work between the check and ack.
                                db.execute('DELETE FROM mail_analysis_queue WHERE mail_id=?',(identity,))
                            self.analysis_current.discard(identity)
                    phase='恢复自动读取'
                    self._resume_after_backpressure()
        except Exception as error:
            errors.append(error if isinstance(error,AnalysisFailure) else AnalysisFailure(error,phase))
        finally:
            # Both pools have closed before releasing claims or rebuilding.
            # A failed release stays claimed and is retried at the next drain.
            with self.lock:
                for identity in list(self.analysis_current):
                    try:self.analysis_queue.release(identity)
                    except Exception as error:
                        errors.append(AnalysisFailure(error,'恢复队列断点'))
                    else:self.analysis_current.discard(identity)
        if errors:
            raise next((error for error in errors if not describe_failure(error)['retryable']),errors[0])
        return not stopping

    def _skip_bad_mail(self, identity, error):
        row=self.store.get(identity)
        if row:
            for asset in row.get('attachment_details',[]):
                if asset.get('status') in ('pending','running'):
                    self.store.update_attachment(identity,asset['id'],status='failed',
                        error='本邮件处理异常，已跳过；其余邮件继续，可稍后单独重试。')
        index=self.store.payment_index([identity])
        summary=mail_summary(row or {},index.get(identity,[]),indexed=True)
        summary['status']='failed'
        if identity not in index:
            self.store.set_payments(identity,[],summary)
        else:
            self.store.set_payment_summary(identity,summary)
        failures=self.store.get_state('mail_analysis_skipped',[])
        failures=[entry for entry in failures if entry.get('mail_id')!=identity]
        failures.append(dict(mail_id=identity,error_type=type(error).__name__,at=datetime.now(timezone.utc).isoformat()))
        self.store.set_state('mail_analysis_skipped',failures[-100:])
        with self.lock:
            self.state['analysis_skipped']+=1
            self.state['analysis_warning']=f"本次已跳过 {self.state['analysis_skipped']} 封处理异常的邮件，其余邮件继续；可在邮件详情单独重试。"

    def _resume_after_backpressure(self):
        with self.lock:
            with self.poll_lock:
                if (not self.platform_permitted() or not self.state['enabled'] or self.state['busy'] or not self.state['queue_paused']
                        or self.queue_resume_scheduled or not self.selection or self.state.get('analysis_error') or self.maintenance_active):
                    return
                low_water=max(1,self.storage.settings()['queue_limit']//2)
                if len(self.analysis_queue)+len(self.analysis_current)>low_water:
                    return
                if self.storage.block_reason(reserve_bytes=32*1024**2):
                    return
                if self.timer:
                    self.timer.cancel()
                self.generation+=1
                generation=self.generation
                self.queue_resume_scheduled=True
                self.state['next_run']=(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat()
                selection=self.selection;selection_options=deepcopy(self.selection_options)
                self.timer=self.timer_factory(1,lambda:self.start(*selection,generation=generation,**selection_options))
                self.timer.daemon=True
                self.timer.start()

    def _analyze_mail(self, identity, retry, analyzer=None):
        token=_analysis_stage.set('读取邮件')
        try:return self._process_mail(identity,retry,analyzer)
        except (sqlite3.Error,OSError,MemoryError,BrokenProcessPool) as error:
            raise AnalysisFailure(error,_analysis_stage.get()) from error
        finally:_analysis_stage.reset(token)

    def _process_mail(self, identity, retry, analyzer=None):
        from hashlib import sha256
        from .outlook_probe import StaleAttachmentError
        from .mail_workers import AttachmentOCRTimeout, AttachmentOCRExhausted
        row=self.store.get(identity)
        for metadata in (row or {}).get('attachment_details',[]):
            allowed=('pending','running','failed') if retry else ('pending','running')
            if metadata['status'] not in allowed: continue
            _analysis_stage.set('读取附件')
            asset=self.store.attachment(identity,metadata['id'])
            if not asset: continue
            if not asset['raw']:
                # A legacy empty original is terminal for this pass, not a
                # concurrent replacement. Compare metadata under the writer gate.
                with self.store.db() as db:
                    current=db.execute('SELECT metadata FROM mail_attachment WHERE mail_id=? AND id=?',
                                       (identity,metadata['id'])).fetchone()
                    expected={k:v for k,v in asset.items() if k!='raw'}
                    if current is None or self.store.decode(current[0])!=expected:
                        raise StaleAttachmentError('Attachment changed while checking missing original')
                    expected.update(status='failed',error='附件原件为空或未保存，无法识别；请从 Outlook 重新导入该邮件后重试。')
                    db.execute('UPDATE mail_attachment SET metadata=? WHERE mail_id=? AND id=?',
                               (self.store.encode(expected),identity,metadata['id']))
                diagnostic_record('attachment_original_missing',mail_id=identity,attachment_id=metadata['id'])
                continue
            expected_sha256=sha256(asset['raw']).hexdigest()
            save_started=perf_counter()
            _analysis_stage.set('保存附件处理状态')
            self.store.update_attachment(identity,asset['id'],expected_sha256=expected_sha256,status='running',error='')
            save_seconds=perf_counter()-save_started
            key = self.recognition_cache.key(asset['raw'], asset['name'])
            wait_started=perf_counter()
            with self.recognition_cache.lock(key):
                wait_seconds=perf_counter()-wait_started
                _analysis_stage.set('查询附件复用结果')
                result = self.recognition_cache.get(key)
                reused=result is not None
                analysis_seconds=0.0
                if result is None:
                    analysis_started=perf_counter()
                    try:
                        _analysis_stage.set('识别附件')
                        # A timed-out slot has already been killed/reaped. Retry
                        # this attachment once on a fresh child; siblings survive.
                        timeout_attempts=int(asset.get('ocr_timeout_attempts',0))
                        while timeout_attempts < 2:
                            try:
                                with self.performance.analyzing():
                                    result=(analyzer or self.analyzer)(asset['raw'],asset['name'])
                                break
                            except AttachmentOCRTimeout as error:
                                timeout_attempts+=1
                                diagnostic_record('attachment_ocr_timeout_recovery',error,
                                    mail_id=identity,attachment_id=asset['id'],
                                    timeout_attempt=timeout_attempts,retry_limit=1,
                                    will_retry=timeout_attempts<2,elapsed_seconds=perf_counter()-analysis_started)
                                # Persist the budget, including across unrelated
                                # controller retries/restarts. CAS protects new raw.
                                self.store.update_attachment(identity,asset['id'],
                                    expected_sha256=expected_sha256,
                                    ocr_timeout_attempts=timeout_attempts)
                        else:
                            raise AttachmentOCRExhausted('附件识别两次超时，已终止异常进程并跳过本附件；其余附件继续。')
                    except (sqlite3.Error,OSError,BrokenProcessPool,MemoryError):
                        self.performance.record_attachment(failed=True,analysis_seconds=perf_counter()-analysis_started,
                                                           save_seconds=save_seconds,wait_seconds=wait_seconds)
                        raise
                    except Exception as error:
                        diagnostic_record('attachment_recognition',error,mail_id=identity,attachment_id=asset['id'])
                        message=str(error) if isinstance(error,ValueError) else f'识别失败（{type(error).__name__}），请检查文件或完整安装包。'
                        analysis_seconds=perf_counter()-analysis_started
                        save_started=perf_counter()
                        _analysis_stage.set('保存附件失败状态')
                        self.store.update_attachment(identity,asset['id'],expected_sha256=expected_sha256,status='failed',error=message)
                        self.performance.record_attachment(failed=True,analysis_seconds=analysis_seconds,
                            save_seconds=save_seconds+perf_counter()-save_started,wait_seconds=wait_seconds)
                        continue
                    analysis_seconds=perf_counter()-analysis_started
                save_started=perf_counter()
                _analysis_stage.set('保存附件识别结果')
                self.store.update_attachment(identity,asset['id'],expected_sha256=expected_sha256,status='skipped' if result.get('status')=='skipped' else 'done',result=result,
                                             error='；'.join(result.get('warnings',[])) if result.get('status')=='skipped' else '',
                                             recognition_version=RECOGNITION_VERSION)
                self.recognition_cache.remember(key, asset['id'], result)
                self.performance.record_attachment(result,reused,analysis_seconds,
                    save_seconds+perf_counter()-save_started,wait_seconds)
        _analysis_stage.set('整理付款信息')
        from .mail_matching import build_payments
        def snapshot(db):
            mail=db.execute('SELECT payload FROM mail WHERE id=?',(identity,)).fetchone()
            assets=db.execute('SELECT id,metadata FROM mail_attachment WHERE mail_id=? ORDER BY id',
                              (identity,)).fetchall()
            return mail,assets
        with self.store.db(write=False) as db:
            before=snapshot(db)
        updated=self.store.get(identity)
        if updated:
            # New/replaced attachments must retain their queue claim for bounded retry.
            # Failed attachments are terminal for this pass, not a retry trigger.
            unfinished=[a for a in updated.get('attachment_details', []) if a.get('status') in ('pending','running')]
            if unfinished:
                diagnostic_record('mail_unfinished_attachments',mail_id=identity,
                    pending_count=sum(a.get('status')=='pending' for a in unfinished),
                    running_count=sum(a.get('status')=='running' for a in unfinished),
                    empty_size_count=sum(not a.get('size') for a in unfinished))
                raise StaleAttachmentError('Mail has unprocessed attachments')
            payments=build_payments(updated)
            summary=mail_summary(updated,payments,indexed=True)
            payload=self.store.encode(payments)
            summary_payload=self.store.encode(summary)
            revision=self.store.encode(datetime.now(timezone.utc).isoformat())
            save_started=perf_counter()
            _analysis_stage.set('保存付款信息')
            with self.store.db() as db:
                if snapshot(db) != before:
                    raise StaleAttachmentError('Mail changed while building payments')
                db.execute('INSERT INTO mail_payments VALUES(?,?) ON CONFLICT(mail_id) DO UPDATE SET payload=excluded.payload',
                           (identity,payload))
                db.execute('INSERT INTO mail_payment_summary VALUES(?,?) ON CONFLICT(mail_id) DO UPDATE SET payload=excluded.payload',
                           (identity,summary_payload))
                db.execute('INSERT INTO mail_state VALUES(?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload',
                           ('match_revision',revision))
            self.performance.add_save(perf_counter()-save_started)

    def _save_poll_settings(self, settings, merge=False):
        started=perf_counter()
        phase='mail_settings_read' if merge else 'mail_settings_write'
        try:
            if merge:
                diagnostic_record(phase)
                settings={**self.poll_settings.load(),**settings}
                diagnostic_record(phase+'_done',elapsed_seconds=perf_counter()-started)
            phase='mail_settings_write'
            diagnostic_record(phase)
            self.poll_settings.save(settings)
            diagnostic_record(phase+'_done',elapsed_seconds=perf_counter()-started)
        except Exception as error:
            diagnostic_record(phase+'_failed',error,elapsed_seconds=perf_counter()-started)
            raise

    def start(self, store, folder, days, limit, generation=None, **options):
        with self.lock:
            with self.poll_lock:
                if not self.platform_permitted():
                    if generation is not None and self._pending_read_generation==generation:
                        self._pending_read_generation=None
                        self.state['busy']=False
                    return
                if self.maintenance_active:raise ValueError('存储优化进行中，请稍后读取。')
                if generation is not None and (generation!=self.generation or not self.state['enabled']): return
                reserved=generation is not None and self._pending_read_generation==generation
                if self.state['busy'] and not reserved: raise ValueError('邮件正在读取，请等待完成后再操作。')
                self._pending_read_generation=None
                if generation is None:
                    self.generation+=1
                    if self.timer:self.timer.cancel()
                run_generation=self.generation
                self._read_admission_generation=run_generation
                self.read_progress={}
                self._read_progress('starting')
                self.queue_resume_scheduled=False
                self.state.update(busy=True,error='',next_run=None)
        with self.poll_lock:
            if (run_generation!=self.generation or (generation is not None and not self.state['enabled'])
                    or not self.platform_permitted()):
                if self._read_admission_generation==run_generation:
                    self._read_admission_generation=None
                    self.state['busy']=False
                return
            self._read_admission_generation=None
            phase='settings'
            try:
                self._save_poll_settings(dict(store=store,folder=folder,days=days,limit=limit,**options),merge=True)
                self.state.update(store=store,folder=folder,days=days,limit=limit,**options)
                phase='read_thread'
                diagnostic_record('mail_read_thread_launch')
                Thread(target=self._run,args=(store,folder,days,limit,options,run_generation,generation is None),daemon=True).start()
            except Exception as error:
                diagnostic_record('mail_read_start_failed',error,phase=phase)
                self.state.update(busy=False,enabled=False,next_run=None,error='启动邮件读取失败；请导出日志检查配置保存或线程启动异常后重试。')
                self.read_progress={'phase':'startup_failed','updated_at':perf_counter()}
                self.generation+=1
                if self.timer:self.timer.cancel()
                raise

    def _run(self, store, folder, days, limit, options=None, run_generation=None, resume_analysis=False):
        options=options or {}
        from .mail_read_progress import sink, report
        progress_token=sink.set(self._read_progress)
        diagnostic_record('mail_read_started')
        read_started=None;read_recorded=False
        try:
            if run_generation is not None and (resume_analysis or not self.state['analysis_halted']) and (self.analysis_current or self.analysis_queue) and not self.analysis_busy:
                with self.lock:
                    if not self.analysis_busy and not self.maintenance_active:
                        self.state['analysis_error']=''
                        self.state['analysis_halted']=False
                        self._start_analysis_thread()
            if self.state['analysis_halted']:
                raise ValueError('附件处理已因明确故障暂停，新增读取暂缓。请按上方故障提示处理后手动重试。')
            reason=self.storage.block_reason(reserve_bytes=32*1024**2)
            if reason: raise ValueError(reason)
            if len(self.analysis_queue)+len(self.analysis_current)>=self.storage.settings()['queue_limit']:
                with self.lock:self.state['queue_paused']=True
                raise ValueError('等待识别的邮件较多，正在消化队列；积压下降后自动读取将提前继续。')
            with self.lock:self.state['queue_paused']=False
            targets=options.get('folders')
            read_started=perf_counter()
            if self.paged_reader:
                scope=json.dumps([sorted((f['store'],f['folder']) for f in targets),bool(options.get('include_children'))]) if targets else folder
                plan=self.sync.plan(store,scope,days,start_date=options.get('start_date'),end_date=options.get('end_date'))
                if plan['complete'] and options.get('start_date'):
                    result=dict(rows=[],errors=plan.get('errors',[]),partial=False,cursor=None,read=0,
                                folders_total=plan.get('folders_total'),folders_done=plan.get('folders_done'))
                elif targets:
                    from .mail_scope import read_scope_page
                    result=read_scope_page(targets,bool(options.get('include_children')),datetime.fromisoformat(plan['start']),datetime.fromisoformat(plan['end']),limit,plan['cursor'],known_unchanged=self.store.mail_unchanged)
                else:
                    result=self.paged_reader(store,folder,datetime.fromisoformat(plan['start']),datetime.fromisoformat(plan['end']),limit,plan['cursor'],**({'known_unchanged':self.store.mail_unchanged} if self.paged_reader is read_outlook_page else {}))
            else: result=self.reader(store,folder,datetime.now(timezone.utc)-timedelta(days=days),limit)
            self.performance.record_read(perf_counter()-read_started,result.get('read',len(result['rows'])),
                result.get('unchanged',0),result.get('attachment_seconds',0))
            read_recorded=True
            reserve=32*1024**2+sum(len(a.get('raw') or b'')*3 for row in result['rows'] for a in row.get('_attachments',[]))
            reason=self.storage.block_reason(reserve_bytes=reserve)
            if reason:raise ValueError(reason)
            save_started=perf_counter()
            report('saving')
            new=self.store.save(result['rows'])
            for row in result['rows']:
                if self.store.has_mail(row['id']): self.analyze(row['id'])
            report('checkpoint')
            if self.paged_reader: self.sync.commit(plan,result)
            self.performance.add_save(perf_counter()-save_started)
            update=dict(error='',errors=result['errors'],partial=result['partial'],new=new,read=result.get('read',len(result['rows'])))
            if result['rows']: update['folder_label']=result['rows'][0].get('folder','Outlook 收件箱')
        except Exception as error:
            if not self.state.get('queue_paused') and not self.state.get('analysis_halted'):
                diagnostic_record('mail_batch_read',error)
            if read_started is not None and not read_recorded:
                self.performance.record_read(perf_counter()-read_started,0)
            update=dict(error=str(error) if isinstance(error,ValueError) else
                        describe_failure(error,phase='保存邮件与读取进度' if read_recorded else '读取 Outlook',limit=0)['message'],new=0,read=0,errors=[],partial=False)
        sink.reset(progress_token)
        diagnostic_record('mail_read_finished',failed=bool(update['error']),read=update['read'],partial=update['partial'])
        with self.poll_lock:
            finish_range=(options.get('start_date') and not update['partial'] and not update['error']
                          and (run_generation is None or run_generation==self.generation))
            if finish_range:
                try:self._save_poll_settings({'enabled':False},merge=True)
                except Exception:
                    update['error']='读取已完成，但自动读取配置保存失败，请重新保存配置。'
            self.state.update(**update,busy=False,last_run=datetime.now(timezone.utc).isoformat())
            if finish_range:self.state['enabled']=False
            if self.platform_permitted() and self.state['enabled']:
                generation=self.generation
                delay=2 if self.paged_reader and self.state['partial'] and not self.state['error'] else self.state['seconds']
                self.state['next_run']=(datetime.now(timezone.utc)+timedelta(seconds=delay)).isoformat()
                selection=self.selection;selection_options=deepcopy(self.selection_options)
                self.timer=self.timer_factory(delay,lambda:self.start(*selection,generation=generation,**selection_options))
                self.timer.daemon=True; self.timer.start()
        self._resume_after_backpressure()

    def configure(self, enabled, store, folder, days, limit, seconds, resume_on_start=False, _expected_generation=None, **options):
        with self.poll_lock:
            if _expected_generation is not None and _expected_generation!=self.generation:return self._status_snapshot()
            if self.maintenance_active:raise ValueError('存储优化进行中，请稍后修改自动读取。')
            if enabled and self.state['busy'] and self._pending_read_generation is None: raise ValueError('请等待当前读取完成后再开启定时读取。')
            previous_settings=self.poll_settings.load()
            self._save_poll_settings(dict(enabled=enabled,store=store,folder=folder,days=days,limit=limit,seconds=seconds,resume_on_start=resume_on_start,**options))
            if self.maintenance_active:
                self.poll_settings.save(previous_settings)
                raise ValueError('存储优化进行中，请稍后修改自动读取。')
            self.generation+=1
            if self.timer:self.timer.cancel()
            self.timer=None; self.selection=(store,folder,days,limit); self.selection_options=options
            self.queue_resume_scheduled=False
            if self._pending_read_generation is not None or self._read_admission_generation is not None:self.state['busy']=False
            self._pending_read_generation=None
            self._read_admission_generation=None
            if not enabled:self.state['queue_paused']=False
            self.state.update(enabled=enabled,seconds=seconds,next_run=None,resume_on_start=resume_on_start,store=store,folder=folder,days=days,limit=limit,**options)
            generation=self.generation
            if enabled:
                self._pending_read_generation=generation
                self.state['busy']=True
        if enabled:
            def admit():
                try:self.start(store,folder,days,limit,generation=generation,**options)
                except Exception as error:
                    diagnostic_record('mail_configured_start_failed',error)
                    with self.poll_lock:
                        if self._pending_read_generation==generation:
                            self._pending_read_generation=None
                            self.state.update(busy=False,enabled=False,error='启动邮件读取失败，请重试。')
            try:Thread(target=admit,daemon=True).start()
            except Exception:
                with self.poll_lock:
                    if self._pending_read_generation==generation:
                        self._pending_read_generation=None
                        self.state.update(busy=False,enabled=False,error='启动邮件读取失败，请重试。')
                raise
        return self._status_snapshot()

    def maintenance_job(self):
        return self.store.get_state('mail_maintenance_job',{})

    def maintain_storage(self,mode,preview_id=None,compact=True,selected=None,backup_id=None):
        with self.lock:
            if self.maintenance_active or self.state['busy'] or self.state['enabled']:
                raise ValueError('请先关闭自动读取并等待本批读取完成后再优化存储。')
            previous=self.maintenance_job()
            if mode=='optimize' and (previous.get('id')!=preview_id or previous.get('mode')!='preview' or previous.get('status')!='completed'):
                raise ValueError('请先完成存储优化预览，再确认执行。')
            if mode=='cleanup' and (previous.get('id')!=preview_id or previous.get('mode')!='cleanup_preview' or previous.get('status')!='completed'):
                raise ValueError('请先完成邮件清理预览，再逐封确认。')
            self.maintenance_active=True
            job=dict(id=uuid4().hex,mode=mode,status='running',phase='准备检查存储',processed=0,total=0,
                     started_at=datetime.now(timezone.utc).isoformat())
            try:self.store.set_state('mail_maintenance_job',job)
            except Exception as error:
                self.maintenance_active=False
                diagnostic_record('mail_maintenance_prepare_failed',error)
                raise
        def run():
            from .mail_maintenance import MailMaintenance
            def progress(**changes):
                job.update(changes)
                self.store.set_state('mail_maintenance_job',job)
            try:
                progress(phase='等待当前附件识别完成，剩余队列已暂停')
                while True:
                    with self.lock:processing=self.analysis_busy
                    if not processing:break
                    sleep(.2)
                maintenance=MailMaintenance(self.store)
                if mode in ('cleanup_preview','cleanup','cleanup_restore'):
                    from .mail_cleanup import MailCleanup
                    cleanup=MailCleanup(self.store)
                    if mode=='cleanup_preview':result=cleanup.preview(progress)
                    elif mode=='cleanup':
                        progress(phase='复查所选邮件并保存加密恢复备份')
                        result=cleanup.delete(previous['result']['candidates'],selected or [])
                    else:
                        progress(phase='恢复清理备份，保留后来读取的数据')
                        result=cleanup.restore(backup_id)
                else:result=maintenance.preview(progress) if mode=='preview' else maintenance.optimize(compact,progress)
                job.update(status='completed',result=result,phase='检查完成' if mode in ('preview','cleanup_preview') else '恢复完成' if mode=='cleanup_restore' else '清理完成' if mode=='cleanup' else '存储优化完成')
            except Exception as error:
                diagnostic_record('storage_maintenance',error)
                diagnostic=describe_failure(error,job.get('phase','存储维护'),limit=0)
                message=str(error) if isinstance(error,ValueError) else diagnostic['message']
                if mode.startswith('cleanup'):
                    message+='；本批数据库变更已回滚，恢复备份保留，可重新预览。'
                job.update(status='failed',message=message,diagnostic=diagnostic)
            finally:
                # Publish completion and release the maintenance guard under the
                # same lock used to start jobs. A caller seeing "completed" must
                # wait for finalization, never receive a spurious busy conflict.
                with self.lock:
                    try:
                        job['finished_at']=datetime.now(timezone.utc).isoformat()
                        self.store.set_state('mail_maintenance_job',job)
                        history=self.store.get_state('mail_maintenance_history',[])
                        self.store.set_state('mail_maintenance_history',([job]+history)[:20])
                    finally:
                        self.maintenance_active=False
                        self.state['analysis_error']=''
                        if (self.analysis_current or self.analysis_queue) and not self.analysis_busy and not self.state['analysis_halted']:
                            self.analysis_busy=True
                            self._start_analysis_thread()
        try:Thread(target=run,daemon=True).start()
        except Exception as error:
            diagnostic_record('mail_maintenance_launch_failed',error)
            with self.lock:
                self.maintenance_active=False
                job.update(status='failed',message='存储维护线程启动失败，数据未清理；请重试或导出日志。')
                self.store.set_state('mail_maintenance_job',job)
            raise
        return dict(job)

    def clear(self):
        with self.lock:
            if self.maintenance_active or self.state['busy'] or self.state['enabled'] or self.analysis_busy: raise ValueError('请先停止存储优化及定时读取并等待邮件及附件识别完成。')
            self.store.clear()
            with self.store.db() as db:db.execute('DELETE FROM mail_analysis_queue')


_manager=None
_platform_permission=None
_manager_lock=Lock()


def manager():
    global _manager
    with _manager_lock:
        if _manager is None: _manager=MailManager(MailStore(data_path()),permission_check=_platform_permission)
        return _manager


def create_router(require_local):
    """Compatibility entry point; route modules do not own manager state."""
    from .mail_routes import create_router as compose_router
    return compose_router(require_local)
