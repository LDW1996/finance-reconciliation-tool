from __future__ import annotations

from io import BytesIO
from pathlib import Path
import sys
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from pydantic import BaseModel, Field, SecretStr
from urllib.parse import urlparse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from .accounts import AccountStore, account_data_dir
from . import ilog_tasks
from .ilog_retention import Retention

from .reconciliation import (
    ReconciliationError,
    inspect_combined_workbook,
    localize_message,
    reconcile_combined_workbook,
    reconcile_workbooks,
)


app = FastAPI(title="财务Excel自动核对工具")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def bundled_frontend_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / "frontend_dist"
    return Path(__file__).resolve().parents[2] / "frontend" / "dist"


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/ilog/accounts")
def ilog_accounts() -> list[dict[str, object]]:
    try:
        return AccountStore().list_accounts()
    except Exception:
        raise HTTPException(status_code=503, detail="无法读取本机账号数据，请检查应用数据目录") from None


class AccountUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    username: str = Field(min_length=1, max_length=100)
    password: SecretStr | None = None


def require_local_account_write(request: Request):
    origin = request.headers.get("origin")
    if request.headers.get("x-finance-local") != "1" or (origin and urlparse(origin).hostname not in {"localhost", "127.0.0.1", "::1"}):
        raise HTTPException(status_code=403, detail="请通过本机账号管理页面修改")


def account_values(body: AccountUpdate):
    name, username = body.name.strip(), body.username.strip()
    if not name or not username:
        raise HTTPException(status_code=400, detail="账号名称和用户名不能为空")
    return name, username, body.password.get_secret_value() if body.password is not None else None


from .mail_tasks import create_router as create_mail_router
app.include_router(create_mail_router(require_local_account_write))


@app.post("/api/ilog/accounts", status_code=201)
def create_ilog_account(body: AccountUpdate, request: Request):
    require_local_account_write(request)
    values = account_values(body)
    try:
        return AccountStore().create(*values)
    except Exception:
        raise HTTPException(status_code=503, detail="保存失败，请检查应用数据目录是否可写") from None


@app.put("/api/ilog/accounts/{account_id}")
def update_ilog_account(account_id: str, body: AccountUpdate, request: Request):
    require_local_account_write(request)
    values = account_values(body)
    try:
        return AccountStore().update(account_id, *values)
    except LookupError:
        raise HTTPException(status_code=404, detail="账号不存在，请刷新页面后重试") from None
    except Exception:
        raise HTTPException(status_code=503, detail="保存失败，请检查应用数据目录是否可写") from None


@app.delete("/api/ilog/accounts/{account_id}", status_code=204)
def delete_ilog_account(account_id: str, request: Request):
    require_local_account_write(request)
    try:
        AccountStore().delete(account_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="账号不存在，请刷新页面后重试") from None
    except Exception:
        raise HTTPException(status_code=503, detail="删除失败，请检查应用数据目录是否可写") from None


class UnlockTaskRequest(BaseModel):
    account_id: str = Field(min_length=1, max_length=100)
    fallback_account_id: str | None = Field(default=None, min_length=1, max_length=100)
    fee: list[str] = Field(default_factory=list, max_length=2000)
    order: list[str] = Field(default_factory=list, max_length=2000)
    request_id: str = Field(min_length=1, max_length=100)
    confirmed: bool = False


class TaskControl(BaseModel):
    action: str


@app.get('/api/ilog/runtime')
def ilog_runtime():
    return ilog_tasks.runtime()


@app.get('/api/ilog/tasks/latest')
def latest_ilog_task():
    manager=ilog_tasks.manager()
    Retention(manager).maybe_cleanup()
    return manager.get()


@app.get('/api/ilog/tasks')
def ilog_history(limit: int=20, offset: int=0):
    manager=ilog_tasks.manager()
    Retention(manager).maybe_cleanup()
    try:
        return manager.history(limit,offset)
    except ValueError as error:
        raise HTTPException(400,str(error)) from None


