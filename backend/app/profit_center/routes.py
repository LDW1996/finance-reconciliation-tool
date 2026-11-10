"""利润中心表格上报 HTTP 接口。

    GET    /api/profit-center/config           静态配置说明
    GET    /api/profit-center/templates        模板槽位状态（4 地）
    POST   /api/profit-center/templates        上传/替换模板（一次上传，每月复用）
    DELETE /api/profit-center/templates/{key}  清除某地模板
    POST   /api/profit-center/run              上传当月数据源文件，生成公式版结果包
    GET    /api/profit-center/download/{id}    下载结果包（一次性）

非 GET 请求走平台鉴权中间件，前端需带 X-Finance-Local / X-Requested-With，
详见 design 文档与 platform_auth_routes.py。
"""
from __future__ import annotations

from io import BytesIO
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from ..excel_upload import check_excel_archive, read_excel_upload
from .service import (
    ProfitCenterError, config_payload, delete_template, get_job,
    list_templates, parse_exec_salaries, run_job, save_templates,
)

router = APIRouter(prefix="/api/profit-center")


@router.get("/config")
def get_config():
    return config_payload()


@router.get("/templates")
def get_templates():
    return {"templates": list_templates()}


@router.post("/templates")
async def upload_templates(files: list[UploadFile] = File(...)):
    payloads: list[tuple[str, bytes]] = []
    try:
        for upload in files:
            data = await read_excel_upload(upload)
            check_excel_archive(data)
            payloads.append((upload.filename or "", data))
        result = await run_in_threadpool(save_templates, payloads)
    except ProfitCenterError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    return JSONResponse(result)


@router.delete("/templates/{key}")
def remove_template(key: str):
    try:
        result = delete_template(key)
    except ProfitCenterError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(result)


@router.post("/run")
async def run(
    files: list[UploadFile] = File(...),
    month: str = Form(""),
    exec_salaries: str = Form("{}"),
):
    payloads: list[tuple[str, bytes]] = []
    try:
        for upload in files:
            data = await read_excel_upload(upload)
            check_excel_archive(data)
            payloads.append((upload.filename or "", data))
        salaries = parse_exec_salaries(exec_salaries)
        result = await run_in_threadpool(run_job, payloads, month, salaries)
    except ProfitCenterError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    return JSONResponse(result)


@router.get("/download/{job_id}")
def download(job_id: str):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="结果包不存在或已过期，请重新生成")
    encoded = quote(job["download_name"])
    return StreamingResponse(
        BytesIO(job["zip_bytes"]),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded}",
            "X-Output-Filename": encoded,
        },
    )
