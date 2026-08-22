from io import BytesIO

import pandas as pd
import pytest
from openpyxl import load_workbook

from app.reconciliation import (
    REQUIRED_COLUMNS,
    ReconciliationError,
    inspect_combined_workbook,
    reconcile_combined_workbook,
    reconcile_workbooks,
    validate_dataframe,
)


def workbook_bytes(rows):
    buffer = BytesIO()
    pd.DataFrame(rows).to_excel(buffer, index=False)
    buffer.seek(0)
    return buffer


def test_validate_dataframe_reports_missing_required_columns():
    df = pd.DataFrame([{"公司代码": "1703", "分配": "A001"}])

    with pytest.raises(ReconciliationError) as exc:
        validate_dataframe(df, "公司A")

    assert exc.value.user_message == "Excel缺少字段：原币金额，请检查模板"
    assert REQUIRED_COLUMNS == {"公司代码", "分配", "原币金额"}


def test_reconcile_workbooks_classifies_success_difference_and_unmatched():
    company_a = workbook_bytes(
        [
            {"公司代码": "1703", "凭证编号": "A-1", "分配": "OK001", "文本": "抵消", "原币金额": 10000},
            {"公司代码": "1703", "凭证编号": "A-2", "分配": "DIFF001", "文本": "差异", "原币金额": 10000},
            {"公司代码": "1703", "凭证编号": "A-3", "分配": "ONLYA", "文本": "仅A", "原币金额": 500},
        ]
    )
    company_b = workbook_bytes(
        [
            {"公司代码": "2615", "凭证编号": "B-1", "分配": "OK001", "文本": "抵消", "原币金额": -10000},
            {"公司代码": "2615", "凭证编号": "B-2", "分配": "DIFF001", "文本": "差异", "原币金额": 8000},
            {"公司代码": "2615", "凭证编号": "B-3", "分配": "ONLYB", "文本": "仅B", "原币金额": -300},
        ]
    )

    result = reconcile_workbooks(company_a, company_b, "a.xlsx", "b.xlsx")

    assert result.company_a_code == "1703"
    assert result.company_b_code == "2615"
    assert result.summary["匹配成功"]["数量"] == 1
    assert result.summary["金额差异"]["数量"] == 1
    assert result.summary["未匹配"]["数量"] == 2
    assert result.summary["合计"]["数量"] == 4
    assert result.differences.iloc[0]["差异金额"] == 18000
    assert set(result.unmatched["未匹配原因"]) == {"仅公司A存在", "仅公司B存在"}
    assert result.output_filename == "1703_2615_对账结果.xlsx"


def test_reconcile_workbooks_rejects_same_company_code():
    company_a = workbook_bytes([{"公司代码": "1703", "分配": "A001", "原币金额": 100}])
    company_b = workbook_bytes([{"公司代码": "1703", "分配": "A001", "原币金额": -100}])

    with pytest.raises(ReconciliationError) as exc:
        reconcile_workbooks(company_a, company_b, "a.xlsx", "b.xlsx")

    assert exc.value.user_message == "两个文件属于同一家公司，无法进行比对"


def test_reconcile_workbooks_exports_expected_sheets():
    company_a = workbook_bytes([{"公司代码": "1703", "分配": "A001", "原币金额": 100}])
    company_b = workbook_bytes([{"公司代码": "2615", "分配": "A001", "原币金额": -100}])

    result = reconcile_workbooks(company_a, company_b, "a.xlsx", "b.xlsx")

    sheets = pd.read_excel(BytesIO(result.excel_bytes), sheet_name=None)

    assert list(sheets) == ["汇总结果", "匹配成功", "金额差异", "未匹配", "原始数据"]
    assert sheets["汇总结果"].iloc[0]["分类"] == "匹配成功"
    assert sheets["匹配成功"].iloc[0]["匹配状态"] == "匹配成功"


def test_reconcile_combined_workbook_splits_by_company_column_and_keeps_blank_allocations():
    workbook = workbook_bytes(
        [
            {"公司代码or伙伴公司": "1703", "凭证编号": "A-1", "分配": "OK001", "文本": "抵消", "原币金额": 100},
            {"公司代码or伙伴公司": "2615", "凭证编号": "B-1", "分配": "OK001", "文本": "抵消", "原币金额": -100},
            {"公司代码or伙伴公司": "1703", "凭证编号": "A-2", "分配": "DIFF001", "文本": "差异", "原币金额": 100},
            {"公司代码or伙伴公司": "2615", "凭证编号": "B-2", "分配": "DIFF001", "文本": "差异", "原币金额": 80},
            {"公司代码or伙伴公司": "1703", "凭证编号": "A-3", "分配": None, "文本": "空分配", "原币金额": 50},
        ]
    )

    result = reconcile_combined_workbook(workbook, "往来.xlsx")

    assert result.company_a_code == "1703"
    assert result.company_b_code == "2615"
    assert result.summary["匹配成功"]["分配数量"] == 1
    assert result.summary["金额差异"]["分配数量"] == 1
    assert result.summary["未匹配"]["分配数量"] == 1
    assert result.summary["未匹配"]["明细行数"] == 1
    assert result.unmatched.iloc[0]["未匹配说明"] == "仅1703存在"


