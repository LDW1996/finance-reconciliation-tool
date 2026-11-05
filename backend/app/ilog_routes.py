"""iLog HTTP API; execution remains owned by ilog_tasks."""
from typing import Literal
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, SecretStr
from .accounts import AccountStore, account_data_dir
from . import ilog_tasks
from .ilog_retention import Retention


class AccountUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    username: str = Field(min_length=1, max_length=100)
    password: SecretStr | None = None


def account_values(body: AccountUpdate):
    name, username = body.name.strip(), body.username.strip()
    if not name or not username:
        raise HTTPException(status_code=400, detail="账号名称和用户名不能为空")
    return name, username, body.password.get_secret_value() if body.password is not None else None


class UnlockTaskRequest(BaseModel):
    account_id: str = Field(min_length=1, max_length=100)
    fallback_account_id: str | None = Field(default=None, min_length=1, max_length=100)
    fee: list[str] = Field(default_factory=list, max_length=2000)
    order: list[str] = Field(default_factory=list, max_length=2000)
    request_id: str = Field(min_length=1, max_length=100)
    scope: Literal["bill", "voyage"] = "bill"
    screenshot_policy: Literal["errors", "all", "none"] = "errors"
    record_timings: bool = False
    verify_after: bool = True
    preview_token: str | None = Field(default=None, max_length=200)
    confirmed: bool = False


class TaskControl(BaseModel):
    action: str


class RetentionSettings(BaseModel):
    screenshot_days: int = Field(ge=1,le=36500)
    record_days: int = Field(ge=1,le=36500)
    auto_enabled: bool


class CleanupConfirmation(BaseModel):
    confirmed: bool = False


class VoyagePreviewRequest(BaseModel):
    account_id: str = Field(min_length=1,max_length=100)
    fallback_account_id: str | None = Field(default=None,min_length=1,max_length=100)
    fee_bills: list[str] = Field(default_factory=list,max_length=2000)
    order_bills: list[str] = Field(default_factory=list,max_length=2000)


def create_accounts_router():
    """Account list read, registered before the mail router to keep legacy order."""
    router = APIRouter()

    @router.get("/api/ilog/accounts")
    def ilog_accounts() -> list[dict[str, object]]:
        try:
            return AccountStore().list_accounts()
        except Exception:
            raise HTTPException(status_code=503, detail="无法读取本机账号数据，请检查应用数据目录") from None

    return router


def create_router(require_local_account_write):
    router = APIRouter()

    @router.post("/api/ilog/accounts", status_code=201)
    def create_ilog_account(body: AccountUpdate, request: Request):
        require_local_account_write(request)
        values = account_values(body)
        try:
            return AccountStore().create(*values)
        except Exception:
            raise HTTPException(status_code=503, detail="保存失败，请检查应用数据目录是否可写") from None


    @router.put("/api/ilog/accounts/{account_id}")
    def update_ilog_account(account_id: str, body: AccountUpdate, request: Request):
        require_local_account_write(request)
        values = account_values(body)
        try:
            return AccountStore().update(account_id, *values)
        except LookupError:
            raise HTTPException(status_code=404, detail="账号不存在，请刷新页面后重试") from None
        except Exception:
            raise HTTPException(status_code=503, detail="保存失败，请检查应用数据目录是否可写") from None


    @router.delete("/api/ilog/accounts/{account_id}", status_code=204)
    def delete_ilog_account(account_id: str, request: Request):
        require_local_account_write(request)
        try:
            AccountStore().delete(account_id)
        except LookupError:
            raise HTTPException(status_code=404, detail="账号不存在，请刷新页面后重试") from None
        except Exception:
            raise HTTPException(status_code=503, detail="删除失败，请检查应用数据目录是否可写") from None


    @router.get('/api/ilog/runtime')
    def ilog_runtime():
        return ilog_tasks.runtime()


    @router.get('/api/ilog/tasks/latest')
    def latest_ilog_task():
        manager=ilog_tasks.manager()
        Retention(manager).maybe_cleanup()
        return manager.get()


    @router.get('/api/ilog/tasks')
    def ilog_history(limit: int=20, offset: int=0):
        manager=ilog_tasks.manager()
        Retention(manager).maybe_cleanup()
        try:
            return manager.history(limit,offset)
        except ValueError as error:
            raise HTTPException(400,str(error)) from None


    @router.get('/api/ilog/retention')
    def retention_settings():
        return Retention(ilog_tasks.manager()).settings()


    @router.put('/api/ilog/retention')
    def update_retention(body: RetentionSettings, request: Request):
        require_local_account_write(request)
        return Retention(ilog_tasks.manager()).configure(body.screenshot_days,body.record_days,body.auto_enabled)


    @router.get('/api/ilog/retention/preview')
    def retention_preview():
        return Retention(ilog_tasks.manager()).cleanup(dry_run=True)


    @router.post('/api/ilog/retention/cleanup')
    def cleanup_retention(body: CleanupConfirmation,request: Request):
        require_local_account_write(request)
        if not body.confirmed: raise HTTPException(400,'请确认清理过期记录')
        return Retention(ilog_tasks.manager()).cleanup()


    @router.get('/api/ilog/tasks/{task_id}')
    def ilog_task(task_id: str):
        try:
            return ilog_tasks.manager().get(task_id)
        except LookupError:
            raise HTTPException(404, '任务不存在') from None


    @router.post('/api/ilog/voyages/preview')
    def preview_ilog_voyages(body: VoyagePreviewRequest, request: Request):
        require_local_account_write(request)
        if not ilog_tasks.runtime()['available']: raise HTTPException(503,'缺少浏览器或自动化依赖，无法查询')
        accounts=AccountStore().list_accounts()
        account=next((a for a in accounts if a['id']==body.account_id),None)
        fallback=next((a for a in accounts if a['id']==body.fallback_account_id),None) if body.fallback_account_id else None
        if not account or not account['hasPassword']: raise HTTPException(400,'请选择已保存密码的业务账号')
        if body.fallback_account_id and (not fallback or not fallback['hasPassword'] or fallback['id']==account['id']):
            raise HTTPException(400,'兜底账号必须存在、已保存密码，且与主账号不同')
        try:
            return ilog_tasks.manager().preview(account['id'],account['name'],body.fee_bills,body.order_bills,
                fallback_account=fallback['id'] if fallback else None,fallback_name=fallback['name'] if fallback else None)
        except ValueError as error: raise HTTPException(400,str(error)) from None
        except RuntimeError as error: raise HTTPException(409,str(error)) from None


    @router.post('/api/ilog/tasks', status_code=201)
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
                fallback_account=fallback['id'] if fallback else None, fallback_name=fallback['name'] if fallback else None,
                scope=body.scope, screenshot_policy=body.screenshot_policy, record_timings=body.record_timings, preview_token=body.preview_token, verify_after=body.verify_after)
        except ValueError as error:
            raise HTTPException(400,str(error)) from None
        except RuntimeError as error:
            raise HTTPException(409,str(error)) from None


    @router.get('/api/ilog/tasks/{task_id}/evidence/{index}')
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


    @router.post('/api/ilog/tasks/{task_id}/control')
    def control_ilog_task(task_id: str, body: TaskControl, request: Request):
        require_local_account_write(request)
        try:
            return ilog_tasks.manager().control(task_id,body.action)
        except LookupError:
            raise HTTPException(404,'任务不存在') from None
        except ValueError as error:
            raise HTTPException(400,str(error)) from None


    return router
