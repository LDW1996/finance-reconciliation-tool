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
from threading import Lock, Thread, Timer
from time import perf_counter, sleep
from uuid import uuid4
from .mail_storage import MailStorage, AnalysisQueue
from .mail_workers import analysis_pool, configured_workers
from .mail_recognition import RecognitionCache, VERSION as RECOGNITION_VERSION
from .mail_performance import MailPerformance
from .diagnostics import record as diagnostic_record, export as export_diagnostics, start_cleanup
from .mail_failures import AnalysisFailure, describe_failure

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
    def __init__(self, store, reader=read_outlook, timer_factory=Timer, analyzer=analyze_attachment, paged_reader=None, analysis_workers=None, analysis_retry_limit=3, retry_wait=sleep):
        self.store=store; self.reader=reader; self.timer_factory=timer_factory
        self.lock=Lock(); self.timer=None; self.generation=0; self.selection=None
        self.queue_resume_scheduled=False
        self.analysis_retry_limit=max(0,min(3,analysis_retry_limit));self.retry_wait=retry_wait
        self.storage=MailStorage(store)
        self.maintenance_active=False
        previous=self.store.get_state('mail_maintenance_job',{})
        if previous.get('status')=='running':
            self.store.set_state('mail_maintenance_job',{**previous,'status':'interrupted','message':'上次存储优化被中断；数据保留，可重新预览并继续。'})
        self.analyzer=analyzer; self.analysis_queue=AnalysisQueue(store); self.analysis_busy=False; self.analysis_current=set()
        self.analysis_workers=configured_workers(analysis_workers)
        self.recognition_cache=RecognitionCache(store)
        self.performance=MailPerformance()
        start_cleanup()
        self._status_counts={'saved_total':0,'analysis_pending':0};self._status_progress={}
        self.state=dict(busy=False,enabled=False,queue_paused=False,analysis_skipped=0,analysis_halted=False,analysis_recovering=False,seconds=300,last_run=None,next_run=None,error='',errors=[],partial=False,new=0,read=0)
        self.sync=MailSync(store);self.workspace=MailWorkspace(store);self.online=OnlineLedger(self.workspace);self.customer_online=OnlineCustomerMapping(self.workspace)
        self.paged_reader=paged_reader or (read_outlook_page if reader is read_outlook else None)
        saved=self.store.get_state('poll_settings',{})
        self.state.update({k:v for k,v in saved.items() if k in ('seconds','store','folder','days','limit','resume_on_start','folders','include_children','start_date','end_date')})
        self.analysis_queue.extend((identity,False) for identity in self.store.recoverable_mail_ids())
        if self.analysis_queue:
            self.analysis_busy=True
            Thread(target=self._analyze_queue,daemon=True).start()
        if saved.get('enabled') and saved.get('resume_on_start') and sys.platform=='win32':
            self.configure(True,saved['store'],saved['folder'],saved['days'],saved['limit'],saved['seconds'],True,**{k:saved.get(k) for k in ('folders','include_children','start_date','end_date')})

    def status(self):
        with self.lock:
            try:
                progress=self.store.get_state('sync_progress',{})
                self._status_counts=dict(saved_total=self.store.mail_count(),analysis_pending=len(self.analysis_queue)+len(self.analysis_current))
                self._status_progress=progress
                status_diagnostic=None
            except (sqlite3.Error,OSError) as error:
                progress=self._status_progress
                status_diagnostic=describe_failure(error,phase='读取状态',limit=0)
            return {**deepcopy(self.state),'outlook_supported':sys.platform=='win32','analysis_busy':self.analysis_busy,
                    'errors':self.state['errors'] or progress.get('errors',[]),
                    'sync_phase':progress.get('phase'),'sync_complete':progress.get('complete',False),
                    'folders_total':progress.get('folders_total'),'folders_done':progress.get('folders_done'),
                    **self._status_counts,'status_diagnostic':status_diagnostic,'maintenance_active':self.maintenance_active,
                    'analysis_active':len(self.analysis_current),'analysis_workers':self.analysis_workers,
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

    def analyze(self, identity, retry=False):
        with self.lock:
            if self.maintenance_active:raise ValueError('存储优化进行中，请稍后识别。')
            row=self.store.get(identity)
            if row is None: raise ValueError('邮件记录不存在，请刷新列表。')
            summary=mail_summary(row,self.store.payment_index([identity]).get(identity,[]),indexed=False)
            self.store.set_payment_summary(identity,summary)
            if identity not in self.analysis_current: self.analysis_queue.append((identity,retry))
            if not self.analysis_busy:
                self.state['analysis_error']=''
                self.state['analysis_halted']=False
                self.analysis_busy=True
                Thread(target=self._analyze_queue,daemon=True).start()

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
            handler=RotatingFileHandler(self.store.path.parent/'mail-errors.log',maxBytes=256*1024,backupCount=2,encoding='utf-8')
            try:
                handler.emit(logging.LogRecord('mail',logging.ERROR,'',0,json.dumps(info,ensure_ascii=False),(),None))
            finally:handler.close()
        except Exception:pass  # Full disk must not replace the original diagnosis.

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
                if not self.state['analysis_halted'] and not self.maintenance_active:
                    try:restart=bool(self.analysis_current or self.analysis_queue)
                    except Exception as error:
                        final_error=AnalysisFailure(error,'检查剩余识别队列')
                        restart=False
                    if restart:
                        self.analysis_busy=True
                        Thread(target=self._analyze_queue,daemon=True).start()
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
                    with self.lock:
                        phase='检查存储空间'
                        reason=self.storage.block_reason(reserve_bytes=32*1024**2)
                        if reason:
                            self.state['analysis_error']=reason
                            stopping=True
                        while not stopping and not self.maintenance_active and self.analysis_queue and len(pending)<self.analysis_workers:
                            phase='领取识别任务'
                            identity,retry=self.analysis_queue.popleft()
                            self.analysis_current.add(identity)
                            pending[controllers.submit(self._analyze_mail,identity,retry,analyze)]=identity
                        if not pending:break
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
                            self.analysis_queue.done(identity)
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
            if (not self.state['enabled'] or self.state['busy'] or not self.state['queue_paused']
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
            self.timer=self.timer_factory(1,lambda:self.start(*self.selection,generation=generation,**self.selection_options))
            self.timer.daemon=True
            self.timer.start()

    def _analyze_mail(self, identity, retry, analyzer=None):
        token=_analysis_stage.set('读取邮件')
        try:return self._process_mail(identity,retry,analyzer)
        except (sqlite3.Error,OSError,MemoryError,BrokenProcessPool) as error:
            raise AnalysisFailure(error,_analysis_stage.get()) from error
        finally:_analysis_stage.reset(token)

    def _process_mail(self, identity, retry, analyzer=None):
        row=self.store.get(identity)
        for metadata in (row or {}).get('attachment_details',[]):
            allowed=('pending','running','failed') if retry else ('pending','running')
            if metadata['status'] not in allowed: continue
            _analysis_stage.set('读取附件')
            asset=self.store.attachment(identity,metadata['id'])
            if not asset or not asset['raw']: continue
            save_started=perf_counter()
            _analysis_stage.set('保存附件处理状态')
            self.store.update_attachment(identity,asset['id'],status='running',error='')
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
                        with self.performance.analyzing():
                            result=(analyzer or self.analyzer)(asset['raw'],asset['name'])
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
                        self.store.update_attachment(identity,asset['id'],status='failed',error=message)
                        self.performance.record_attachment(failed=True,analysis_seconds=analysis_seconds,
                            save_seconds=save_seconds+perf_counter()-save_started,wait_seconds=wait_seconds)
                        continue
                    analysis_seconds=perf_counter()-analysis_started
                save_started=perf_counter()
                _analysis_stage.set('保存附件识别结果')
                self.store.update_attachment(identity,asset['id'],status='done',result=result,error='',
                                             recognition_version=RECOGNITION_VERSION)
                self.recognition_cache.remember(key, asset['id'], result)
                self.performance.record_attachment(result,reused,analysis_seconds,
                    save_seconds+perf_counter()-save_started,wait_seconds)
        _analysis_stage.set('整理付款信息')
        from .mail_matching import build_payments
        updated=self.store.get(identity)
        if updated:
            payments=build_payments(updated)
            save_started=perf_counter()
            _analysis_stage.set('保存付款信息')
            self.store.set_payments(identity,payments,mail_summary(updated,payments,indexed=True))
            self.performance.add_save(perf_counter()-save_started)

    def start(self, store, folder, days, limit, generation=None, **options):
        with self.lock:
            if self.maintenance_active:raise ValueError('存储优化进行中，请稍后读取。')
            if generation is not None and (generation!=self.generation or not self.state['enabled']): return
            if self.state['busy']: raise ValueError('邮件正在读取，请等待完成后再操作。')
            if generation is None:
                self.generation+=1
                if self.timer: self.timer.cancel()
            if (self.analysis_current or self.analysis_queue) and not self.analysis_busy and (generation is None or not self.state['analysis_halted']):
                self.analysis_busy=True
                self.state['analysis_error']=''
                self.state['analysis_halted']=False
                Thread(target=self._analyze_queue,daemon=True).start()
            self.queue_resume_scheduled=False
            self.state.update(busy=True,error='',next_run=None)
            self.state.update(store=store,folder=folder,days=days,limit=limit,**options)
            saved=self.store.get_state('poll_settings',{})
            self.store.set_state('poll_settings',{**saved,'store':store,'folder':folder,'days':days,'limit':limit,**options})
        Thread(target=self._run,args=(store,folder,days,limit,options),daemon=True).start()

    def _run(self, store, folder, days, limit, options=None):
        options=options or {}
        read_started=None;read_recorded=False
        try:
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
            new=self.store.save(result['rows'])
            for row in result['rows']:
                if self.store.has_mail(row['id']): self.analyze(row['id'])
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
        with self.lock:
            self.state.update(**update,busy=False,last_run=datetime.now(timezone.utc).isoformat())
            if options.get('start_date') and not update['partial'] and not update['error']:
                self.state['enabled']=False
                saved=self.store.get_state('poll_settings',{})
                self.store.set_state('poll_settings',{**saved,'enabled':False})
            if self.state['enabled']:
                generation=self.generation
                delay=2 if self.paged_reader and self.state['partial'] and not self.state['error'] else self.state['seconds']
                self.state['next_run']=(datetime.now(timezone.utc)+timedelta(seconds=delay)).isoformat()
                self.timer=self.timer_factory(delay,lambda:self.start(*self.selection,generation=generation,**self.selection_options))
                self.timer.daemon=True; self.timer.start()
        self._resume_after_backpressure()

    def configure(self, enabled, store, folder, days, limit, seconds, resume_on_start=False, **options):
        with self.lock:
            if self.maintenance_active:raise ValueError('存储优化进行中，请稍后修改自动读取。')
            if enabled and self.state['busy']: raise ValueError('请等待当前读取完成后再开启定时读取。')
            self.generation+=1
            if self.timer: self.timer.cancel()
            self.timer=None; self.selection=(store,folder,days,limit); self.selection_options=options
            self.queue_resume_scheduled=False
            if not enabled:self.state['queue_paused']=False
            self.state.update(enabled=enabled,seconds=seconds,next_run=None,resume_on_start=resume_on_start,store=store,folder=folder,days=days,limit=limit,**options)
            self.store.set_state('poll_settings',dict(enabled=enabled,store=store,folder=folder,days=days,limit=limit,seconds=seconds,resume_on_start=resume_on_start,**options))
        if enabled: self.start(store,folder,days,limit,**options)
        return self.status()

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
            self.store.set_state('mail_maintenance_job',job)
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
                            Thread(target=self._analyze_queue,daemon=True).start()
        Thread(target=run,daemon=True).start()
        return dict(job)

    def clear(self):
        with self.lock:
            if self.maintenance_active or self.state['busy'] or self.state['enabled'] or self.analysis_busy: raise ValueError('请先停止存储优化及定时读取并等待邮件及附件识别完成。')
            self.store.clear()
            with self.store.db() as db:db.execute('DELETE FROM mail_analysis_queue')


_manager=None
_manager_lock=Lock()


def manager():
    global _manager
    with _manager_lock:
        if _manager is None: _manager=MailManager(MailStore(data_path()))
        return _manager


def create_router(require_local):
    def no_cache(response: Response): response.headers['Cache-Control']='no-store'
    router=APIRouter(prefix='/api/mail',dependencies=[Depends(require_local),Depends(no_cache)])

    @router.get('/status')
    def status(): return manager().status()

    @router.get('/storage')
    def storage():
        try:return manager().storage.snapshot()
        except (sqlite3.Error,OSError) as error:
            info=describe_failure(error,phase='查询邮件存储占用',limit=0)
            raise HTTPException(503,info['message']) from None

    @router.put('/storage')
    def configure_storage(values: dict):
        try:
            if set(values)-{'max_gb','min_free_gb','queue_limit','preview_cache_mb'}:raise ValueError('包含未知的保存设置。')
            with manager().lock:
                if manager().maintenance_active:raise ValueError('存储优化进行中，请稍后修改规则。')
                manager().storage.configure(values)
            return manager().storage.snapshot()
        except ValueError as error:raise HTTPException(400,str(error)) from None

    @router.get('/storage/maintenance')
    def maintenance_status():return manager().maintenance_job()

    @router.get('/storage/maintenance/history')
    def maintenance_history():return manager().store.get_state('mail_maintenance_history',[])

    @router.post('/storage/maintenance/preview',status_code=202)
    def maintenance_preview():
        try:return manager().maintain_storage('preview')
        except ValueError as error:raise HTTPException(409,str(error)) from None

    @router.post('/storage/maintenance/optimize',status_code=202)
    def maintenance_optimize(body: StorageOptimization):
        if not body.confirmed:raise HTTPException(400,'请先确认存储优化。')
        try:return manager().maintain_storage('optimize',body.preview_id,body.compact)
        except ValueError as error:raise HTTPException(409,str(error)) from None

    @router.get('/storage/cleanup/backups')
    def cleanup_backups():
        from .mail_cleanup import MailCleanup
        return MailCleanup(manager().store).backups()

    @router.post('/storage/cleanup/preview',status_code=202)
    def cleanup_preview():
        try:return manager().maintain_storage('cleanup_preview')
        except ValueError as error:raise HTTPException(409,str(error)) from None

    @router.post('/storage/cleanup/execute',status_code=202)
    def cleanup_execute(body: MailCleanupRequest):
        if not body.confirmed:raise HTTPException(400,'请先逐封核对并确认本地邮件清理。')
        try:return manager().maintain_storage('cleanup',body.preview_id,selected=body.selected)
        except ValueError as error:raise HTTPException(409,str(error)) from None

    @router.post('/storage/cleanup/restore',status_code=202)
    def cleanup_restore(body: MailRestoreRequest):
        try:return manager().maintain_storage('cleanup_restore',backup_id=body.backup_id)
        except ValueError as error:raise HTTPException(409,str(error)) from None

    @router.get('/messages/{identity}/attachments/{attachment_id}/preview/{number}')
    def attachment_preview(identity:str,attachment_id:str,number:int):
        from .mail_assets import PreviewCache
        try:
            with manager().lock:
                if manager().maintenance_active:raise ValueError('存储优化进行中，请稍后查看原件预览。')
                if not 1<=number<=10:raise ValueError('页码无效。')
                value=PreviewCache(manager().store).get(identity,attachment_id,number)
            return Response(value,media_type='image/png',headers={'Cache-Control':'no-store'})
        except LookupError:raise HTTPException(404,'附件不存在。') from None
        except ValueError as error:raise HTTPException(409,str(error)) from None
        except Exception:raise HTTPException(422,'附件预览生成失败，识别文字仍保留，请检查原始附件。') from None

    @router.post('/folders')
    def folders():
        try: return list_folders()
        except ValueError as error: raise HTTPException(400,str(error)) from None
        except Exception as error:
            raise HTTPException(503,f'邮箱列表读取失败（{type(error).__name__}）。请检查 Outlook 登录和权限提示。') from None

    @router.post('/scan',status_code=202)
    def scan(body: MailSelection):
        try:
            if body.reset:
                if manager().status()['busy'] or manager().status()['enabled']: raise ValueError('请先停止定时读取并等待本批完成，再重新补扫。')
                manager().sync.reset()
            manager().start(body.store,body.folder,body.days,body.limit,**body.options())
        except ValueError as error: raise HTTPException(409,str(error)) from None
        return manager().status()

    @router.put('/poll')
    def poll(body: PollSettings):
        try: return manager().configure(body.enabled,body.store,body.folder,body.days,body.limit,body.seconds,body.resume_on_start,**body.options())
        except ValueError as error: raise HTTPException(409,str(error)) from None

    @router.get('/messages')
    def messages():
        store=manager().store;rows=store.rows(include_body=False)
        identities=[row['id'] for row in rows]
        index=store.payment_index(identities);summaries=store.payment_summaries(identities)
        for row in rows:row['payment_summary']=summaries.get(row['id']) or mail_summary(row,index.get(row['id'],[]),indexed=row['id'] in index)
        return rows

    @router.get('/diagnostics/export')
    def diagnostics_export(days: int=Query(3,ge=1,le=14)):
        return Response(export_diagnostics(days),media_type='application/zip',headers={'Content-Disposition':'attachment; filename="mail-diagnostics.zip"'})

    @router.get('/messages/page')
    def message_page(page: int=Query(1,ge=1),page_size: int=Query(50,ge=1,le=100),keyword: str=Query('',max_length=200),payment_filter: str=Query('all',pattern='^(all|payment|partial|pending)$')):
        store=manager().store
        result=store.message_page(page,page_size,keyword,payment_filter)
        identities=[row['id'] for row in result['rows']]
        summaries=store.payment_summaries(identities);index=store.payment_index(identities)
        for row in result['rows']:row['payment_summary']=summaries.get(row['id']) or mail_summary(row,index.get(row['id'],[]),indexed=row['id'] in index)
        return result

    @router.get('/messages/{identity}')
    def message(identity: str):
        row=manager().store.get(identity)
        if row is None: raise HTTPException(404,'邮件记录不存在，请刷新列表。')
        index=manager().store.payment_index([identity])
        row['payment_candidates']=index.get(identity,[])
        row['payment_summary']=manager().store.payment_summaries([identity]).get(identity) or mail_summary(row,row['payment_candidates'],indexed=identity in index)
        from .mail_workspace import translated
        for candidate in row['payment_candidates']:candidate['warnings']=[translated(w) for w in candidate.get('warnings',[])]
        return row

    @router.get('/messages/{identity}/receipts')
    def mail_receipts(identity: str,day_window: int=Query(3,ge=1,le=7),limit: int=Query(5,ge=1,le=20),payment_id: str | None=Query(None,max_length=128)):
        if not manager().store.has_mail(identity):raise HTTPException(404,'邮件记录不存在，请刷新列表。')
        try:return related_receipts(manager().workspace,identity,day_window,limit,payment_id)
        except ValueError as error:raise HTTPException(409,str(error)) from None

    @router.delete('/messages')
    def clear():
        try: manager().clear()
        except ValueError as error: raise HTTPException(409,str(error)) from None
        return {'cleared':True}

    @router.post('/messages/{identity}/analyze',status_code=202)
    def analyze(identity: str):
        try: manager().analyze(identity,retry=True)
        except ValueError as error: raise HTTPException(404,str(error)) from None
        return {'queued':True}

    @router.post('/import')
    async def import_mail(mail: UploadFile=File(...)):
        raw=await mail.read(MAX_MAIL_BYTES+1)
        try:
            filename=(mail.filename or '').lower()
            if not filename.endswith(('.eml', '.msg')):
                raise ValueError('请选择 EML 或 MSG 邮件文件。')
            parser=parse_msg if filename.endswith('.msg') else parse_eml
            row=await run_in_threadpool(parser,raw)
            return await run_in_threadpool(manager().import_row,row)
        except ValueError as error: raise HTTPException(400,str(error)) from None
        finally: await mail.close()

    @router.get('/ledger')
    def ledger(): return manager().workspace.ledger()

    @router.get('/online/config')
    def online_config():return manager().online.config()

    @router.post('/online/config')
    async def configure_online(spreadsheet_url: str=Form(...),currency: str=Form('IDR'),credential: UploadFile | None=File(None)):
        try:
            raw=await credential.read(65537) if credential else None
            if raw and len(raw)>65536:raise ValueError('授权文件不能超过 64 KB。')
            return await run_in_threadpool(manager().online.configure,spreadsheet_url,currency,raw)
        except ValueError as error:raise HTTPException(400,str(error)) from None
        finally:
            if credential:await credential.close()

    @router.post('/online/refresh')
    def refresh_online():
        try:return manager().online.refresh()
        except ValueError as error:raise HTTPException(409,str(error)) from None

    @router.post('/online/writeback')
    def writeback_online(body: OnlineWriteback):
        try:return manager().online.writeback(body.ledger_id,body.day_window,body.expected_count,body.confirmation_token)
        except ValueError as error:raise HTTPException(409,str(error)) from None

    @router.get('/online/history')
    def online_history():return manager().online.history()

    @router.get('/customer-mapping/online/config')
    def customer_online_config():return manager().customer_online.config()

    @router.post('/customer-mapping/online/config')
    async def configure_customer_online(spreadsheet_url: str=Form(...),use_shared: bool=Form(True),credential: UploadFile | None=File(None)):
        try:
            raw=await credential.read(65537) if credential else None
            if raw and len(raw)>65536:raise ValueError('授权文件不能超过64KB。')
            return await run_in_threadpool(manager().customer_online.configure,spreadsheet_url,use_shared,raw)
        except ValueError as error:raise HTTPException(400,str(error)) from None
        finally:
            if credential:await credential.close()

    @router.post('/customer-mapping/online/refresh')
    def refresh_customer_online():
        try:return manager().customer_online.refresh()
        except ValueError as error:raise HTTPException(409,str(error)) from None

    @router.get('/customer-mapping/preview')
    def customer_mapping_preview(status: str=Query('valid',pattern='^(valid|excluded)$'),keyword: str=Query('',max_length=200),
                                 page: int=Query(1,ge=1),page_size: int=Query(50,ge=1,le=100)):
        return manager().workspace.customer_mapping_preview(status,keyword,page,page_size)

    @router.get('/customer-mapping')
    def customer_mapping():return manager().workspace.customer_mapping()

    @router.put('/customer-mapping')
    def customer_mapping_setting(body: CustomerMappingSetting):
        try:return manager().workspace.enable_customer_mapping(body.enabled)
        except ValueError as error:raise HTTPException(400,str(error)) from None

    @router.post('/customer-mapping/import')
    async def import_customer_mapping(workbook: UploadFile=File(...),sheet_name: str | None=Form(None)):
        from .customer_mapping import MAX_BYTES
        try:
            name=(workbook.filename or '').replace('\\','/').rsplit('/',1)[-1].replace('\r','').replace('\n','')
            raw=await workbook.read(MAX_BYTES+1)
            return await run_in_threadpool(manager().workspace.import_customer_mapping,raw,name,sheet_name)
        except ValueError as error:raise HTTPException(400,str(error)) from None
        finally:await workbook.close()

    @router.post('/ledger/import')
    async def import_ledger(workbook: UploadFile=File(...),currency: str=Form('IDR'),sheet_name: str | None=Form(None)):
        try:
            name=(workbook.filename or '').replace('\\','/').rsplit('/',1)[-1].replace('\r','').replace('\n','')
            if not name.lower().endswith(('.xlsx','.xls','.csv')): raise ValueError('请选择 XLSX、XLS 或 CSV 流水文件。')
            if len(currency)!=3 or not currency.isalpha(): raise ValueError('请选择有效的三位币种代码。')
            raw=await workbook.read(MAX_MAIL_BYTES+1)
            return await run_in_threadpool(manager().workspace.import_ledger,raw,name,sheet_name,currency.upper())
        except ValueError as error: raise HTTPException(400,str(error)) from None
        finally: await workbook.close()

    @router.get('/matching')
    def matching(scope: str=Query('unidentified',pattern='^(unidentified|unclosed|all)$'),page: int=Query(1,ge=1),
                 page_size: int=Query(50,ge=1,le=100),minimum_score: int=Query(0,ge=0,le=100),
                 day_window: int=Query(3,ge=1,le=7),candidate_limit: int=Query(3,ge=1,le=20),
                 keyword: str=Query('',max_length=200),sort_by: str=Query('source',pattern='^(source|score)$')):
        return manager().workspace.matching(scope,page,page_size,minimum_score,day_window,candidate_limit,keyword,sort_by)

    @router.post('/matching/review')
    def review(body: ReceiptReview):
        try: return manager().workspace.review(body.ledger_id,body.receipt_id,body.payment_id,body.state,body.day_window,body.expected_signature)
        except ValueError as error: raise HTTPException(409,str(error)) from None

    @router.get('/matching/export')
    def export(scope: str=Query('unidentified',pattern='^(unidentified|unclosed|all)$'),minimum_score: int=Query(0,ge=0,le=100),day_window: int=Query(3,ge=1,le=7)):
        from urllib.parse import quote
        try: raw,name=manager().workspace.export(scope,minimum_score,day_window)
        except ValueError as error: raise HTTPException(400,str(error)) from None
        return Response(raw,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        headers={'Content-Disposition':f"attachment; filename*=UTF-8''{quote(name)}"})

    return router