def test_reconcile_combined_workbook_matches_real_expected_sample_counts():
    source = "/Users/hujiarong/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/wxid_hbmgncpu45ad12_be3e/temp/RWTemp/2026-08/5cf38f7010f4b8821463145fcae85380/1703-2615 截止8.20往来.xlsx"

    result = reconcile_combined_workbook(open(source, "rb"), "1703-2615 截止8.20往来.xlsx")

    assert result.summary["匹配成功"]["分配数量"] == 15
    assert result.summary["匹配成功"]["明细行数"] == 61
    assert result.summary["金额差异"]["分配数量"] == 5
    assert result.summary["金额差异"]["明细行数"] == 21
    assert result.summary["未匹配"]["分配数量"] == 66
    assert result.summary["未匹配"]["明细行数"] == 174
    assert result.output_filename == "1703_2615_分配原币金额对账结果.xlsx"


def test_reconcile_combined_workbook_exports_expected_visual_style():
    workbook = workbook_bytes(
        [
            {"公司代码or伙伴公司": "1703", "凭证编号": "A-1", "分配": "A001", "原币金额": 100},
            {"公司代码or伙伴公司": "2615", "凭证编号": "B-1", "分配": "A001", "原币金额": -100},
        ]
    )

    result = reconcile_combined_workbook(workbook, "往来.xlsx")
    wb = load_workbook(BytesIO(result.excel_bytes))

    summary = wb["处理汇总"]
    assert "A1:G1" in {str(cell_range) for cell_range in summary.merged_cells.ranges}
    assert summary["A1"].value == "1703 与 2615 分配字段 / 原币金额对账结果"
    assert summary["A1"].fill.fgColor.rgb == "FF17365D"
    assert summary["A1"].font.color.rgb == "FFFFFFFF"
    assert summary["A3"].value == "分类"
    assert summary["A3"].fill.fgColor.rgb == "FF1F4E78"
    assert summary.column_dimensions["B"].width == 72

    detail = wb["匹配成功"]
    assert detail["A1"].fill.fgColor.rgb == "FF1F4E78"
    assert detail["A1"].font.color.rgb == "FFFFFFFF"
    assert detail.column_dimensions["A"].width == 13


def test_inspect_combined_workbook_reports_valid_file_summary():
    workbook = workbook_bytes(
        [
            {"公司代码or伙伴公司": "1703", "分配": "A001", "原币金额": 100},
            {"公司代码or伙伴公司": "2615", "分配": "A001", "原币金额": -100},
            {"公司代码or伙伴公司": "1703", "分配": None, "原币金额": 50},
        ]
    )

    report = inspect_combined_workbook(workbook, "往来.xlsx")

    assert report["valid"] is True
    assert report["companyCodes"] == ["1703", "2615"]
    assert report["rowCount"] == 3
    assert report["allocationCount"] == 2
    assert report["errors"] == []
    assert "存在 1 行分配为空，系统会统一按“空白”分组处理" in report["warnings"]


def test_inspect_combined_workbook_reports_format_errors_without_tracebacks():
    workbook = workbook_bytes(
        [
            {"公司代码or伙伴公司": "1703", "分配": "A001", "原币金额": "abc"},
            {"公司代码or伙伴公司": "1703", "分配": "A002", "原币金额": 100},
        ]
    )

    report = inspect_combined_workbook(workbook, "往来.xlsx")

    assert report["valid"] is False
    assert "公司代码or伙伴公司必须且只能包含两家公司代码，当前识别到：1703" in report["errors"]
    assert "原币金额存在 1 行无法识别为数字，请检查金额列" in report["errors"]


def test_inspect_combined_workbook_rejects_missing_columns_and_wrong_extension():
    report = inspect_combined_workbook(BytesIO(b"not excel"), "往来.csv")

    assert report["valid"] is False
    assert report["errors"] == ["文件格式错误，请选择 .xlsx 或 .xls 文件"]
