"""Mail messages endpoints; manager state is obtained at request time."""
from fastapi import HTTPException, File, Query, UploadFile
from . import mail_tasks as tasks


def register(router):

    @router.post('/recognition-mode')
    def recognition_mode(body: dict): return tasks.manager().set_recognition_mode(body.get('mode'))

    @router.get('/status')
    def status(): return tasks.manager().status()

    @router.post('/folders')
    def folders():
        try: return tasks.list_folders()
        except ValueError as error: raise HTTPException(400,str(error)) from None
        except Exception as error:
            raise HTTPException(503,f'邮箱列表读取失败（{type(error).__name__}）。请检查 Outlook 登录和权限提示。') from None

    @router.post('/scan',status_code=202)
    def scan(body: tasks.MailSelection):
        try:
            if body.reset:
                if tasks.manager().status()['busy'] or tasks.manager().status()['enabled']: raise ValueError('请先停止定时读取并等待本批完成，再重新补扫。')
                tasks.manager().sync.reset()
            tasks.manager().start(body.store,body.folder,body.days,body.limit,**body.options())
        except ValueError as error: raise HTTPException(409,str(error)) from None
        return tasks.manager().status()

    @router.put('/poll')
    def poll(body: tasks.PollSettings):
        try: return tasks.manager().configure(body.enabled,body.store,body.folder,body.days,body.limit,body.seconds,body.resume_on_start,**body.options())
        except ValueError as error: raise HTTPException(409,str(error)) from None

    @router.get('/messages')
    def messages():
        store=tasks.manager().store;rows=store.rows(include_body=False)
        identities=[row['id'] for row in rows]
        index=store.payment_index(identities);summaries=store.payment_summaries(identities)
        for row in rows:row['payment_summary']=summaries.get(row['id']) or tasks.mail_summary(row,index.get(row['id'],[]),indexed=row['id'] in index)
        return rows

    @router.get('/messages/page')
    def message_page(page: int=Query(1,ge=1),page_size: int=Query(50,ge=1,le=100),keyword: str=Query('',max_length=200),payment_filter: str=Query('all',pattern='^(all|payment|partial|pending)$')):
        store=tasks.manager().store
        result=store.message_page(page,page_size,keyword,payment_filter)
        identities=[row['id'] for row in result['rows']]
        summaries=store.payment_summaries(identities);index=store.payment_index(identities)
        for row in result['rows']:row['payment_summary']=summaries.get(row['id']) or tasks.mail_summary(row,index.get(row['id'],[]),indexed=row['id'] in index)
        return result

    @router.post('/messages/{identity}/open-original')
    def open_original(identity: str):
        from .mail_open import open_original
        try:return open_original(tasks.manager().store,identity)
        except ValueError as error:raise HTTPException(404,str(error)) from None

    @router.get('/messages/{identity}')
    def message(identity: str):
        row=tasks.manager().store.get(identity)
        if row is None: raise HTTPException(404,'邮件记录不存在，请刷新列表。')
        index=tasks.manager().store.payment_index([identity])
        row['payment_candidates']=index.get(identity,[])
        row['payment_summary']=tasks.manager().store.payment_summaries([identity]).get(identity) or tasks.mail_summary(row,row['payment_candidates'],indexed=identity in index)
        from .mail_workspace import translated
        for candidate in row['payment_candidates']:candidate['warnings']=[translated(w) for w in candidate.get('warnings',[])]
        return row

    @router.get('/messages/{identity}/receipts')
    def mail_receipts(identity: str,day_window: int=Query(3,ge=1,le=7),limit: int=Query(5,ge=1,le=20),payment_id: str | None=Query(None,max_length=128),nonblocking: bool=Query(False)):
        if not tasks.manager().store.has_mail(identity):raise HTTPException(404,'邮件记录不存在，请刷新列表。')
        try:return tasks.related_receipts(tasks.manager().workspace,identity,day_window,limit,payment_id,wait=not nonblocking)
        except ValueError as error:raise HTTPException(409,str(error)) from None

    @router.delete('/messages')
    def clear():
        try: tasks.manager().clear()
        except ValueError as error: raise HTTPException(409,str(error)) from None
        return {'cleared':True}

    @router.post('/messages/{identity}/analyze',status_code=202)
    def analyze(identity: str):
        try: tasks.manager().analyze(identity,retry=True)
        except ValueError as error: raise HTTPException(404,str(error)) from None
        return {'queued':True}

    @router.post('/import')
    async def import_mail(mail: UploadFile=File(...)):
        raw=await mail.read(tasks.MAX_MAIL_BYTES+1)
        try:
            filename=(mail.filename or '').lower()
            if not filename.endswith(('.eml', '.msg')):
                raise ValueError('请选择 EML 或 MSG 邮件文件。')
            parser=tasks.parse_msg if filename.endswith('.msg') else tasks.parse_eml
            row=await tasks.run_in_threadpool(parser,raw)
            return await tasks.run_in_threadpool(tasks.manager().import_row,row)
        except ValueError as error: raise HTTPException(400,str(error)) from None
        finally: await mail.close()
