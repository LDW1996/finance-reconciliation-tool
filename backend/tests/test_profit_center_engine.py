"""利润中心引擎的纯内存单元测试（不依赖微信附件 / 真实财务文件）。"""
from __future__ import annotations

import io
import zipfile

import openpyxl
import pytest

from app.profit_center.config import REGIONS, config_payload, month_col
from app.profit_center.engine import (
    ProfitCenterError, _zip_parts, build_external_link_xml, build_provenance_xlsx,
    build_verify_report, build_zip, classify, external_link_map,
    indonesia_values, inject_external_link, parse_month,
)
from app.profit_center.engine import RegionResult


def test_config_payload_shape():
    payload = config_payload("2026-08")
    assert payload["month"] == "2026-08"
    assert "2026-08" in payload["months"]
    assert "2026-01" not in payload["months"]          # 1 月不支持自动平移
    assert all(m != "2026-01" for m in payload["months"])
    assert len(payload["regions"]) == 4
    assert sum(1 for r in payload["regions"] if r["has_exec_salary"]) == 2
    assert all(isinstance(item, str) for item in payload["required_files"])
    # 必需文件清单用占位符描述，不写死具体月份
    assert all("{" in item and "}" in item for item in payload["required_files"])
    assert "202607" not in "".join(payload["required_files"])
    # 样例行按所选月份给出具体文件名
    assert "202607" in payload["required_files_sample"]
    assert "202608" in payload["required_files_sample"]
    assert "2026-08" in payload["required_files_sample"]
    assert all(r["report"].endswith("202608.XLS") for r in payload["regions"])
    assert month_col(8) == "I"


def test_config_payload_defaults_to_current_month():
    from app.profit_center.config import default_month
    assert config_payload()["month"] == default_month()


def test_parse_month():
    assert parse_month("2026-08") == (2026, 8, "202608", "202607")
    assert parse_month("2027-01") == (2027, 1, "202701", "202612")
    for bad in ("", "2026/08", "2026-13", "2026-00", "abc"):
        with pytest.raises(ProfitCenterError):
            parse_month(bad)


def _minimal_files():
    """只构造文件名语义，内容用占位字节；classify 不做解析。"""
    files = [
        (r.template, b"template") for r in REGIONS
    ]
    files += [
        (f"{r.report_stem}{p}.XLS", b"report")
        for r in REGIONS for p in ("202607", "202608")
    ]
    files.append(("财务报表-物流印尼202608.XLSX", b"indonesia"))
    return files


def test_classify_groups_by_filename_semantics():
    files = _minimal_files()
    files.append((r"C:\Users\someone\Desktop\办事处简表-物流巴淡(1).xlsx", b"finance"))
    inputs = classify(files, "2026-08")
    assert len(inputs.templates) == 4
    assert len(inputs.reports) == 8
    assert inputs.indonesia == b"indonesia"
    assert inputs.references.get("batam") == b"finance"
    assert inputs.warnings == ["已识别 1 份财务手工版，作为复现比对基准"]


def test_classify_reports_missing_inputs():
    with pytest.raises(ProfitCenterError, match="缺少简表模板"):
        classify([("财务报表-物流印尼202608.XLSX", b"x")], "2026-08")
    files = [f for f in _minimal_files() if "202608.XLS" not in f[0]]
    with pytest.raises(ProfitCenterError, match="缺少源报表"):
        classify(files, "2026-08")
    files = [f for f in _minimal_files() if "物流印尼" not in f[0]]
    with pytest.raises(ProfitCenterError, match="缺少印尼汇总表"):
        classify(files, "2026-08")