class RetentionSettings(BaseModel):
    screenshot_days: int = Field(ge=1,le=36500)
    record_days: int = Field(ge=1,le=36500)
    auto_enabled: bool


@app.get('/api/ilog/retention')
def retention_settings():
    return Retention(ilog_tasks.manager()).settings()


@app.put('/api/ilog/retention')
def update_retention(body: RetentionSettings, request: Request):
    require_local_account_write(request)
    return Retention(ilog_tasks.manager()).configure(body.screenshot_days,body.record_days,body.auto_enabled)


@app.get('/api/ilog/retention/preview')
def retention_preview():
    return Retention(ilog_tasks.manager()).cleanup(dry_run=True)


class CleanupConfirmation(BaseModel):
    confirmed: bool = False


@app.post('/api/ilog/retention/cleanup')
def cleanup_retention(body: CleanupConfirmation,request: Request):
    require_local_account_write(request)
    if not body.confirmed: raise HTTPException(400,'请确认清理过期记录')
    return Retention(ilog_tasks.manager()).cleanup()


@app.get('/api/ilog/tasks/{task_id}')
def ilog_task(task_id: str):
    try:
        return ilog_tasks.manager().get(task_id)
    except LookupError:
        raise HTTPException(404, '任务不存在') from None


@app.post('/api/ilog/tasks', status_code=201)
def create_ilog_task(body: UnlockTaskRequest, request: Request):
    require_local_account_write(request)
    if not ilog_tasks.runtime()['available']:
        raise HTTPException(503, '缺少浏览器或自动化依赖，无法执行')
    accounts = AccountStore().list_accounts()
    account = next((a for a in accounts if a['id']==body.account_id), None)
    if not account or not account['hasPassword']:
        raise HTTPException(400, '请选择已保存密码的业务账号')
    fallback = next((a for a in accounts if a['id']==body.fallback_account_id),None) if body.fallback_account_id else None
    if body.fallback_account_id and (not fallback or not fallback['hasPassword'] or fallback['id']==account['id']):
        raise HTTPException(400,'兜底账号必须存在、已保存密码，且与主账号不同')
    try:
        manager=ilog_tasks.manager()
        Retention(manager).maybe_cleanup()
        return manager.create(body.account_id, account['name'], body.fee, body.order, body.request_id, body.confirmed,
            fallback_account=fallback['id'] if fallback else None, fallback_name=fallback['name'] if fallback else None)
    except ValueError as error:
        raise HTTPException(400,str(error)) from None
    except RuntimeError as error:
        raise HTTPException(409,str(error)) from None


@app.get('/api/ilog/tasks/{task_id}/evidence/{index}')
def ilog_task_evidence(task_id: str, index: int):
    try:
        task = ilog_tasks.manager().get(task_id)
        if index < 0 or index >= len(task['rows']): raise LookupError()
        filename = task['rows'][index].get('detail',{}).get('screenshot')
        if not filename: raise LookupError()
        path = Path(filename).resolve()
        root = (account_data_dir() / 'unlock-results').resolve()
        if not path.is_relative_to(root) or path.suffix != '.png' or not path.is_file():
            raise LookupError()
        return FileResponse(path, media_type='image/png')
    except LookupError:
        raise HTTPException(404,'截图不存在') from None


@app.post('/api/ilog/tasks/{task_id}/control')
def control_ilog_task(task_id: str, body: TaskControl, request: Request):
    require_local_account_write(request)
    try:
        return ilog_tasks.manager().control(task_id,body.action)
    except LookupError:
        raise HTTPException(404,'任务不存在') from None
    except ValueError as error:
        raise HTTPException(400,str(error)) from None


from .excel_routes import router as excel_router
app.include_router(excel_router)


frontend_dir = bundled_frontend_dir()
if frontend_dir.exists():
    assets_dir = frontend_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def serve_frontend(path: str):
        target = frontend_dir / path
        if path and target.is_file():
            return FileResponse(target)
        return FileResponse(frontend_dir / "index.html")
