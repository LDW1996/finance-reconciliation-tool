"""Mail customers endpoints; manager state is obtained at request time."""
from fastapi import HTTPException, File, Form, Query, UploadFile
from . import mail_tasks as tasks


def register(router):

    @router.get('/customer-mapping/online/config')
    def customer_online_config():return tasks.manager().customer_online.config()

    @router.post('/customer-mapping/online/config')
    async def configure_customer_online(spreadsheet_url: str=Form(...),use_shared: bool=Form(True),credential: UploadFile | None=File(None)):
        try:
            raw=await credential.read(65537) if credential else None
            if raw and len(raw)>65536:raise ValueError('授权文件不能超过64KB。')
            return await tasks.run_in_threadpool(tasks.manager().customer_online.configure,spreadsheet_url,use_shared,raw)
        except ValueError as error:raise HTTPException(400,str(error)) from None
        finally:
            if credential:await credential.close()

    @router.post('/customer-mapping/online/refresh')
    def refresh_customer_online():
        try:return tasks.manager().customer_online.refresh()
        except ValueError as error:raise HTTPException(409,str(error)) from None

    @router.get('/customer-mapping/preview')
    def customer_mapping_preview(status: str=Query('valid',pattern='^(valid|excluded)$'),keyword: str=Query('',max_length=200),
                                 page: int=Query(1,ge=1),page_size: int=Query(50,ge=1,le=100)):
        return tasks.manager().workspace.customer_mapping_preview(status,keyword,page,page_size)

    @router.get('/customer-mapping')
    def customer_mapping():return tasks.manager().workspace.customer_mapping()

    @router.put('/customer-mapping')
    def customer_mapping_setting(body: tasks.CustomerMappingSetting):
        try:return tasks.manager().workspace.enable_customer_mapping(body.enabled)
        except ValueError as error:raise HTTPException(400,str(error)) from None

    @router.post('/customer-mapping/import')
    async def import_customer_mapping(workbook: UploadFile=File(...),sheet_name: str | None=Form(None)):
        from .customer_mapping import MAX_BYTES
        try:
            name=(workbook.filename or '').replace('\\','/').rsplit('/',1)[-1].replace('\r','').replace('\n','')
            raw=await workbook.read(MAX_BYTES+1)
            return await tasks.run_in_threadpool(tasks.manager().workspace.import_customer_mapping,raw,name,sheet_name)
        except ValueError as error:raise HTTPException(400,str(error)) from None
        finally:await workbook.close()
