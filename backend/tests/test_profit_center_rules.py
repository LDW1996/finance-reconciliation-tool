"""规则表模式（RULES_MODE）的纯内存单元测试。

不依赖微信附件 / 真实财务文件：规则表静态体检 + 语义标记渲染 +
外链缓存补齐 + 标签定位。真实数据回归由 tmp/pc-verify-rules.py 与
test_profit_center_real_data.py（显式目录）覆盖。
"""
from __future__ import annotations

import io
import re

import openpyxl
import pytest

from app.profit_center.config import REGIONS
from app.profit_center.engine import (
    _label_column, _zip_parts, ensure_link_cells, inject_external_link,
    outbook_index, render_rule, rule_formula,
)
from app.profit_center.formula import ShiftRule
from app.profit_center.rules import BASE_MONTH, RULES


# ---------------------------------------------------------------------------
# 规则表静态体检
# ---------------------------------------------------------------------------


def test_rules_cover_all_regions_with_required_blocks():
    keys = {r.key for r in REGIONS}
    assert set(RULES) == keys, "规则表必须四地齐全"
    for r in REGIONS:
        entry = RULES[r.key]
        assert isinstance(entry["pl_row"], int) and entry["pl_row"] > 0
        assert entry["upper"], f"{r.key} 上半块规则不能为空"
        assert entry["pl"], f"{r.key} 利润简表规则不能为空"
        # 2025 管理费用块只应出现在需要回填的两地
        if r.fill_mgmt2025:
            assert entry["mgmt2025"], f"{r.key} 需要 2025 管理费用规则"
        else:
            assert not entry.get("mgmt2025"), f"{r.key} 不应携带 2025 管理费用规则"


def test_rules_respect_region_column_exclusions():
    """利润简表规则不得覆盖逐表跳过列（交给常量/特例/模板原值）。"""
    for r in REGIONS:
        pl_keys = set(RULES[r.key]["pl"])
        assert not (pl_keys & set(r.pl_skip)), (
            f"{r.key} pl 规则包含跳过列 {pl_keys & set(r.pl_skip)}"
        )
        # RULES 的 pl_row 是 BASE_MONTH（9 月）基准行；引擎实际定位仍走
        # config.pl_row + (mon - 8)，两者必须只差 BASE_MONTH-8 行。
        assert RULES[r.key]["pl_row"] == r.pl_row + (BASE_MONTH - 8), r.key


def test_rules_have_no_hardcoded_amounts():
    """公式里出现 6 位以上裸数字 = 把某月金额写死，规则表禁止。"""
    pattern = re.compile(r"(?<![\w$])(\d{6,})(?![\w$])")
    for region, blocks in RULES.items():
        for block, rules in blocks.items():
            if not isinstance(rules, dict):
                continue
            for key, formula in rules.items():
                if isinstance(formula, str):
                    hit = pattern.search(formula)
                    assert hit is None, (
                        f"{region}.{block}[{key}] 含硬编码金额 {hit.group(1)}: {formula}"
                    )


def test_rules_use_semantic_markers_only():
    """规则表只能用 [@MONTH]/[@PREVMONTH]/[@OUTBOOK]，不得写死外链索引。"""
    for region, blocks in RULES.items():
        for block, rules in blocks.items():
            if not isinstance(rules, dict):
                continue
            for key, formula in rules.items():
                if not isinstance(formula, str) or not formula.startswith("="):
                    continue
                for idx in re.findall(r"\[(\d+)\]", formula):
                    assert not idx.isdigit(), (
                        f"{region}.{block}[{key}] 写死外链索引 [{idx}]: {formula}"
                    )


def test_base_month_is_september():
    assert BASE_MONTH == 9


# ---------------------------------------------------------------------------
# 语义标记渲染
# ---------------------------------------------------------------------------


def test_render_rule_replaces_all_markers():
    out = render_rule(
        "=[@MONTH]管理费用表!$D$6+[@PREVMONTH]管理费用表!$R$5+[@OUTBOOK]Data!$J$15",
        cur_link=15, prev_link=14, out_link=3,
    )
    assert out == "=[15]管理费用表!$D$6+[14]管理费用表!$R$5+[3]Data!$J$15"


def test_render_rule_requires_outbook_link():
    out = render_rule("=[@OUTBOOK]Data!$J$15", cur_link=1, prev_link=2, out_link=None)
    assert out is None, "没有外包成本表外链时该规则应放弃（保留模板原值）"


def test_outbook_index_finds_external_link():
    assert outbook_index({1: "财务报表-物流巴淡-202609.xls", 2: "2025年外包服务成本.xlsx"}) == 2
    assert outbook_index({1: "财务报表-物流巴淡-202609.xls"}) is None


def test_rule_formula_unknown_entries_return_none():
    spec = next(r for r in REGIONS if r.key == "batam")
    assert rule_formula(spec, "upper", 2, 15, 14, None) == "=[15]管理费用表!$D$6"
    assert rule_formula(spec, "nope", 2, 15, 14, None) is None
    assert rule_formula(spec, "upper", 999, 15, 14, None) is None


