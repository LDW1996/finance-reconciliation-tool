"""Shared Excel-only API: imports no account, browser or mail capabilities."""
from io import BytesIO
from urllib.parse import quote
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from .reconciliation import (ReconciliationError, inspect_combined_workbook, localize_message,
                             reconcile_combined_workbook, reconcile_workbooks)
router=APIRouter()

@router.post("/api/analyze")
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


@router.post("/api/analyze-combined")
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


@router.post("/api/inspect-combined")
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


