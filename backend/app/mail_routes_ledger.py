"""Mail ledger endpoints; manager state is obtained at request time."""
from fastapi import HTTPException, File, Form, Query, UploadFile
from . import mail_tasks as tasks


def register(router):

    @router.get('/ledger')
    def ledger(): return tasks.manager().workspace.ledger()

    @router.get('/online/config')
    def online_config():return tasks.manager().online.config()

    @router.post('/online/config')
    async def configure_online(spreadsheet_url: str=Form(...),currency: str=Form('IDR'),credential: UploadFile | None=File(None)):
        try:
            raw=await credential.read(65537) if credential else None
            if raw and len(raw)>65536:raise ValueError('授权文件不能超过 64 KB。')
            return await tasks.run_in_threadpool(tasks.manager().online.configure,spreadsheet_url,currency,raw)
        except ValueError as error:raise HTTPException(400,str(error)) from None
        finally:
            if credential:await credential.close()

    @router.post('/online/refresh')
    def refresh_online():
        try:return tasks.manager().online.refresh()
        except ValueError as error:raise HTTPException(409,str(error)) from None

    @router.post('/online/writeback')
    def writeback_online(body: tasks.OnlineWriteback):
        try:return tasks.manager().online.writeback(body.ledger_id,body.day_window,body.expected_count,body.confirmation_token)
        except ValueError as error:raise HTTPException(409,str(error)) from None

    @router.get('/online/history')
    def online_history():return tasks.manager().online.history()

    @router.post('/ledger/import')
    async def import_ledger(workbook: UploadFile=File(...),currency: str=Form('IDR'),sheet_name: str | None=Form(None)):
        try:
            name=(workbook.filename or '').replace('\\','/').rsplit('/',1)[-1].replace('\r','').replace('\n','')
            if not name.lower().endswith(('.xlsx','.xls','.csv')): raise ValueError('请选择 XLSX、XLS 或 CSV 流水文件。')
            if len(currency)!=3 or not currency.isalpha(): raise ValueError('请选择有效的三位币种代码。')
            raw=await workbook.read(tasks.MAX_MAIL_BYTES+1)
            return await tasks.run_in_threadpool(tasks.manager().workspace.import_ledger,raw,name,sheet_name,currency.upper())
        except ValueError as error: raise HTTPException(400,str(error)) from None
        finally: await workbook.close()

    @router.get('/ledger/preview')
    def ledger_preview(page: int=Query(1,ge=1),page_size: int=Query(50,ge=1,le=100),keyword: str=Query('',max_length=200)):
        data=tasks.manager().workspace._dataset()
        if not data:return dict(rows=[],total=0,metadata=None)
        needle=keyword.strip().casefold()
        rows=[r for r in data['entries'] if not needle or any(needle in str(r.get(k) or '').casefold() for k in ('ref','payer','description'))]
        return dict(rows=rows[(page-1)*page_size:page*page_size],total=len(rows),metadata=tasks.manager().workspace.ledger())
