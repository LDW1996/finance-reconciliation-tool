from __future__ import annotations

from io import BytesIO
from pathlib import Path
import sys
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .reconciliation import (
    ReconciliationError,
    inspect_combined_workbook,
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
):
    try:
        result = reconcile_combined_workbook(
            BytesIO(await workbook.read()),
            workbook.filename or "往来.xlsx",
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
):
    return inspect_combined_workbook(
        BytesIO(await workbook.read()),
        workbook.filename or "往来.xlsx",
        match_field=match_field,
        amount_field=amount_field,
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
