"""Local, opt-in mail polling. No dependency on iLog task execution."""

from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from concurrent.futures.process import BrokenProcessPool
from contextlib import ExitStack
from copy import deepcopy
import json
from datetime import date
import sys
from datetime import datetime, timedelta, timezone
from threading import Lock, Thread, Timer
from time import perf_counter
from .mail_storage import MailStorage, AnalysisQueue
from .mail_workers import analysis_pool, configured_workers
from .mail_recognition import RecognitionCache, VERSION as RECOGNITION_VERSION
from .mail_performance import MailPerformance

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field, model_validator
from starlette.concurrency import run_in_threadpool

from .outlook_probe import MailStore, data_path, list_folders, parse_eml, parse_msg, read_outlook, read_outlook_page
from .mail_attachments import analyze_attachment, MAX_MAIL_BYTES
from .mail_sync import MailSync
from .mail_workspace import MailWorkspace
from .mail_online import OnlineLedger
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




class MailManager:
    def __init__(self, store, reader=read_outlook, timer_factory=Timer, analyzer=analyze_attachment, paged_reader=None, analysis_workers=None):
        self.store=store; self.reader=reader; self.timer_factory=timer_factory
        self.lock=Lock(); self.timer=None; self.generation=0; self.selection=None
        self.queue_resume_scheduled=False
        self.storage=MailStorage(store)
        self.analyzer=analyzer; self.analysis_queue=AnalysisQueue(store); self.analysis_busy=False; self.analysis_current=set()
        self.analysis_workers=configured_workers(analysis_workers)
        self.recognition_cache=RecognitionCache(store)
        self.performance=MailPerformance()
        self.state=dict(busy=False,enabled=False,queue_paused=False,seconds=300,last_run=None,next_run=None,error='',errors=[],partial=False,new=0,read=0)
        self.sync=MailSync(store);self.workspace=MailWorkspace(store);self.online=OnlineLedger(self.workspace)
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
            progress=self.store.get_state('sync_progress',{})
            return {**deepcopy(self.state),'outlook_supported':sys.platform=='win32','analysis_busy':self.analysis_busy,
                    'errors':self.state['errors'] or progress.get('errors',[]),
                    'sync_phase':progress.get('phase'),'sync_complete':progress.get('complete',False),
                    'folders_total':progress.get('folders_total'),'folders_done':progress.get('folders_done'),
                    'saved_total':self.store.mail_count(),'analysis_pending':len(self.analysis_queue)+len(self.analysis_current),
                    'analysis_active':len(self.analysis_current),'analysis_workers':self.analysis_workers,
                    'performance':self.performance.snapshot(),
                    'sync_scanned':progress.get('scanned',0),'sync_start':progress.get('start'),'sync_end':progress.get('end')}

    def import_row(self, row):
        reason=self.storage.block_reason(reserve_bytes=32*1024**2+sum(len(a.get('raw') or b'')*3 for a in row.get('_attachments',[])))
        if reason:raise ValueError(reason)
        save_started=perf_counter()
        with self.lock: new=self.store.save([row])
        self.analyze(row['id'])
        self.performance.add_save(perf_counter()-save_started)
        return {'new':new,'id':row['id']}

    def analyze(self, identity, retry=False):
        with self.lock:
            row=self.store.get(identity)
            if row is None: raise ValueError('邮件记录不存在，请刷新列表。')
            summary=mail_summary(row,self.store.payment_index([identity]).get(identity,[]),indexed=False)
            self.store.set_payment_summary(identity,summary)
            if identity not in self.analysis_current: self.analysis_queue.append((identity,retry))
            if not self.analysis_busy:
                self.state['analysis_error']=''
                self.analysis_busy=True
                Thread(target=self._analyze_queue,daemon=True).start()

    def _analyze_queue(self):
        # Only this coordinator claims/acknowledges durable queue entries.
        # At most N mails (and N attachment payloads) can be in flight.
        pending = {}
        stopping = False
        try:
            self.recognition_cache.prepare()
            with ExitStack() as stack:
                pool = None
                if self.analyzer is analyze_attachment:
                    pool = stack.enter_context(analysis_pool(self.analysis_workers))
                controllers = stack.enter_context(ThreadPoolExecutor(max_workers=self.analysis_workers))
                def analyze(raw, name):
                    return pool.submit(analyze_attachment, raw, name).result() if pool else self.analyzer(raw, name)
                while True:
                    with self.lock:
                        reason = self.storage.block_reason(reserve_bytes=32*1024**2)
                        if reason:
                            self.state['analysis_error'] = reason
                            stopping = True
                        while not stopping and self.analysis_queue and len(pending) < self.analysis_workers:
                            identity, retry = self.analysis_queue.popleft()
                            self.analysis_current.add(identity)
                            future = controllers.submit(self._analyze_mail, identity, retry, analyze)
                            pending[future] = identity
                        if not pending:
                            break
                    completed, _ = wait(pending, timeout=.1, return_when=FIRST_COMPLETED)
                    for future in completed:
                        identity = pending.pop(future)
                        try:
                            future.result()
                            with self.lock:
                                self.analysis_queue.done(identity)
                        except Exception:
                            stopping = True
                            with self.lock:
                                self.analysis_queue.release(identity)
                                self.state['analysis_error'] = '识别进程或结果保存异常，已暂停队列；请检查资源后重试或重启。'
                        finally:
                            with self.lock:
                                self.analysis_current.discard(identity)
                    self._resume_after_backpressure()
        except Exception:
            stopping = True
            with self.lock:
                for identity in self.analysis_current:
                    self.analysis_queue.release(identity)
                self.analysis_current.clear()
                self.state['analysis_error'] = '识别任务启动或保存失败，请检查本机资源并重试。'
        finally:
            # Keep busy until both pools have exited. New arrivals during
            # shutdown stay queued, then start a fresh coordinator atomically.
            with self.lock:
                self.analysis_busy = False
                if not stopping and self.analysis_queue:
                    self.analysis_busy = True
                    Thread(target=self._analyze_queue, daemon=True).start()
            self._resume_after_backpressure()

    def _resume_after_backpressure(self):
        with self.lock:
            if (not self.state['enabled'] or self.state['busy'] or not self.state['queue_paused']
                    or self.queue_resume_scheduled or not self.selection or self.state.get('analysis_error')):
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
        row=self.store.get(identity)
        for metadata in (row or {}).get('attachment_details',[]):
            allowed=('pending','running','failed') if retry else ('pending','running')
            if metadata['status'] not in allowed: continue
            asset=self.store.attachment(identity,metadata['id'])
            if not asset or not asset['raw']: continue
            save_started=perf_counter()
            self.store.update_attachment(identity,asset['id'],status='running',error='')
            save_seconds=perf_counter()-save_started
            key = self.recognition_cache.key(asset['raw'], asset['name'])
            wait_started=perf_counter()
            with self.recognition_cache.lock(key):
                wait_seconds=perf_counter()-wait_started
                result = self.recognition_cache.get(key)
                reused=result is not None
                analysis_seconds=0.0
                if result is None:
                    analysis_started=perf_counter()
                    try:
                        with self.performance.analyzing():
                            result=(analyzer or self.analyzer)(asset['raw'],asset['name'])
                    except (BrokenProcessPool, MemoryError):
                        self.performance.record_attachment(failed=True,analysis_seconds=perf_counter()-analysis_started,
                                                           save_seconds=save_seconds,wait_seconds=wait_seconds)
                        raise
                    except Exception as error:
                        message=str(error) if isinstance(error,ValueError) else f'识别失败（{type(error).__name__}），请检查文件或完整安装包。'
                        analysis_seconds=perf_counter()-analysis_started
                        save_started=perf_counter()
                        self.store.update_attachment(identity,asset['id'],status='failed',error=message)
                        self.performance.record_attachment(failed=True,analysis_seconds=analysis_seconds,
                            save_seconds=save_seconds+perf_counter()-save_started,wait_seconds=wait_seconds)
                        continue
                    analysis_seconds=perf_counter()-analysis_started
                save_started=perf_counter()
                self.store.update_attachment(identity,asset['id'],status='done',result=result,error='',
                                             recognition_version=RECOGNITION_VERSION)
                self.recognition_cache.remember(key, asset['id'], result)
                self.performance.record_attachment(result,reused,analysis_seconds,
                    save_seconds+perf_counter()-save_started,wait_seconds)
        from .mail_matching import build_payments
        updated=self.store.get(identity)
        if updated:
            payments=build_payments(updated)
            save_started=perf_counter()
            self.store.set_payments(identity,payments,mail_summary(updated,payments,indexed=True))
            self.performance.add_save(perf_counter()-save_started)

    def start(self, store, folder, days, limit, generation=None, **options):
        with self.lock:
            if generation is not None and (generation!=self.generation or not self.state['enabled']): return
            if self.state['busy']: raise ValueError('邮件正在读取，请等待完成后再操作。')
            if generation is None:
                self.generation+=1
                if self.timer: self.timer.cancel()
            if self.analysis_queue and not self.analysis_busy:
                self.analysis_busy=True
                self.state['analysis_error']=''
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
            if read_started is not None and not read_recorded:
                self.performance.record_read(perf_counter()-read_started,0)
            update=dict(error=str(error) if isinstance(error,ValueError) else
                        f'读取失败（{type(error).__name__}），请检查 Outlook 登录、权限提示和本机数据目录。',new=0,read=0,errors=[],partial=False)
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



    def clear(self):
        with self.lock:
            if self.state['busy'] or self.state['enabled'] or self.analysis_busy: raise ValueError('请先停止定时读取并等待邮件及附件识别完成。')
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
    def storage():return manager().storage.snapshot()

    @router.put('/storage')
    def configure_storage(values: dict):
        try:
            if set(values)-{'max_gb','min_free_gb','queue_limit'}:raise ValueError('包含未知的保存设置。')
            manager().storage.configure(values)
            return manager().storage.snapshot()
        except ValueError as error:raise HTTPException(400,str(error)) from None

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
