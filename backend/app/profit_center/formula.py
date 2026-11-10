"""公式平移引擎（P001）。

仓库原本没有任何公式平移能力（Translator / formula.translate / move_range
全库零命中），且 openpyxl 的 Translator 不认识 `[n]外部引用` 前缀。因此这里
实现基于 token 重写的平移器：把公式拆成「引用 token」逐个变换，其余字符
原样保留，避免破坏 ROUND / SUM / 引号 / 中文 sheet 名 / 绝对引用符号。

**平移源永远是上月列（7 月 = H），不是本月的残留公式。**

变换维度（全部可由调用方按目标格语义独立开关）：
    link_prev  -> link_cur          上月外链换本月
    link_legacy -> link_prev        历史月外链换上月（差额链减项）
    col_shift                      本表裸引用 / 本簿跨表引用的列偏移（H->I 为 +1）
    income_col_shift               [n]收入成本表 月份列偏移（每月 9 列）
    profit_chain_col_shift         [n]利润表/损益表 差额链列偏移（I->J 为 +1）
    mgmt_accum_col_shift           [n]管理费用表 2025 累计列偏移（P->R 为 +2）
    data_row_shift                 [n]Data 外包成本行偏移（J26->J27 为 +1）
    cross_row_shift                '2025年管理费用'! 引用改走行偏移（帕鲁 F 列）
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from openpyxl.utils import column_index_from_string, get_column_letter

# 外部引用：[9]管理费用表!$D$6 / [1]Data!$J$27 / [11]收入成本表!$CA$8
# 本地引用：'2025年管理费用'!H11 / $D$5 / SUM(H2:H12) 里的 H2 / H12
_REF_TOKEN = re.compile(
    r"(?P<ext>\[(?P<idx>\d+)\](?P<extsheet>'[^']+'|[^\s!$+\-*/(),]+)!)"
    r"(?P<ecol_abs>\$?)(?P<ecol>[A-Z]{1,3})(?P<erow_abs>\$?)(?P<erow>\d+)"
    r"|(?P<localsheet>(?:'[^']+'|[\w\u4e00-\u9fff]+)!)?"
    r"(?P<lcol_abs>\$?)(?P<lcol>[A-Z]{1,3})(?P<lrow_abs>\$?)(?P<lrow>\d+)"
)

_STRIP_QUOTES = re.compile(r"^'|'$")


def shift_col(letter: str, delta: int) -> str:
    idx = column_index_from_string(letter.upper()) + delta
    if idx < 1:
        raise ValueError(f"列平移越界: {letter}{delta:+d}")
    return get_column_letter(idx)


@dataclass(frozen=True)
class ShiftRule:
    """一次公式平移的全部开关。默认「只换外链索引 + 本表列 +1」。"""

    link_prev: int | None = None
    link_cur: int | None = None
    link_legacy: frozenset[int] = frozenset()
    col_shift: int = 1
    income_col_shift: int = 0
    profit_chain_col_shift: int = 0
    mgmt_accum_col_shift: int = 0
    data_row_shift: int = 0
    cross_row_shift: int = 0
    income_sheet: str = "收入成本表"
    profit_sheets: tuple[str, ...] = ("利润表", "损益表")
    mgmt_sheets: tuple[str, ...] = ("管理费用表", "管理费用")

    def apply(self, formula):
        if not is_formula(formula):
            return formula
        return "=" + _REF_TOKEN.sub(self._replace, formula[1:])

    def _replace(self, m: re.Match) -> str:
        if m.group("ext") is not None:
            return self._external(m)
        return self._local(m)

    def _external(self, m: re.Match) -> str:
        idx = int(m.group("idx"))
        sheet = _STRIP_QUOTES.sub("", m.group("extsheet").rstrip("!"))
        col_abs, col = m.group("ecol_abs"), m.group("ecol").upper()
        row_abs, row = m.group("erow_abs"), int(m.group("erow"))

        if self.link_prev is not None and idx == self.link_prev:
            idx = self.link_cur if self.link_cur is not None else idx
        elif idx in self.link_legacy and self.link_prev is not None:
            idx = self.link_prev

        if sheet == self.income_sheet:
            if self.income_col_shift:
                col = shift_col(col, self.income_col_shift)
        elif sheet == "Data":
            if self.data_row_shift:
                row += self.data_row_shift
        elif sheet in self.profit_sheets:
            # $C$60 是当月净利润，列固定；F/H/I/J 是历史累计差额链，按月右移
            if row == 60 and col != "C" and self.profit_chain_col_shift:
                col = shift_col(col, self.profit_chain_col_shift)
        elif sheet in self.mgmt_sheets:
            # 2025 累计列（G/J/L/N/P/R...）按月 +2；当月列 C/D/E 固定不动
            if self.mgmt_accum_col_shift and col not in ("C", "D", "E"):
                col = shift_col(col, self.mgmt_accum_col_shift)

        # extsheet 组不含结尾的 '!'（'!' 属于 ext 组），重建时必须补回，
        # 否则会生成 [11]管理费用表$D$6 这类非法引用。
        return f"[{idx}]{m.group('extsheet')}!{col_abs}{col}{row_abs}{row}"

    def _local(self, m: re.Match) -> str:
        sheet = m.group("localsheet") or ""
        col_abs, col = m.group("lcol_abs"), m.group("lcol").upper()
        row_abs, row = m.group("lrow_abs"), int(m.group("lrow"))
        if sheet:
            # 本簿跨表引用：帕鲁 F 列走「行 +1、列不动」，其余走列 +1
            if self.cross_row_shift:
                row += self.cross_row_shift
            else:
                col = shift_col(col, self.col_shift)
        elif self.col_shift:
            col = shift_col(col, self.col_shift)
        return f"{sheet}{col_abs}{col}{row_abs}{row}"


def normalize_links(formula, link_map: dict[int, str]) -> str:
    """把 [n] 归一成 [目标文件名]，消除复现比对里的冗余外链索引假差异。

    例：雅加达财务版另建 [12] 指向 202607，旧模板用 [9] 指向同一份文件，
    两者语义相同；巴淡财务版把 [9] 路径改成「7月/…」但文件仍是 202607。
    """
    if not formula:
        return formula

    def repl(m: re.Match) -> str:
        name = link_map.get(int(m.group(1)))
        return f"[{name}]" if name else m.group(0)

    return re.sub(r"\[(\d+)\]", repl, formula)


def normalized_name(target: str) -> str:
    """外链 target 归一成文件名主干，用于按文件名语义匹配外链索引。"""
    base = re.split(r"[\\/]", target or "")[-1]
    return base.strip().lower()


def external_refs(formula) -> list[tuple[int, str, str]]:
    """提取公式用到的 (外链索引, sheet 名, 单元格坐标)，用于生成外链缓存。"""
    if not is_formula(formula):
        return []
    out: list[tuple[int, str, str]] = []
    for m in _REF_TOKEN.finditer(formula[1:]):
        if m.group("ext") is None:
            continue
        sheet = _STRIP_QUOTES.sub("", m.group("extsheet").rstrip("!"))
        out.append((int(m.group("idx")), sheet, f"{m.group('ecol')}{m.group('erow')}"))
    return out


def is_formula(value) -> bool:
    return isinstance(value, str) and value.startswith("=")