def _indonesia_bytes():
    wb = openpyxl.Workbook()
    pl = wb.active
    pl.title = "损益表-考核"
    for coord, value in {
        "C60": 1051961825, "D60": 6582847082, "F60": 4364411189,
        "C4": 14403763490, "D4": 131810768747,
        "C20": 12048544197, "D20": 117728414527,
        "C32": 201855065, "D32": 2075816155,
    }.items():
        pl[coord] = value
    mg = wb.create_sheet("管理费用表-考核")
    mg["D5"], mg["E5"] = 756245506, 5496258540
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_indonesia_values_uses_assessment_sheets():
    values = indonesia_values(_indonesia_bytes())
    assert values["net_cur_month"] == 1051961825
    assert values["net_cur_accum"] == 6582847082
    assert values["net_2025_accum"] == 4364411189
    assert values["net_delta_accum"] == 2218435893
    assert values["gross_month"] == 2355219293
    assert values["mgmt_accum"] == 5496258540


def test_indonesia_values_rejects_wrong_sheets():
    wb = openpyxl.Workbook()
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(ProfitCenterError, match="损益表-考核"):
        indonesia_values(buf.getvalue())


def _plain_workbook_bytes():
    wb = openpyxl.Workbook()
    wb.active["A1"] = "hello"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_external_link_xml_roundtrip_with_cache():
    raw = _plain_workbook_bytes()
    cache = {"Sheet": {"A1": 42, "B2": 1.5, "C3": "text"}}
    injected = inject_external_link(
        raw, "财务报表-物流巴淡-202608.XLS", ["Sheet"], cache, 1,
    )
    parts = _zip_parts(injected)
    assert "xl/externalLinks/externalLink1.xml" in parts
    assert "xl/externalLinks/_rels/externalLink1.xml.rels" in parts
    assert b"fullCalcOnLoad" in parts["xl/workbook.xml"]
    assert b"<externalReferences>" in parts["xl/workbook.xml"]
    assert b"externalLink1.xml" in parts["[Content_Types].xml"]
    assert "财务报表-物流巴淡-202608.XLS".encode("utf-8") in parts[
        "xl/externalLinks/_rels/externalLink1.xml.rels"]

    # 生成的文件必须能被 openpyxl 按 keep_links=True 正常打开
    wb = openpyxl.load_workbook(io.BytesIO(injected), data_only=False,
                                keep_links=True)
    assert len(wb._external_links) == 1
    link = wb._external_links[0]
    assert link.externalBook.sheetNames.sheetName == ["Sheet"]
    assert link.externalBook.sheetDataSet.sheetData[0].row[0].cell[0].v == "42"
    wb.close()
    assert external_link_map(parts) == {1: "财务报表-物流巴淡-202608.xls"}


def test_external_link_without_cache_is_still_valid():
    xml = build_external_link_xml(["Sheet"], {})
    assert b"<sheetDataSet>" not in xml
    injected = inject_external_link(
        _plain_workbook_bytes(), "target.XLS", ["Sheet"], {}, 1)
    wb = openpyxl.load_workbook(io.BytesIO(injected), keep_links=True)
    assert wb.active["A1"].value == "hello"
    wb.close()


def test_inject_external_link_rejects_index_mismatch():
    with pytest.raises(ProfitCenterError, match="外链索引不一致"):
        inject_external_link(_plain_workbook_bytes(), "target.XLS",
                             ["Sheet"], {}, 7)


def test_provenance_forces_text_data_type():
    rows = [{
        "利润中心": "=1+1", "工作表": "=cmd|' /C calc'!A0",
        "单元格": "I2", "类别": "测试", "写入值": "=HYPERLINK(...)",
        "来源": "模板",
    }]
    data = build_provenance_xlsx(rows)
    wb = openpyxl.load_workbook(io.BytesIO(data))
    ws = wb["取数明细"]
    assert ws["A1"].data_type == "s" and ws["A1"].value == "利润中心"
    for coord in ("A2", "B2", "E2"):
        assert ws[coord].data_type == "s", f"{coord} 被当成公式"
    assert ws["A2"].value == "=1+1"
    wb.close()