# ---------------------------------------------------------------------------
# ShiftRule 在规则模式下的行为（link_prev=None -> 外链索引原样保留）
# ---------------------------------------------------------------------------


def test_shift_rule_keeps_injected_link_indices_in_rules_mode():
    rule = ShiftRule(col_shift=2, link_prev=None, link_cur=None, link_legacy=frozenset())
    out = rule.apply("=[15]管理费用表!$D$6+J12")
    assert out == "=[15]管理费用表!$D$6+L12", "外链索引是引擎注入的，平移不得改动"


def test_shift_rule_shifts_local_columns_by_delta():
    rule = ShiftRule(col_shift=3)
    assert rule.apply("=SUM(J2:J12)") == "=SUM(M2:M12)"
    with pytest.raises(ValueError):
        ShiftRule(col_shift=-2).apply("=A1")   # 左移越界必须报错


def test_mgmt2025_rules_shift_same_sheet_refs_but_not_rendered_columns():
    """2025 管理费用表：同表引用随月右移，[@MC]/[@PC] 渲染出的累计列不再平移。

    回归 2026-10：10 月合计行曾生成 ``=J12+J13``（取到 9 月列），应为 K 列。
    """
    spec = next(r for r in REGIONS if r.key == "batam")
    rule = ShiftRule(col_shift=1, mgmt_accum_col_shift=0, data_row_shift=1)
    common = dict(cur_link=15, prev_link=14, out_link=3,
                  mc="V", pc="U", shift=rule)

    assert rule_formula(spec, "mgmt2025", 14, **common) == "=K12+K13"
    assert rule_formula(spec, "mgmt2025", 12, **common) == "=SUM(K2:K11)"
    row13 = rule_formula(spec, "mgmt2025", 13, **common)
    assert row13.endswith("-K12+K11") and "J12" not in row13

    row2 = rule_formula(spec, "mgmt2025", 2, **common)
    assert row2 == "=[15]管理费用表!$V$6-[14]管理费用表!$U$6"
    # 外包成本行按行平移（Data!J28 -> J29），列不动
    assert rule_formula(spec, "mgmt2025", 11, **common) == "=[3]Data!$J$29"


# ---------------------------------------------------------------------------
# ensure_link_cells：外链缓存补齐
# ---------------------------------------------------------------------------


def _workbook_with_ext_formula(formula: str) -> bytes:
    wb = openpyxl.Workbook()
    wb.active["B1"] = formula
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_ensure_link_cells_fills_missing_cache():
    raw = _workbook_with_ext_formula("=[1]Sheet!$A$1")
    # 先注入外链部件：缓存只带 B2（模拟财务原件缓存残缺）
    data = inject_external_link(
        raw, "target.XLS", ["Sheet"], {"Sheet": {"B2": 7}}, 1,
    )
    wb = openpyxl.load_workbook(io.BytesIO(data), keep_links=True)
    fixed = ensure_link_cells(data, wb, template=_workbook_with_ext_formula("=1+1"))
    xml = _zip_parts(fixed)["xl/externalLinks/externalLink1.xml"].decode("utf-8")
    assert '<cell r="A1"><v>0</v></cell>' in xml, "缺失的 A1 缓存应补 0"
    assert '<cell r="B2"><v>7</v></cell>' in xml, "已有缓存不得丢失"
    wb.close()


def test_ensure_link_cells_noop_when_cache_complete():
    raw = _workbook_with_ext_formula("=[1]Sheet!$A$1")
    data = inject_external_link(
        raw, "target.XLS", ["Sheet"], {"Sheet": {"A1": 42}}, 1,
    )
    wb = openpyxl.load_workbook(io.BytesIO(data), keep_links=True)
    assert ensure_link_cells(data, wb, template=raw) == data
    wb.close()


# ---------------------------------------------------------------------------
# _label_column：「上年同期累计」列按标签定位
# ---------------------------------------------------------------------------


class _FakeSheet:
    def __init__(self, headers: dict[int, list[str]]):
        self.nrows, self.ncols = 6, 40
        self._headers = headers  # {row(0-based): [label per col]}

    def cell_value(self, row, col):
        return self._headers.get(row, [])[col] if col < len(
            self._headers.get(row, [])) else ""


class _FakeBook:
    def __init__(self, sheet_name, headers):
        self._sheet_name, self._headers = sheet_name, headers

    def sheet_by_name(self, name):
        if name != self._sheet_name:
            raise KeyError(name)
        return _FakeSheet(self._headers)


def test_label_column_finds_header_in_any_row():
    book = _FakeBook("利润表", {2: ["", "", "上年同期累计"]})
    assert _label_column(book, "利润表", "上年同期累计") == 2


def test_label_column_returns_none_when_missing_or_no_sheet():
    book = _FakeBook("利润表", {2: ["", "", "本年累计"]})
    assert _label_column(book, "利润表", "上年同期累计") is None
    assert _label_column(book, "不存在", "上年同期累计") is None
