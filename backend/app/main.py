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
from .accounts import AccountStore

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


@app.post("/api/analyze")
async def analyze(
    company_a: UploadFile = File(...),
    company_b: UploadFile = File(...),
    match_field: str = Form("分配"),
    amount_field: str = Form("原币金额"),
):
    try:
        result = reconcile_workbooks(
            BytesIO(await company_a.read()),
            BytesIO(await company_b.read()),
            company_a.filename or "company_a.xlsx",
            company_b.filename or "company_b.xlsx",
            match_field=match_field,
            amount_field=amount_field,
        )
    except ReconciliationError as exc:
        raise HTTPException(status_code=400, detail=exc.user_message) from exc

    encoded_filename = quote(result.output_filename)
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
        "X-Company-A-Code": result.company_a_code,
        "X-Company-B-Code": result.company_b_code,
        "X-Summary-Matched": str(result.summary["匹配成功"]["数量"]),
        "X-Summary-Differences": str(result.summary["金额差异"]["数量"]),
        "X-Summary-Unmatched": str(result.summary["未匹配"]["数量"]),
        "X-Summary-Total": str(result.summary["合计"]["数量"]),
        "X-Output-Filename": encoded_filename,
    }
    return StreamingResponse(
        BytesIO(result.excel_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


@app.post("/api/analyze-combined")
async def analyze_combined(
    workbook: UploadFile = File(...),
    match_field: str = Form("分配"),
    amount_field: str = Form("原币金额"),
    tolerance: float = Form(100),
    output_mode: str = Form("detailed"),
    locale: str = Form("zh-CN"),
):
    try:
        result = reconcile_combined_workbook(
            BytesIO(await workbook.read()),
            workbook.filename or "往来.xlsx",
            match_field=match_field,
            amount_field=amount_field,
            tolerance=tolerance,
            output_mode=output_mode,
            locale=locale,
        )
    except ReconciliationError as exc:
        raise HTTPException(status_code=400, detail=localize_message(exc.user_message, locale)) from exc

    encoded_filename = quote(result.output_filename)
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
        "X-Company-A-Code": result.company_a_code,
        "X-Company-B-Code": result.company_b_code,
        "X-Summary-Matched": str(result.summary["匹配成功"]["明细行数"]),
        "X-Summary-Differences": str(result.summary["金额差异"]["明细行数"]),
        "X-Summary-Unmatched": str(result.summary["未匹配"]["明细行数"]),
        "X-Summary-Total": str(
            result.summary["匹配成功"]["明细行数"]
            + result.summary["金额差异"]["明细行数"]
            + result.summary["未匹配"]["明细行数"]
        ),
        "X-Allocation-Matched": str(result.summary["匹配成功"]["分配数量"]),
        "X-Allocation-Differences": str(result.summary["金额差异"]["分配数量"]),
        "X-Allocation-Unmatched": str(result.summary["未匹配"]["分配数量"]),
        "X-Allocation-Exact": str(result.summary["完全一致"]["分配数量"]),
        "X-Allocation-Tolerance": str(result.summary["容差匹配"]["分配数量"]),
        "X-Output-Filename": encoded_filename,
    }
    return StreamingResponse(
        BytesIO(result.excel_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


@app.post("/api/inspect-combined")
async def inspect_combined(
    workbook: UploadFile = File(...),
    match_field: str = Form("分配"),
    amount_field: str = Form("原币金额"),
    locale: str = Form("zh-CN"),
):
    return inspect_combined_workbook(
        BytesIO(await workbook.read()),
        workbook.filename or "往来.xlsx",
        match_field=match_field,
        amount_field=amount_field,
        locale=locale,
    )


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
