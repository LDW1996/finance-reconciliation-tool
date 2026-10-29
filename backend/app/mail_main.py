"""Isolated mail edition: no full-edition routes or service imports."""
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException, Request, Depends, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from .mail_tasks import create_router
from .mail_english import response, english_export

app=FastAPI(title='Mail Reconciliation',docs_url=None,redoc_url=None,openapi_url=None)
def require_local(request:Request):
    origin=request.headers.get('origin')
    if request.headers.get('x-finance-local')!='1' or (origin and urlparse(origin).hostname not in {'localhost','127.0.0.1','::1'}):
        raise HTTPException(403,'Use the local Mail Reconciliation application.')
@app.get('/api/mail/customer-mapping/preview',dependencies=[Depends(require_local)])
def customer_preview(status: str=Query('valid',pattern='^(valid|excluded)$'),keyword: str=Query('',max_length=200),
                     page: int=Query(1,ge=1),page_size: int=Query(50,ge=1,le=100)):
    from .mail_tasks import manager
    from .customer_mapping import preview_contacts
    from .mail_english import EXACT
    terms=('客户名称缺失','银行付款人缺失','客户名称超过300字','银行付款人超过300字','邮箱格式无法识别','与已有映射完全重复，已合并','未登记邮箱','共享邮箱','一对多关系')
    needle=keyword.strip().casefold()
    aliases=[term for term in terms if needle and needle in EXACT[term].casefold()]
    return preview_contacts(manager().store.get_state('customer_mapping',{}),status,keyword,page,page_size,aliases)

app.include_router(create_router(require_local))
@app.get('/api/health')
def health():return {'status':'ok','edition':'mail_en'}
async def localized_response(request,call_next):
    result=await call_next(request)
    if request.url.path=='/api/mail/matching/export' and result.status_code==200:
        raw=b''.join([part async for part in result.body_iterator])
        headers=dict(result.headers);headers.pop('content-length',None)
        headers['content-disposition']="attachment; filename=mail-reconciliation.xlsx"
        return Response(english_export(raw),headers=headers,media_type=result.media_type)
    if request.url.path.startswith('/api/mail') and 'application/json' in result.headers.get('content-type',''):
        raw=b''.join([part async for part in result.body_iterator])
        headers=dict(result.headers);headers.pop('content-length',None)
        return Response(json.dumps(response(json.loads(raw)),ensure_ascii=False),status_code=result.status_code,headers=headers,media_type='application/json')
    return result

@app.middleware('http')
async def english_response(request,call_next):
    from .mail_online import OUTPUT_LANGUAGE
    token=OUTPUT_LANGUAGE.set('en')
    try:return await localized_response(request,call_next)
    finally:OUTPUT_LANGUAGE.reset(token)

@app.api_route('/api/{path:path}',methods=['GET','POST','PUT','PATCH','DELETE','OPTIONS','HEAD'])
def unavailable(path:str):raise HTTPException(404,'This edition provides mail reconciliation only.')

def bundled_frontend_dir():
    if getattr(sys,'frozen',False):return Path(sys._MEIPASS)/'frontend_dist'
    return Path(os.environ.get('FINANCE_MAIL_FRONTEND_DIR',Path(__file__).resolve().parents[2]/'frontend'/'dist'))
frontend=bundled_frontend_dir()
if (frontend/'assets').exists():app.mount('/assets',StaticFiles(directory=frontend/'assets'),name='assets')
@app.get('/{path:path}',include_in_schema=False)
def frontend_page(path:str):
    if path.startswith('api/') or path=='api':raise HTTPException(404,'Not found')
    target=(frontend/path).resolve()
    if not target.is_relative_to(frontend.resolve()):raise HTTPException(404,'Not found')
    if path and target.is_file():return FileResponse(target)
    if (frontend/'index.html').exists():return FileResponse(frontend/'index.html')
    raise HTTPException(404,'Frontend not installed')