def test_build_zip_contains_only_formula_workbooks_and_reports():
    result = RegionResult(key="batam", label="物流巴淡", data=b"PK-fake")
    checks = [{"name": "净利润-2026", "scope": "当月", "sum": 1.0,
               "benchmark": 1.0, "delta": 0.0, "pass": True, "note": ""}]
    data = build_zip("2026-08", [result], checks, [], ["提示"])
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = set(archive.namelist())
        assert archive.read("物流巴淡-简表-2026-08.xlsx") == b"PK-fake"
        assert "0 处差异" in archive.read("复现比对.md").decode("utf-8")
        assert "只含公式版" in archive.read("交付说明.md").decode("utf-8")
        assert "提示" in archive.read("运行摘要.txt").decode("utf-8")
    assert names == {
        "物流巴淡-简表-2026-08.xlsx", "取数明细.xlsx", "校验报告.md",
        "复现比对.md", "交付说明.md", "运行摘要.txt",
    }


def test_verify_report_marks_explained_differences():
    checks = [
        {"name": "净利润-2026", "scope": "当月", "sum": 1.0,
         "benchmark": 1.0, "delta": 0.0, "pass": True, "note": ""},
        {"name": "毛利润", "scope": "当月", "sum": 3.0,
         "benchmark": 1.0, "delta": 2.0, "pass": True, "note": "口径差异不调平"},
    ]
    report = build_verify_report(checks, "2026-08")
    assert "| 净利润-2026 | 当月 |" in report
    assert "| 毛利润 | 当月 |" in report
    assert "口径差异不调平" in report
    assert "不调平" in report


def test_classify_warns_on_ignored_files():
    """识别不了的文件不再静默丢弃，warnings 里必须点名。"""
    files = _minimal_files()
    files += [
        ("财务报表-物流巴淡-202605.XLS", b"stale"),          # 月份不符
        ("财务报表-物流印尼202607.XLSX", b"stale-id"),        # 印尼月份不符
        ("随手记.xlsx", b"unknown"),                          # 完全不认识
        ("办事处简表-物流某地.xlsx", b"unknown-template"),     # 简表但无此地区
    ]
    inputs = classify(files, "2026-08")
    ignored = [w for w in inputs.warnings if "已忽略" in w]
    assert len(ignored) == 4
    assert any("202605" in w for w in ignored)
    assert any("202607" in w for w in ignored)
    assert any("随手记" in w for w in ignored)
    assert any("某地" in w for w in ignored)


def test_classify_warns_on_duplicates():
    files = _minimal_files()
    files.append(("财务报表-物流巴淡-202608.XLS", b"dup"))
    inputs = classify(files, "2026-08")
    assert inputs.reports[("batam", "202608")] == b"report"   # 保留第一份
    assert any("重复" in w and "巴淡" in w for w in inputs.warnings)


def test_validate_inputs_reports_missing_sheets(monkeypatch):
    """源报表 sheet 齐全时放行，印尼表缺「-考核」工作表要报错。"""
    from app.profit_center import engine as eng

    class _Book:
        @staticmethod
        def sheet_names():
            return ["利润表", "损益表", "管理费用表", "管理费用", "收入成本表"]

    monkeypatch.setattr(eng, "_xls_book", lambda data: _Book())
    inputs = eng.classify(_minimal_files(), "2026-08")
    # 印尼表是占位字节，不是合法 xlsx
    with pytest.raises(ProfitCenterError, match="无法读取"):
        eng.validate_inputs(inputs)
    # 换成缺「-考核」工作表的合法 xlsx
    wb = openpyxl.Workbook()
    wb.active.title = "Sheet1"
    buf = io.BytesIO()
    wb.save(buf)
    inputs.indonesia = buf.getvalue()
    with pytest.raises(ProfitCenterError, match="缺少工作表"):
        eng.validate_inputs(inputs)
    # 换成齐全的印尼表后整体验证通过
    inputs.indonesia = _indonesia_bytes()
    eng.validate_inputs(inputs)


def test_validate_inputs_flags_corrupt_report(monkeypatch):
    """源报表打不开时报错要指向具体文件名。"""
    from app.profit_center import engine as eng
    inputs = eng.classify(_minimal_files(), "2026-08")
    inputs.indonesia = _indonesia_bytes()
    with pytest.raises(ProfitCenterError, match="物流巴淡-202607"):
        eng.validate_inputs(inputs)
