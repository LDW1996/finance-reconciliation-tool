"""四地逐表规则配置。

这里只描述「怎么从上月公式推导出本月公式」与无法推导的逐表特例，
**不存放任何财务答案数值**（高管工资由使用方手工录入）。

所有月份相关量都由上报月份 m 参数化，因此工具兼容任意 2–12 月
（1 月的上月列在去年模板里，暂不支持自动平移）：

核心派生规则（engine 自动执行）：
    从上月列 / 上月行取公式，用 formula.ShiftRule 平移填入本月列；
    *_skip 留空；*_overrides 用显式公式覆盖；本月之后的 E 列残留自动清理。

坐标规律（四份模板 + 八份源报表逐格实测归纳）：
    简表月份列       m 月 -> get_column_letter(m + 1)   (8 月 = I, 7 月 = H)
    收入成本表列     每月 9 列，m-1 -> m 恒为 +9
    净利润差额链     [cur]!${L(m+2)}$60 - [prev]!${L(m+1)}$60
    2025 管理费用累计 [cur]!${L(2m+2)}$r - [prev]!${L(2m)}$r
    外包成本 Data    J{base + m}（East / West 逐表不同）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from openpyxl.utils import get_column_letter

INCOME_SHEET = "收入成本表"
EXEC_SALARY_LABEL = "外包服务成本：高管工资"

INCOME_COL_STEP = 9          # 收入成本表每月占 9 列
MGMT_ACCUM_STEP = 2          # 2025 累计列每月 +2（P->R）
PROFIT_CHAIN_STEP = 1        # 差额链列每月 +1（I->J）

_MONTH_RE = re.compile(r"(\d{4})-(\d{1,2})")


def main_sheet(year: int) -> str:
    """简表主工作表名（模板按年份命名，如「2026年」）。"""
    return f"{year}年"


def mgmt_sheet(year: int) -> str:
    """上一年管理费用工作表名（如「2025年管理费用」）。"""
    return f"{year - 1}年管理费用"


def parse_month(month: str) -> tuple[int, int, str, str]:
    m = _MONTH_RE.fullmatch((month or "").strip())
    if not m:
        raise ValueError("上报月份格式应为 YYYY-MM，例如 2026-08")
    year, mon = int(m.group(1)), int(m.group(2))
    if not 1 <= mon <= 12:
        raise ValueError("月份必须在 1–12 之间")
    prev_year, prev_mon = (year - 1, 12) if mon == 1 else (year, mon - 1)
    return year, mon, f"{year}{mon:02d}", f"{prev_year}{prev_mon:02d}"


def default_month() -> str:
    """默认上报月份 = 当前自然月。"""
    now = datetime.now()
    return f"{now.year}-{now.month:02d}"


def month_options(count: int = 24) -> list[str]:
    """可选月份列表：从当前月往前推 count 个自然月，排除不支持自动平移的 1 月。"""
    now = datetime.now()
    year, mon = now.year, now.month
    options: list[str] = []
    for _ in range(count):
        if mon != 1:
            options.append(f"{year}-{mon:02d}")
        mon -= 1
        if mon == 0:
            year, mon = year - 1, 12
    return options


def month_col(m: int) -> str:
    """简表里 m 月对应的列字母（1 月 = B）。"""
    return get_column_letter(m + 1)


def chain_col(m: int) -> str:
    """净利润差额链：m 月源报表列字母（8 月 = J）。"""
    return get_column_letter(m + 2)


def mgmt_accum_col(m: int) -> str:
    """上一年管理费用累计列（7 月 = P, 8 月 = R）。"""
    return get_column_letter(2 * m + 2)


@dataclass(frozen=True)
class RegionSpec:
    key: str
    label: str
    template: str                       # 上月已填好的简表模板
    report_stem: str                    # 源报表文件名前缀
    profit_sheet: str                   # 外链公式里的利润表 sheet 名
    mgmt_sheet: str                     # 外链公式里的管理费用 sheet 名
    # 2026 年上半块
    labour_total_row: int               # 人工费用合计行
    labour_sum_end: int                 # 本月 SUM 覆盖到的最后一行
    other_mgmt_row: int                 # 其他管理费用行
    grand_total_row: int                # 合计行
    exec_salary_row: int | None = None  # 高管工资行；None = 模板无此行
    detail_skip: frozenset[int] = frozenset()
    detail_overrides: dict[int, str] = field(default_factory=dict)
    future_total_until: int = 0         # 合计行补齐到几月（0 = 不补）
    # 利润简表
    pl_row: int = 0                     # 本月行（8 月行 = pl_row，任意月按 pl_row 定位）
    pl_skip: frozenset[str] = frozenset()
    pl_constants: dict[str, object] = field(default_factory=dict)
    pl_overrides: dict[str, str] = field(default_factory=dict)
    # 本月之后各行 E 列残留公式由 engine 按 (12 - mon) 动态清理
    # 上一年管理费用 sheet（行 2..N 为人工明细；高管工资行、SUM 行
    # 在旧模板里已覆盖全年，不动；其他管理费用、合计需平移补齐）
    fill_mgmt2025: bool = False
    mgmt2025_labour_row: int = 10
    mgmt2025_other_row: int = 13
    mgmt2025_total_row: int = 14
    mgmt2025_skip: frozenset[int] = frozenset()
    mgmt2025_overrides: dict[int, str] = field(default_factory=dict)
    mgmt2025_future_until: int = 0      # 合计行补齐到几月（0 = 不补）
    outsourcing_base: int | None = None    # Data!J{base + m}
    # 一次性历史列固化（财务为消除浮点小数做的清理，非月度平移逻辑）
    # (列位, 行)：列位 "prev" = 上月列（随上报月份变化），"cur" = 本月列。
    # 值一律从原始模板 data_only=True 的公式缓存读取，不硬编码答案。
    history_round: frozenset[tuple[str, int]] = frozenset()
    history_hard: frozenset[tuple[str, int]] = frozenset()
    # 财务手工版把部分历史格固化成整数（源公式缓存带 .25/.5 小数），
    # 按 Excel 显示口径截去小数；其余历史格保留缓存原值。
    # 这里只记 (列位, 行) + 取整规则，不存放任何答案数值。
    history_int: frozenset[tuple[str, int]] = frozenset()
    has_exec_salary: bool = False


REGIONS: tuple[RegionSpec, ...] = (
    RegionSpec(
        key="batam", label="物流巴淡",
        template="办事处简表-物流巴淡.xlsx",
        report_stem="财务报表-物流巴淡-",
        profit_sheet="利润表", mgmt_sheet="管理费用表",
        labour_total_row=13, labour_sum_end=12,
        other_mgmt_row=14, grand_total_row=15,
        exec_salary_row=12, detail_skip=frozenset({11}),
        future_total_until=12,
        pl_row=29,
        fill_mgmt2025=True,
        mgmt2025_skip=frozenset({10}),       # 上月 H10=0 是占位，本月不填
        mgmt2025_future_until=10,
        outsourcing_base=19,                 # West: J{19+m}（8 月 = J27）
        history_round=frozenset({("prev", 23), ("prev", 25), ("prev", 28)}),
        history_hard=frozenset({("cur", 23)}),
        history_int=frozenset({("prev", 22), ("prev", 24)}),
        has_exec_salary=True,
    ),
    RegionSpec(
        key="jakarta", label="物流雅加达",
        template="办事处简表-物流雅加达-更新.xlsx",
        report_stem="财务报表-物流雅加达-",
        profit_sheet="损益表", mgmt_sheet="管理费用表",
        labour_total_row=12, labour_sum_end=11,
        other_mgmt_row=13, grand_total_row=14,
        exec_salary_row=11, detail_skip=frozenset(),
        future_total_until=9,                # 只补下一个月（复现财务口径）
        pl_row=28,
        fill_mgmt2025=True,
        # 上月 H9 指向 $P$29/$N$29（模板残留），财务本月修正为 30 行
        mgmt2025_overrides={9: "=[{cur}]管理费用表!${acc_cur}$30-[{prev}]管理费用表!${acc_prev}$30"},
        mgmt2025_future_until=0,
        outsourcing_base=6,                  # East: J{6+m}（8 月 = J14）
        has_exec_salary=True,
    ),
    RegionSpec(
        key="semarang", label="物流三宝垄",
        template="办事处简表-物流三宝垄.xlsx",
        report_stem="财务报表-物流三宝垄-",
        profit_sheet="利润表", mgmt_sheet="管理费用",
        labour_total_row=12, labour_sum_end=11,
        other_mgmt_row=13, grand_total_row=14,
        exec_salary_row=None,
        detail_skip=frozenset({5, 6, 7, 11}),
        pl_row=28,
        pl_skip=frozenset({"I"}),            # 净利润差额链本月留空
        pl_constants={"C": 0, "F": "=0"},
        fill_mgmt2025=False,                 # 该 sheet 0 改动
        history_round=frozenset({("prev", r) for r in range(23, 28)}),
        history_hard=frozenset(),
        history_int=frozenset({("prev", 22)}),
        has_exec_salary=False,
    ),
    RegionSpec(
        key="palu", label="物流帕鲁",
        template="办事处简表-物流帕鲁.xlsx",
        report_stem="财务报表-物流帕鲁-",
        profit_sheet="利润表", mgmt_sheet="管理费用表",
        labour_total_row=11, labour_sum_end=10,
        other_mgmt_row=12, grand_total_row=13,
        exec_salary_row=None,
        detail_skip=frozenset({8}),
        # 上月 H9 指 $D$30、H10 是 =0；财务本月用 $E$30 与两项求和公式
        detail_overrides={
            9: "=[{cur}]管理费用表!$E$30",
            10: "=[{cur}]管理费用表!$D$11+[{cur}]管理费用表!$D$25",
        },
        pl_row=27,
        # C/F/I 模板已有且财务未改，不动；只补 B/H 与修 E
        pl_skip=frozenset({"C", "F", "I"}),
        fill_mgmt2025=False,
        history_round=frozenset({("prev", 21), ("prev", 22), ("prev", 23), ("prev", 24)}),
        history_hard=frozenset(),
        history_int=frozenset({("prev", 20), ("prev", 26)}),
        has_exec_salary=False,
    ),
)

REGION_BY_KEY = {r.key: r for r in REGIONS}


def config_payload(month: str | None = None) -> dict:
    """前端 /config 的说明（不接触任何真实财务数据），月份参数化。"""
    current = month or default_month()
    year, mon, period, prev_period = parse_month(current)
    return {
        "month": current,
        "months": month_options(),
        "regions": [
            {
                "key": r.key,
                "label": r.label,
                "has_exec_salary": r.has_exec_salary,
                "template": r.template,
                "report": f"{r.report_stem}{period}.XLS",
            }
            for r in REGIONS
        ],
        "required_files": [
            "四地 {上月YYYYMM} + {本月YYYYMM} 财务报表 (.XLS)",
            "财务报表-物流印尼{本月YYYYMM}.XLSX",
        ],
        "required_files_sample": f"样例：上报 {current} 时 → 四地 {prev_period} + {period} 财务报表（.XLS）、财务报表-物流印尼{period}.XLSX",
        "templates_note": "4 份办事处简表模板不随每月上传，请在「模板管理」一次性上传保存",
        "optional_reference": "可同时上传财务手工填好的版本，工具会做逐格复现比对",
        "output": "公式版结果包 ZIP（4 份 xlsx + 取数明细 + 双口径校验报告 + 复现比对报告）",
    }
