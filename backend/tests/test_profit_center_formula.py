"""P001 公式平移器单元测试。

重点守住一条回归：外部引用重建时必须保留 '!'，否则会生成
``[11]管理费用表$D$6`` 这种 Excel 无法解析、外链缓存也取不到的公式。
"""
from app.profit_center.formula import (
    ShiftRule, external_refs, is_formula, normalize_links, normalized_name,
    shift_col,
)


def test_shift_col():
    assert shift_col("H", 1) == "I"
    assert shift_col("BR", 9) == "CA"
    assert shift_col("BP", 9) == "BY"
    assert shift_col("P", 2) == "R"


def test_external_ref_keeps_bang_and_absolute_markers():
    rule = ShiftRule(col_shift=1, link_prev=9, link_cur=11)
    assert rule.apply("=[9]管理费用表!$D$6") == "=[11]管理费用表!$D$6"
    assert rule.apply("=[9]管理费用!$D$6") == "=[11]管理费用!$D$6"
    assert is_formula(rule.apply("=[9]管理费用表!$D$6"))


def test_upper_block_shift_matches_august_financial_version():
    assert ShiftRule(link_prev=12, link_cur=13).apply(
        "=[12]管理费用!$D$11+[12]管理费用!$D$25"
    ) == "=[13]管理费用!$D$11+[13]管理费用!$D$25"
    assert ShiftRule(link_prev=7, link_cur=8).apply(
        "=[7]管理费用表!$D$5-H11"
    ) == "=[8]管理费用表!$D$5-I11"


def test_income_column_shift_uses_detected_step():
    rule = ShiftRule(link_prev=9, link_cur=11, income_col_shift=9, col_shift=1,
                     income_sheet="收入成本表")
    assert rule.apply("=[9]收入成本表!$BR$8+H12") == "=[11]收入成本表!$CA$8+I12"
    assert rule.apply("=[9]收入成本表!$BP$8+H11") == "=[11]收入成本表!$BY$8+I11"


def test_profit_chain_and_legacy_link_shift():
    rule = ShiftRule(link_prev=9, link_cur=11, link_legacy=frozenset({10}),
                     profit_chain_col_shift=1, profit_sheets=("利润表",))
    assert rule.apply("=[9]利润表!$I$60-[10]利润表!$H$60") == (
        "=[11]利润表!$J$60-[9]利润表!$I$60"
    )
    # 当月净利润列 C 固定，不参与差额链右移
    assert rule.apply("=[9]利润表!$C$60") == "=[11]利润表!$C$60"


def test_mgmt_accum_shift_keeps_month_columns():
    rule = ShiftRule(link_prev=9, link_cur=11, link_legacy=frozenset({10}),
                     mgmt_accum_col_shift=2, mgmt_sheets=("管理费用表",))
    assert rule.apply("=[9]管理费用表!$P$6-[10]管理费用表!$N$6") == (
        "=[11]管理费用表!$R$6-[9]管理费用表!$P$6"
    )
    assert rule.apply("=[9]管理费用表!$D$6") == "=[11]管理费用表!$D$6"


def test_local_refs_sum_ranges_and_quoted_sheets():
    assert ShiftRule(col_shift=1).apply("=SUM(H2:H12)") == "=SUM(I2:I12)"
    assert ShiftRule(col_shift=1).apply("=H13+H14") == "=I13+I14"
    assert ShiftRule(col_shift=1).apply("='2025年管理费用'!H14") == (
        "='2025年管理费用'!I14"
    )
    assert ShiftRule(col_shift=1).apply("=[9]收入成本表!$BX$8+'2025年管理费用'!H11") == (
        "=[9]收入成本表!$BX$8+'2025年管理费用'!I11"
    )


def test_round_wrapping_is_not_corrupted():
    formula = "=ROUND([4]损益表!$C$60,0)"
    assert ShiftRule(profit_chain_col_shift=0, col_shift=0).apply(formula) == formula


def test_data_row_shift():
    assert ShiftRule(data_row_shift=1).apply("=[1]Data!$J$26") == "=[1]Data!$J$27"


def test_external_refs_extraction():
    assert external_refs("=[11]管理费用表!$D$6") == [(11, "管理费用表", "D6")]
    assert external_refs("=[9]利润表!$I$60-[10]利润表!$H$60") == [
        (9, "利润表", "I60"), (10, "利润表", "H60"),
    ]


def test_normalize_links_and_names():
    assert normalized_name(r"C:\Users\x\7月\财务报表-物流巴淡-202607.XLS") == (
        "财务报表-物流巴淡-202607.xls"
    )
    link_map = {11: "财务报表-物流雅加达-202608.xls",
                9: "财务报表-物流雅加达-202607.xls",
                12: "财务报表-物流雅加达-202607.xls"}
    generated = "=[11]损益表!$J$60-[9]损益表!$I$60"
    finance = "=[11]损益表!$J$60-[12]损益表!$I$60"
    assert normalize_links(generated, link_map) == normalize_links(finance, link_map)
