"""公式数值缓存（recalc）单元测试。

背景：openpyxl 保存会清空全部公式缓存，WPS 在「外链不更新」时不重算，
财务因此看到整列空白。这里守住最小求值器与 <v> 写回的行为。
"""
from __future__ import annotations

import io
import re
import zipfile

import openpyxl
import pytest

from app.profit_center.recalc import (
    _Ctx, _eval, fill_formula_caches, parse_link_caches, sheet_parts,
)


def _ctx(links=None, formulas=None, constants=None, snapshot=None) -> _Ctx:
    return _Ctx(formulas or {}, constants or {}, snapshot or {}, links or {})


# ---------------------------------------------------------------------------
# 求值器
# ---------------------------------------------------------------------------


def test_arithmetic_and_cell_refs():
    ctx = _ctx(constants={("2026年", "A1"): 10, ("2026年", "A2"): 4})
    assert _eval("$A$1+A2*2", "2026年", ctx) == 18
    assert _eval("(A1-A2)/2", "2026年", ctx) == 3
    assert _eval("-A2", "2026年", ctx) == -4


def test_sum_range_and_round():
    cells = {("2026年", f"A{r}"): r for r in range(1, 6)}
    ctx = _ctx(constants=cells)
    assert _eval("SUM(A1:A5)", "2026年", ctx) == 15
    assert _eval("ROUND(SUM(A1:A5)/3,0)", "2026年", ctx) == 5


def test_external_refs_use_link_cache():
    ctx = _ctx(links={11: {"管理费用表": {"G5": 230_499_265.0}},
                      10: {"管理费用表": {"G5": 167_254_116.0}}})
    assert _eval("[11]管理费用表!$G$5-[10]管理费用表!$G$5", "25年管理费用", ctx) \
        == 63_245_149


def test_missing_external_cache_counts_as_zero():
    ctx = _ctx(links={})
    assert _eval("[11]管理费用表!$G$5", "2026年", ctx) == 0


def test_cross_sheet_reference():
    ctx = _ctx(constants={("25年管理费用", "K14"): 123.0})
    assert _eval("'25年管理费用'!K14+1", "2026年", ctx) == 124


def test_blank_cell_blocks_cache_instead_of_writing_wrong_value():
    """引用空白格时按 0 参与运算（Excel 语义），不得抛错。"""
    ctx = _ctx()
    assert _eval("A1+A2", "2026年", ctx) == 0


def test_unsupported_function_returns_no_value():
    ctx = _ctx(constants={("2026年", "A1"): 1})
    with pytest.raises(Exception):
        _eval("VLOOKUP(A1,A1:A2,1,0)", "2026年", ctx)


# ---------------------------------------------------------------------------
# <v> 写回
# ---------------------------------------------------------------------------


def _tiny_workbook() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "2026年"
    ws["A1"] = 10
    ws["A2"] = 4
    ws["A3"] = "=A1+A2"
    ws["A4"] = "=SUM(A1:A3)"
    other = wb.create_sheet("25年管理费用")
    other["K14"] = 77
    ws["A5"] = "='25年管理费用'!K14*2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_fill_writes_numeric_cache_for_every_formula_cell():
    data = _tiny_workbook()
    wb = openpyxl.load_workbook(io.BytesIO(data))
    snap = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    filled, count = fill_formula_caches(data, wb, snap)
    assert count == 3

    with zipfile.ZipFile(io.BytesIO(filled)) as z:
        xml = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
    for coord, expected in (("A3", 14), ("A4", 28), ("A5", 154)):
        m = re.search(rf'<c r="{coord}"[^>]*>.*?</c>', xml, re.S)
        assert m and f"<v>{expected}</v>" in m.group(0), coord
    assert 't="str"' not in xml

    check = openpyxl.load_workbook(io.BytesIO(filled), data_only=True)
    assert check["2026年"]["A5"].value == 154


def test_fill_keeps_untouched_cells_intact():
    data = _tiny_workbook()
    wb = openpyxl.load_workbook(io.BytesIO(data))
    snap = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    filled, _ = fill_formula_caches(data, wb, snap)
    plain = openpyxl.load_workbook(io.BytesIO(_tiny_workbook()))["2026年"]
    after = openpyxl.load_workbook(io.BytesIO(filled))["2026年"]
    for coord in ("A1", "A2"):
        assert after[coord].value == plain[coord].value


def test_parse_link_caches_reads_values(tmp_path):
    """外链部件的 <row><cell> 嵌套结构必须被正确读出。"""
    ext = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<externalLink xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<externalBook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
        ' r:id="rId1"><sheetNames><sheetName val="管理费用表"/></sheetNames>'
        '<sheetDataSet><sheetData sheetId="0"><row r="5">'
        '<cell r="G5"><v>230499265</v></cell></row></sheetData></sheetDataSet>'
        "</externalBook></externalLink>"
    ).encode("utf-8")
    parts = {"xl/externalLinks/externalLink11.xml": ext}
    assert parse_link_caches(parts) == {11: {"管理费用表": {"G5": 230499265.0}}}


def test_sheet_parts_maps_names_to_xml():
    data = _tiny_workbook()
    parts = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            parts[name] = z.read(name)
    mapping = sheet_parts(parts)
    assert mapping["2026年"].startswith("xl/worksheets/")
    assert mapping["25年管理费用"].startswith("xl/worksheets/")
    assert all(p in parts for p in mapping.values())
