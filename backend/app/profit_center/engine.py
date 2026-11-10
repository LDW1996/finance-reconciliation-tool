"""利润中心表格上报核心引擎。

流程（每个利润中心一份简表，兼容任意 2–12 月上报）：
    1. 旧模板 + 上月/本月源报表 -> 按 P001 把上月列平移出本月列
    2. 注入指向本月源文件的 externalLink 部件及其缓存值
    3. Python 侧独立累加，做当月 / 累计双口径对平校验
    4. 若上传了财务手工版，逐格复现比对（外链索引按文件名归一化）
    5. 打包公式版结果 ZIP

设计约束（见 docs/design/2026-11-10-利润中心表格上报.md）：
    * 平移源永远是上月列 / 上月行，绝不照抄财务答案
    * 外链按文件名语义匹配，绝不按索引序或本机路径读盘
    * openpyxl save 后 sheet 内公式缓存会丢，校验值一律 Python 侧独立算
    * 公式格直接赋值让 openpyxl 设 data_type='f'；外部文本强制 's'
"""
from __future__ import annotations

import io
import math
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePosixPath
from xml.sax.saxutils import escape as xml_escape

import openpyxl
import xlrd
from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.utils.cell import coordinate_from_string

from .config import (
    INCOME_COL_STEP, INCOME_SHEET, PROFIT_CHAIN_STEP,
    REGIONS, REGION_BY_KEY, RegionSpec,
    chain_col, main_sheet, mgmt_accum_col, mgmt_sheet, month_col,
)
from .config import parse_month as _config_parse_month
from .formula import (
    ShiftRule, external_refs, is_formula, normalize_links, normalized_name,
)

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
LINK_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.externalLink+xml"
)


class ProfitCenterError(Exception):
    """可直接回给前端 400 的业务错误。"""


# --------------------------------------------------------------------------
# 输入分类
# --------------------------------------------------------------------------


@dataclass
class Inputs:
    templates: dict[str, bytes] = field(default_factory=dict)
    references: dict[str, bytes] = field(default_factory=dict)
    reports: dict[tuple[str, str], bytes] = field(default_factory=dict)
    indonesia: bytes | None = None
    warnings: list[str] = field(default_factory=list)


def parse_month(month: str) -> tuple[int, int, str, str]:
    """错误统一为 ProfitCenterError 的月份解析。"""
    try:
        return _config_parse_month(month)
    except ValueError as exc:
        raise ProfitCenterError(str(exc)) from exc


def classify(files: list[tuple[str, bytes]], month: str,
             templates: dict[str, bytes] | None = None) -> Inputs:
    """按文件名语义归类，绝不依赖外链索引顺序或本机路径。

    templates：模板管理里保存的 4 份简表模板（key -> bytes）。
    传入时每月上传的文件只做数据源；模板同名文件视为误传，忽略并提示。
    不传时保持旧行为（模板可随批次上传），供引擎级调用与测试使用。
    """
    _, _, period, prev_period = parse_month(month)
    inputs = Inputs()
    inputs.templates.update(templates or {})
    template_names = {r.template: r.key for r in REGIONS}
    label_to_key = {r.label: r.key for r in REGIONS}

    for raw_name, data in files:
        name = PurePosixPath(str(raw_name).replace("\\", "/")).name
        lower = name.lower()
        if not data:
            continue

        if name in template_names:
            if template_names[name] in inputs.templates:
                inputs.warnings.append(
                    f"模板 {name} 已由模板管理统一维护，本次忽略"
                )
            else:
                inputs.templates[template_names[name]] = data
            continue

        if lower.startswith("办事处简表") and lower.endswith((".xlsx", ".xlsm")):
            matched = next((k for label, k in label_to_key.items() if label in name), None)
            if not matched:
                inputs.warnings.append(f"办事处简表未识别到地区，已忽略：{name}")
            elif matched in inputs.references:
                inputs.warnings.append(f"财务手工版重复上传，仅取第一份：{name}")
            else:
                inputs.references[matched] = data
            continue

        if "物流印尼" in name:
            if not lower.endswith((".xlsx", ".xlsm")):
                inputs.warnings.append(f"印尼汇总表需为 .xlsx 格式，已忽略：{name}")
            elif period not in name:
                inputs.warnings.append(
                    f"印尼汇总表文件名应包含 {period}（上报 {month} 当月），已忽略：{name}"
                )
            elif inputs.indonesia is not None:
                inputs.warnings.append(f"印尼汇总表重复上传，仅取第一份：{name}")
            else:
                inputs.indonesia = data
            continue

        matched_spec = next((s for s in REGIONS if s.report_stem in name), None)
        if matched_spec:
            token = next((t for t in (period, prev_period) if t in name), None)
            if token is None:
                inputs.warnings.append(
                    f"{matched_spec.label} 源报表文件名应包含 {prev_period} 或 {period}"
                    f"（上报 {month}），已忽略：{name}"
                )
            elif (matched_spec.key, token) in inputs.reports:
                inputs.warnings.append(f"源报表重复上传，仅取第一份：{name}")
            else:
                inputs.reports[(matched_spec.key, token)] = data
            continue

        if "外包服务成本" in name and lower.endswith(".xlsx"):
            inputs.warnings.append(
                f"外包成本表无需上传（模板外链缓存已含其数值），已忽略：{name}"
            )
            continue

        if lower.startswith("办事处简表"):
            inputs.warnings.append(f"办事处简表文件名异常，已忽略：{name}")
            continue

        inputs.warnings.append(f"无法识别的文件，已忽略：{name}")

    missing_templates = [r.label for r in REGIONS if r.key not in inputs.templates]
    if missing_templates:
        raise ProfitCenterError(
            "缺少简表模板：" + "、".join(missing_templates)
            + "。模板在「模板管理」一次上传即可，无需每月重复上传。"
        )
    missing_reports = [
        f"{r.label} {p}" for r in REGIONS for p in (prev_period, period)
        if (r.key, p) not in inputs.reports
    ]
    if missing_reports:
        raise ProfitCenterError("缺少源报表：" + "、".join(missing_reports))
    if inputs.indonesia is None:
        raise ProfitCenterError(f"缺少印尼汇总表 财务报表-物流印尼{period}.XLSX")
    if not inputs.references:
        inputs.warnings.append("未上传财务手工版，跳过逐格复现比对")
    else:
        inputs.warnings.append(
            f"已识别 {len(inputs.references)} 份财务手工版，作为复现比对基准"
        )
    return inputs


# --------------------------------------------------------------------------
# 源报表读取（xlrd / openpyxl）
# --------------------------------------------------------------------------


def _xls_book(data: bytes):
    try:
        return xlrd.open_workbook(file_contents=data)
    except Exception as exc:  # pragma: no cover - 真实损坏文件
        raise ProfitCenterError(f"源报表无法读取（需 BIFF8 .XLS）：{exc}") from exc


def xls_value(book, sheet_name: str, coord: str, default=None):
    try:
        sh = book.sheet_by_name(sheet_name)
    except Exception:
        return default
    col_letter, row = coordinate_from_string(coord)
    col = column_index_from_string(col_letter) - 1
    if row - 1 >= sh.nrows or col >= sh.ncols:
        return default
    value = sh.cell_value(row - 1, col)
    if isinstance(value, str) and not value.strip():
        return default
    return value


def _num(value, default=0.0) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return float(default)


def detect_income_shift(inputs: Inputs, spec: RegionSpec, mon: int,
                        period: str, prev_period: str) -> int:
    """从源报表第 4 行探测「本年 m 月实际」列，算出月份列偏移（通常 9）。"""
    try:
        cur = _xls_book(inputs.reports[(spec.key, period)])
        prev = _xls_book(inputs.reports[(spec.key, prev_period)])
        sh_cur, sh_prev = cur.sheet_by_name("收入成本表"), prev.sheet_by_name("收入成本表")

        def find(sh, label):
            for col in range(min(sh.ncols, 140)):
                if str(sh.cell_value(3, col)).strip() == label:
                    return col
            return None

        cur_col = find(sh_cur, f"本年{mon}月实际")
        prev_col = find(sh_prev, f"本年{mon - 1}月实际") if mon > 1 else None
        if cur_col is not None and prev_col is not None:
            return cur_col - prev_col
    except Exception:
        pass
    return INCOME_COL_STEP


def indonesia_values(data: bytes) -> dict[str, float]:
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    out: dict[str, float] = {}
    try:
        pl = wb["损益表-考核"] if "损益表-考核" in wb.sheetnames else None
        mg = wb["管理费用表-考核"] if "管理费用表-考核" in wb.sheetnames else None
        if pl is None or mg is None:
            raise ProfitCenterError("印尼汇总表缺少「损益表-考核」或「管理费用表-考核」")

        def grab(ws, coord, key):
            out[key] = _num(ws[coord].value)

        for coord, key in (
            ("C60", "net_cur_month"), ("D60", "net_cur_accum"),
            ("F60", "net_2025_accum"), ("C4", "revenue_month"),
            ("C20", "cost_month"), ("D4", "revenue_accum"),
            ("D20", "cost_accum"), ("C32", "outsourcing_month"),
            ("D32", "outsourcing_accum"),
        ):
            grab(pl, coord, key)
        grab(mg, "D5", "mgmt_month")
        grab(mg, "E5", "mgmt_accum")
        out["net_delta_accum"] = out["net_cur_accum"] - out["net_2025_accum"]
        out["gross_month"] = out["revenue_month"] - out["cost_month"]
        out["gross_accum"] = out["revenue_accum"] - out["cost_accum"]
    finally:
        wb.close()
    return out


# --------------------------------------------------------------------------
# 外链部件：解析 / 注入
# --------------------------------------------------------------------------


def validate_inputs(inputs: Inputs) -> None:
    """内容级预检：每份数据源能打开、必备工作表齐全，报错指向具体文件。

    与 classify（纯文件名语义）分离，保证 classify 可用占位字节测试。
    """
    for key, token in sorted(inputs.reports):
        spec = REGION_BY_KEY[key]
        label = f"{spec.report_stem}{token}.XLS"
        try:
            book = _xls_book(inputs.reports[(key, token)])
        except ProfitCenterError as exc:
            raise ProfitCenterError(f"{label} 无法读取（需 BIFF8 .XLS）：{exc}") from exc
        missing = [
            s for s in (spec.profit_sheet, spec.mgmt_sheet, INCOME_SHEET)
            if s not in book.sheet_names()
        ]
        if missing:
            raise ProfitCenterError(f"{label} 缺少工作表：{'、'.join(missing)}")
    try:
        wb = openpyxl.load_workbook(
            io.BytesIO(inputs.indonesia or b""), read_only=True, data_only=True
        )
    except Exception as exc:  # noqa: BLE001 - 统一转成业务错误
        raise ProfitCenterError(f"印尼汇总表无法读取（需 .xlsx）：{exc}") from exc
    try:
        missing = [
            s for s in ("损益表-考核", "管理费用表-考核") if s not in wb.sheetnames
        ]
    finally:
        wb.close()
    if missing:
        raise ProfitCenterError("印尼汇总表缺少工作表：" + "、".join(missing))


def _zip_parts(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as zin:
        return {info.filename: zin.read(info.filename) for info in zin.infolist()}


def external_link_map(parts: dict[str, bytes]) -> dict[int, str]:
    """公式里的 [n] -> 归一化外链文件名（按 workbook.xml externalReferences 顺序）。"""
    try:
        wb_xml = parts["xl/workbook.xml"].decode("utf-8")
        rels_xml = parts["xl/_rels/workbook.xml.rels"].decode("utf-8")
    except KeyError:
        return {}
    rid_target: dict[str, str] = {}
    # Relationship 属性顺序不保证，逐个 tag 解析 Id / Target，避免按序正则漏配
    for tag in re.findall(r"<Relationship\b[^>]*/>", rels_xml):
        rid = re.search(r'\bId="([^"]+)"', tag)
        target = re.search(r'\bTarget="([^"]+)"', tag)
        if rid and target:
            rid_target[rid.group(1)] = target.group(1)
    block = re.search(r"<externalReferences>(.*?)</externalReferences>", wb_xml, re.S)
    if not block:
        return {}
    out: dict[int, str] = {}
    for index, rid in enumerate(re.findall(r'<externalReference[^>]*r:id="([^"]+)"', block.group(1)), 1):
        target = rid_target.get(rid, "")
        part = target.lstrip("/")
        if not part.startswith("xl/"):
            part = "xl/" + part
        rel_part = part.replace("externalLinks/", "externalLinks/_rels/") + ".rels"
        rel_data = parts.get(rel_part)
        filename = target
        if rel_data:
            found = re.search(
                r'<Relationship\s[^>]*Target="([^"]+)"[^>]*TargetMode="External"',
                rel_data.decode("utf-8"),
            ) or re.search(r'<Relationship\s[^>]*Target="([^"]+)"', rel_data.decode("utf-8"))
            if found:
                filename = found.group(1)
        out[index] = normalized_name(filename)
    return out


def _link_indices(link_map: dict[int, str], prev_period: str,
                  period: str) -> tuple[int, frozenset[int]]:
    prev_link = next((i for i, name in link_map.items() if prev_period in name), None)
    if prev_link is None:
        raise ProfitCenterError(
            f"模板外链里找不到指向 {prev_period} 的报表，无法按 P001 平移"
        )
    legacy = frozenset(
        i for i, name in link_map.items()
        if re.search(r"20\d{2}(0[1-9]|1[0-2])", name)
        and prev_period not in name and period not in name
    )
    return prev_link, legacy


def _xml_number(value) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return "0"
        if value.is_integer():
            return str(int(value))
        return repr(value)
    return "0"


def build_external_link_xml(sheet_names: list[str],
                            cache: dict[str, dict[str, object]]) -> bytes:
    """构造 externalLink 部件，含被引用单元格的缓存值，避免打开时 #REF。"""
    names = "".join(
        f'<sheetName val="{xml_escape(str(n))}"/>' for n in sheet_names
    )
    datasets = []
    for sheet_id, sheet_name in enumerate(sheet_names):
        cells = cache.get(sheet_name) or {}
        if not cells:
            continue
        rows: dict[int, list[str]] = {}
        for coord, value in cells.items():
            col_letter, row = coordinate_from_string(coord)
            cell_xml = f'<cell r="{coord}"'
            if isinstance(value, str):
                cell_xml += f' t="str"><v>{xml_escape(value)}</v></cell>'
            else:
                cell_xml += f"><v>{_xml_number(value)}</v></cell>"
            rows.setdefault(row, []).append(cell_xml)
        row_xml = "".join(
            f'<row r="{r}">' + "".join(sorted(
                rows[r], key=lambda s: column_index_from_string(
                    re.search(r'r="([A-Z]+)', s).group(1)))) + "</row>"
            for r in sorted(rows)
        )
        datasets.append(f'<sheetData sheetId="{sheet_id}">{row_xml}</sheetData>')
    # sheetDataSet 是可选部件；没有缓存单元格时必须整个省略，
    # 否则 openpyxl 解析空的 <sheetDataSet/> 会抛 "Value must be a sequence"。
    dataset_xml = f"<sheetDataSet>{''.join(datasets)}</sheetDataSet>" if datasets else ""
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<externalLink xmlns="{MAIN_NS}">'
        f'<externalBook xmlns:r="{REL_NS}" r:id="rId1">'
        f"<sheetNames>{names}</sheetNames>"
        f"{dataset_xml}"
        "</externalBook></externalLink>"
    )
    return xml.encode("utf-8")


def inject_external_link(workbook_bytes: bytes, target_name: str,
                         sheet_names: list[str],
                         cache: dict[str, dict[str, object]],
                         expected_index: int) -> bytes:
    """在 openpyxl save 之后追加一个 externalLink 部件（纯 ZIP/XML 后处理）。"""
    parts = _zip_parts(workbook_bytes)

    existing = [
        int(m.group(1)) for name in parts
        if (m := re.fullmatch(r"xl/externalLinks/externalLink(\d+)\.xml", name))
    ]
    part_no = max(existing, default=0) + 1
    part_path = f"xl/externalLinks/externalLink{part_no}.xml"
    rel_path = f"xl/externalLinks/_rels/externalLink{part_no}.xml.rels"

    rels = parts.get("xl/_rels/workbook.xml.rels", b"").decode("utf-8")
    used_rids = [int(x) for x in re.findall(r'Id="rId(\d+)"', rels)]
    new_rid = f"rId{max(used_rids, default=0) + 1}"
    rels = rels.replace(
        "</Relationships>",
        f'<Relationship Id="{new_rid}" Type="{REL_NS}/externalLink" '
        f'Target="externalLinks/externalLink{part_no}.xml"/></Relationships>',
    )
    parts["xl/_rels/workbook.xml.rels"] = rels.encode("utf-8")

    wb_xml = parts.get("xl/workbook.xml", b"").decode("utf-8")
    ref_tag = f'<externalReference r:id="{new_rid}"/>'
    if "<externalReferences>" in wb_xml:
        wb_xml = wb_xml.replace("</externalReferences>", ref_tag + "</externalReferences>", 1)
        count = len(re.findall(r"<externalReference\b", wb_xml))
    else:
        anchor = "<calcPr"
        block = f"<externalReferences>{ref_tag}</externalReferences>"
        wb_xml = wb_xml.replace(anchor, block + anchor, 1) if anchor in wb_xml else \
            wb_xml.replace("</workbook>", block + "</workbook>", 1)
        count = 1
    if count != expected_index:
        raise ProfitCenterError(
            f"外链索引不一致：公式用 [{expected_index}]，部件登记后为 [{count}]"
        )
    # 强制打开时全量重算（openpyxl 通常已写，这里双保险）
    if "fullCalcOnLoad" not in wb_xml:
        wb_xml = re.sub(r"<calcPr\b", '<calcPr fullCalcOnLoad="1"', wb_xml, count=1)
    parts["xl/workbook.xml"] = wb_xml.encode("utf-8")

    ct = parts.get("[Content_Types].xml", b"").decode("utf-8")
    override = (f'<Override PartName="/{part_path}" ContentType="{LINK_CONTENT_TYPE}"/>')
    if f'PartName="/{part_path}"' not in ct:
        ct = ct.replace("</Types>", override + "</Types>", 1)
    parts["[Content_Types].xml"] = ct.encode("utf-8")

    parts[part_path] = build_external_link_xml(sheet_names, cache)
    parts[rel_path] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{PKG_REL_NS}">'
        f'<Relationship Id="rId1" Type="{REL_NS}/externalLinkPath" '
        f'Target="{xml_escape(target_name)}" TargetMode="External"/>'
        "</Relationships>"
    ).encode("utf-8")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in parts.items():
            zout.writestr(name, data)
    return buf.getvalue()


def collect_link_cache(wb, link_index: int, book) -> dict[str, dict[str, object]]:
    """扫描所有公式，取 [link_index] 引用的单元格在源报表里的值。"""
    cache: dict[str, dict[str, object]] = {}
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if not is_formula(cell.value):
                    continue
                for idx, sheet, coord in external_refs(cell.value):
                    if idx != link_index:
                        continue
                    value = xls_value(book, sheet, coord)
                    if value is not None:
                        cache.setdefault(sheet, {})[coord] = value
    return cache


# --------------------------------------------------------------------------
# 逐格平移填充
# --------------------------------------------------------------------------


@dataclass
class RegionResult:
    key: str
    label: str
    data: bytes
    provenance: list[dict] = field(default_factory=list)
    diffs: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    exec_salary: float | None = None


def _set_formula(ws, coord: str, value, provenance: list[dict],
                 spec: RegionSpec, category: str, source: str = ""):
    """统一写入口：公式格交给 openpyxl 自动设 data_type='f'。"""
    ws[coord] = value
    provenance.append({
        "利润中心": spec.label, "工作表": ws.title, "单元格": coord,
        "类别": category, "写入值": str(value) if value is not None else "",
        "来源": source,
    })


def _to_number_or_none(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def run_region(spec: RegionSpec, inputs: Inputs, year: int, mon: int,
               period: str, prev_period: str,
               exec_salary: float | None, target_name: str) -> RegionResult:
    template = inputs.templates[spec.key]
    wb = openpyxl.load_workbook(io.BytesIO(template), data_only=False, keep_links=True)
    snap = openpyxl.load_workbook(io.BytesIO(template), data_only=True)
    result = RegionResult(key=spec.key, label=spec.label, data=b"", exec_salary=exec_salary)
    prov = result.provenance

    sheet_name = main_sheet(year)
    if sheet_name not in wb.sheetnames:
        raise ProfitCenterError(
            f"模板缺少「{sheet_name}」工作表：请确认模板年份与上报月份 {year}-{mon:02d} 一致"
        )
    ws = wb[sheet_name]
    ssnap = snap[sheet_name]
    prev_col, cur_col = month_col(mon - 1) if mon > 1 else "A", month_col(mon)
    if mon == 1:
        raise ProfitCenterError(
            "1 月简表的上月（去年 12 月）列不在同年模板内，暂不支持自动平移；"
            "请改用 2–12 月上报"
        )

    parts = _zip_parts(template)
    link_map = external_link_map(parts)
    prev_link, legacy = _link_indices(link_map, prev_period, period)
    cur_link = len(link_map) + 1
    income_shift = detect_income_shift(inputs, spec, mon, period, prev_period)
    book = _xls_book(inputs.reports[(spec.key, period)])

    base = dict(
        link_prev=prev_link, link_cur=cur_link, link_legacy=legacy,
        income_sheet="收入成本表",
        profit_sheets=(spec.profit_sheet,),
        mgmt_sheets=(spec.mgmt_sheet,),
    )
    rule_upper = ShiftRule(col_shift=1, **base)
    rule_pl = ShiftRule(
        col_shift=1, income_col_shift=income_shift,
        profit_chain_col_shift=PROFIT_CHAIN_STEP, **base,
    )
    rule_mgmt = ShiftRule(
        col_shift=1, mgmt_accum_col_shift=2, data_row_shift=1, **base,
    )

    # ---- 1. 上半块：7 月列 -> 8 月列 -------------------------------------
    detail_rows = range(2, spec.labour_sum_end + 1)
    for row in detail_rows:
        coord = f"{cur_col}{row}"
        if row in spec.detail_skip:
            _set_formula(ws, coord, None, prov, spec, "本月留空（逐表特例）")
            continue
        if row == spec.exec_salary_row:
            if exec_salary is None:
                _set_formula(ws, coord, None, prov, spec, "高管工资（待手工录入）")
                result.warnings.append(f"{spec.label} 高管工资未录入，该格留空")
            else:
                _set_formula(ws, coord, exec_salary, prov, spec,
                             "高管工资（手工录入）", "财务人员录入")
            continue
        if row in spec.detail_overrides:
            formula = spec.detail_overrides[row].format(cur=cur_link)
            _set_formula(ws, coord, formula, prov, spec, "逐表特例公式")
            continue
        src = ws[f"{prev_col}{row}"].value
        if src is None:
            continue
        value = rule_upper.apply(src) if is_formula(src) else src
        _set_formula(ws, coord, value, prov, spec, "科目（P001 平移）",
                     f"{prev_col}{row}")

    # 人工合计 / 其他管理费用 / 合计：同样从 7 月列平移，SUM 范围自动修正
    for row in (spec.labour_total_row, spec.other_mgmt_row, spec.grand_total_row):
        src = ws[f"{prev_col}{row}"].value
        if src is None:
            continue
        value = rule_upper.apply(src) if is_formula(src) else src
        _set_formula(ws, f"{cur_col}{row}", value, prov, spec,
                     "合计行（P001 平移，SUM 范围自动修正）", f"{prev_col}{row}")

    # 补齐未来月份合计行（从本月合计公式向右平移，补到逐表约定的月份为止）
    grand_formula = ws[f"{cur_col}{spec.grand_total_row}"].value
    if is_formula(grand_formula):
        for m2 in range(mon + 1, min(spec.future_total_until, 12) + 1):
            col = month_col(m2)
            delta = column_index_from_string(col) - column_index_from_string(cur_col)
            _set_formula(ws, f"{col}{spec.grand_total_row}",
                         ShiftRule(col_shift=delta).apply(grand_formula),
                         prov, spec, "未来月份合计行补齐")

    # ---- 2. 利润简表本月行 ------------------------------------------------
    # 利润简表按「月」排成行（A 列写月份数字），config.pl_row 固定是 8 月行。
    # 上报月份变化时整块行号必须跟着平移，否则会覆盖 8 月历史行、
    # 清掉后续月份行的 E 列，却留空本月行。
    pl_cur = spec.pl_row + (mon - 8)
    prev_row = pl_cur - 1
    for col in ("B", "C", "E", "F", "H", "I"):
        coord = f"{col}{pl_cur}"
        if col in spec.pl_skip:
            continue
        if col in spec.pl_constants:
            _set_formula(ws, coord, spec.pl_constants[col], prov, spec,
                         "逐表常量口径")
            continue
        if col in spec.pl_overrides:
            _set_formula(ws, coord, spec.pl_overrides[col], prov, spec,
                         "逐表特例公式")
            continue
        src = ws[f"{col}{prev_row}"].value
        if src is None:
            continue
        value = rule_pl.apply(src) if is_formula(src) else src
        _set_formula(ws, coord, value, prov, spec, "利润简表（P001 平移）",
                     f"{col}{prev_row}")
    # D/G/J 增减列模板已正确，保留不动
    for col in ("D", "G", "J"):
        if ws[f"{col}{pl_cur}"].value is not None:
            prov.append({
                "利润中心": spec.label, "工作表": ws.title,
                "单元格": f"{col}{pl_cur}", "类别": "增减列（模板原样保留）",
                "写入值": str(ws[f"{col}{pl_cur}"].value), "来源": "模板",
            })

    # 清理本月之后各行 E 列残留错误公式（残留到年底，行数随上报月份变化）
    for ahead in range(1, 12 - mon + 1):
        coord = f"E{pl_cur + ahead}"
        if ws[coord].value is not None:
            prov.append({
                "利润中心": spec.label, "工作表": ws.title, "单元格": coord,
                "类别": "清理残留错误公式", "写入值": str(ws[coord].value),
                "来源": "模板",
            })
            _set_formula(ws, coord, None, prov, spec, "清理后留空")

    # ---- 3. 上一年管理费用 sheet（仅巴淡 / 雅加达） ---------------------
    mgmt_name = mgmt_sheet(year)
    if spec.fill_mgmt2025 and mgmt_name in wb.sheetnames:
        w2 = wb[mgmt_name]
        for row in range(2, spec.mgmt2025_labour_row + 1):
            coord = f"{cur_col}{row}"
            if row in spec.mgmt2025_skip:
                continue
            if row in spec.mgmt2025_overrides:
                formula = spec.mgmt2025_overrides[row].format(
                    cur=cur_link, prev=prev_link,
                    acc_cur=mgmt_accum_col(mon), acc_prev=mgmt_accum_col(mon - 1),
                )
                _set_formula(w2, coord, formula, prov, spec,
                             "2025 管理费用（逐表特例）")
                continue
            src = w2[f"{prev_col}{row}"].value
            if src is None:
                continue
            value = rule_mgmt.apply(src) if is_formula(src) else src
            _set_formula(w2, coord, value, prov, spec,
                         "2025 管理费用（P001 平移）", f"{prev_col}{row}")
        # 其他管理费用 / 合计行
        for row in sorted({spec.mgmt2025_other_row, spec.mgmt2025_total_row}):
            src = w2[f"{prev_col}{row}"].value
            if src is None:
                continue
            value = rule_mgmt.apply(src) if is_formula(src) else src
            _set_formula(w2, f"{cur_col}{row}", value, prov, spec,
                         "2025 管理费用合计行（P001 平移）", f"{prev_col}{row}")
        total_formula = w2[f"{cur_col}{spec.mgmt2025_total_row}"].value
        if is_formula(total_formula):
            for m2 in range(mon + 1, min(spec.mgmt2025_future_until, 12) + 1):
                col = month_col(m2)
                delta = column_index_from_string(col) - column_index_from_string(cur_col)
                _set_formula(w2, f"{col}{spec.mgmt2025_total_row}",
                             ShiftRule(col_shift=delta).apply(total_formula),
                             prov, spec, "未来月份合计行补齐")

    # ---- 4. 一次性历史列固化（值取自原始模板公式缓存，不硬编码答案） ----
    # ("prev", row) 落在上月列（随上报月份变化），("cur", row) 落在本月列。
    def history_col(role: str) -> str:
        return cur_col if role == "cur" else prev_col

    for role, row in sorted(spec.history_round):
        coord = f"{history_col(role)}{row}"
        value = ws[coord].value
        if is_formula(value) and not value.startswith("=ROUND("):
            _set_formula(ws, coord, f"=ROUND({value[1:]},0)", prov, spec,
                         "历史列消除浮点小数")
    for role, row in sorted(spec.history_hard | spec.history_int):
        coord = f"{history_col(role)}{row}"
        cached = ssnap[coord].value
        if cached is None:
            result.warnings.append(f"{spec.label} {coord} 模板无缓存值，未固化")
            continue
        category = "历史列固化（模板缓存）"
        if (role, row) in spec.history_int and isinstance(cached, (int, float)):
            # 截去小数但保留符号（int() 对负数同样向零取整，与财务口径一致）
            cached = int(cached)
            category = "历史列固化（截去显示小数）"
        _set_formula(ws, coord, cached, prov, spec, category,
                     f"data_only!{coord}")

    # ---- 5. 保存 + 注入外链部件 ------------------------------------------
    buf = io.BytesIO()
    wb.save(buf)
    cache = collect_link_cache(wb, cur_link, book)
    result.data = inject_external_link(
        buf.getvalue(), target_name, list(book.sheet_names()), cache, cur_link,
    )
    result.warnings.append(
        f"已注入 [{cur_link}] -> {target_name}（缓存 {sum(len(v) for v in cache.values())} 个单元格）"
    )
    snap.close()
    return result


# --------------------------------------------------------------------------
# 复现比对
# --------------------------------------------------------------------------


def _cell_text(value, link_map: dict[int, str]) -> str:
    if value is None:
        return ""
    if is_formula(value):
        return normalize_links(value, link_map)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def reproduce(generated: bytes, reference: bytes, spec: RegionSpec) -> list[dict]:
    """全表逐格比对；外链索引按文件名归一化，消除冗余外链的假差异。"""
    gen_parts, ref_parts = _zip_parts(generated), _zip_parts(reference)
    gen_map, ref_map = external_link_map(gen_parts), external_link_map(ref_parts)
    gwb = openpyxl.load_workbook(io.BytesIO(generated), data_only=False, keep_links=True)
    rwb = openpyxl.load_workbook(io.BytesIO(reference), data_only=False, keep_links=True)
    diffs: list[dict] = []
    for sheet in gwb.sheetnames:
        if sheet not in rwb.sheetnames:
            continue
        gws, rws = gwb[sheet], rwb[sheet]
        max_row = max(gws.max_row, rws.max_row)
        max_col = max(gws.max_column, rws.max_column)
        for row in range(1, max_row + 1):
            for col in range(1, max_col + 1):
                gc, rc = gws.cell(row=row, column=col), rws.cell(row=row, column=col)
                expected, actual = (_cell_text(rc.value, ref_map),
                                    _cell_text(gc.value, gen_map))
                if expected == actual:
                    continue
                diffs.append({
                    "region": spec.label, "sheet": sheet, "cell": gc.coordinate,
                    "expected": expected[:300], "actual": actual[:300],
                    "match": False,
                })
    gwb.close()
    rwb.close()
    return diffs


# --------------------------------------------------------------------------
# 双口径校验（Python 侧独立累加，不依赖 Excel 公式缓存）
# --------------------------------------------------------------------------


def verify(inputs: Inputs, period: str, prev_period: str,
           exec_salaries: dict[str, float]) -> list[dict]:
    mon = int(period[-2:])
    benchmark = indonesia_values(inputs.indonesia)
    totals: dict[str, float] = {k: 0.0 for k in (
        "net_month", "net_accum", "net_2025_accum", "net_2025_month",
        "gross_month", "gross_accum", "mgmt_month", "mgmt_accum",
    )}
    for spec in REGIONS:
        cur = _xls_book(inputs.reports[(spec.key, period)])
        prev = _xls_book(inputs.reports[(spec.key, prev_period)])
        totals["net_month"] += _num(xls_value(cur, spec.profit_sheet, "C60"))
        totals["net_accum"] += _num(xls_value(cur, spec.profit_sheet, "D60"))
        totals["net_2025_accum"] += _num(xls_value(cur, spec.profit_sheet, "J60"))
        totals["net_2025_month"] += (
            _num(xls_value(cur, spec.profit_sheet, "J60"))
            - _num(xls_value(prev, spec.profit_sheet, "I60"))
        )
        sh = cur.sheet_by_name("收入成本表")
        # 第 4 行标签定位月份列，避免各中心列位不同（巴淡 CA / 雅加达 BY）。
        # 当月只取「本年 m 月实际」这一列；把所有月份列相加得到的是全年累计，
        # 不能拿来对当月基准。
        month_label = f"本年{mon}月实际"
        gross_month = gross_accum = 0.0
        for col in range(min(sh.ncols, 140)):
            label = str(sh.cell_value(3, col)).strip()
            if label == month_label:
                gross_month += _num(sh.cell_value(7, col))
            elif label == "本年累计实际":
                gross_accum += _num(sh.cell_value(7, col))
        totals["gross_month"] += gross_month
        totals["gross_accum"] += gross_accum
        totals["mgmt_month"] += _num(xls_value(cur, spec.mgmt_sheet, "D5"))
        totals["mgmt_accum"] += _num(xls_value(cur, spec.mgmt_sheet, "E5"))

    exec_month = sum(
        v for k, v in exec_salaries.items() if v is not None
    ) or benchmark["outsourcing_month"]
    exec_accum = benchmark["outsourcing_accum"]

    # 简表把高管工资加回毛利润并计入管理费用；印尼考核表把它单列为营业成本
    gross_month = totals["gross_month"] + exec_month
    gross_accum = totals["gross_accum"] + exec_accum
    mgmt_month = totals["mgmt_month"] + exec_month
    mgmt_accum = totals["mgmt_accum"] + exec_accum

    def check(name, scope, value, benchmark, note="", kind="strict"):
        delta = value - benchmark
        if kind == "explain":
            passed = abs(delta - (exec_month if scope == "当月" else exec_accum)) < 1e-6
        elif kind == "info":
            # 仅列示，不参与通过判定；差额仍如实保留，避免报告里出现假的 0
            passed = True
        else:
            passed = abs(delta) < 1e-6
        return {
            "name": name, "scope": scope, "sum": round(value, 2),
            "benchmark": round(benchmark, 2), "delta": round(delta, 2),
            "pass": passed, "note": note,
        }

    b = benchmark
    return [
        check("净利润-2026", "当月", totals["net_month"], b["net_cur_month"]),
        check("净利润-2026", "全年累计", totals["net_accum"], b["net_cur_accum"]),
        check("净利润-2025", "全年累计", totals["net_2025_accum"], b["net_2025_accum"]),
        check("净利润-增减", "全年累计",
              totals["net_accum"] - totals["net_2025_accum"], b["net_delta_accum"]),
        check("净利润-2025", "当月", totals["net_2025_month"], 0,
              "印尼表无上年同期当月基准，仅列示", kind="info"),
        check("毛利润", "当月", gross_month, b["gross_month"],
              f"差额 = 四地高管工资合计，等于印尼 C32（{b['outsourcing_month']:,.0f}）；口径差异不调平",
              kind="explain"),
        check("毛利润", "全年累计", gross_accum, b["gross_accum"],
              f"差额 = 印尼 D32（{b['outsourcing_accum']:,.0f}）；口径差异不调平",
              kind="explain"),
        check("管理费用", "当月", mgmt_month, b["mgmt_month"],
              "差额 = 四地高管工资合计；口径差异不调平", kind="explain"),
        check("管理费用", "全年累计", mgmt_accum, b["mgmt_accum"],
              "差额 = 印尼 D32；口径差异不调平", kind="explain"),
    ]


# --------------------------------------------------------------------------
# 报告 / 结果包
# --------------------------------------------------------------------------


def _safe_text(ws, coord: str, value):
    """明细表里的外部文本一律强制字符串，杜绝公式注入。"""
    cell = ws[coord]
    cell.value = "" if value is None else str(value)
    cell.data_type = "s"


def build_provenance_xlsx(rows: list[dict]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "取数明细"
    headers = ["利润中心", "工作表", "单元格", "类别", "写入值", "来源"]
    for col, head in enumerate(headers, 1):
        _safe_text(ws, f"{get_column_letter(col)}1", head)
    for r, row in enumerate(rows, 2):
        for c, head in enumerate(headers, 1):
            _safe_text(ws, f"{get_column_letter(c)}{r}", row.get(head, ""))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_verify_report(checks: list[dict], month: str) -> str:
    lines = [
        f"# 利润中心双口径校验报告 · {month}", "",
        "校验基准：印尼 `财务报表-物流印尼202608.XLSX` 的「-考核」表。",
        "所有数值均由 Python 侧从源报表独立累加，不依赖 Excel 公式缓存。", "",
        "| 指标 | 口径 | 四地合计 | 印尼基准 | 差额 | 判定 | 说明 |",
        "|---|---|---:|---:|---:|:---:|---|",
    ]
    for c in checks:
        lines.append(
            f"| {c['name']} | {c['scope']} | {c['sum']:,.2f} | "
            f"{c['benchmark']:,.2f} | {c['delta']:,.2f} | "
            f"{'通过' if c['pass'] else '未通过'} | {c['note']} |"
        )
    lines += [
        "", "## 判定口径", "",
        "- 净利润三项 + 增减：必须严格对平（差额 0）。",
        "- 毛利润 / 管理费用：四地简表把高管工资加回毛利润并计入管理费用，"
        "印尼考核表把它单列为营业成本，差额等于高管工资合计（当月 C32 / 累计 D32），"
        "属口径差异，逐项解释、**不调平**；一加一减抵消，因此净利润仍精确对平。",
        "- 上年同期当月：印尼表无对应基准，仅列示数值，不参与通过判定。",
    ]
    return "\n".join(lines) + "\n"


def build_diff_report(diffs: list[dict], month: str, regions: int) -> str:
    lines = [
        f"# 阶段 A 独立复现逐格比对 · {month}", "",
        f"比对方式：工具按 P001 从旧模板 7 月列独立推导，与财务手工版全表逐格比较；",
        "公式里的外链索引按目标文件名归一化，消除冗余外链造成的假差异。", "",
    ]
    if not diffs:
        lines += ["**结果：全表逐格一致，0 处差异。**", ""]
    else:
        by_region: dict[str, list[dict]] = {}
        for d in diffs:
            by_region.setdefault(d["region"], []).append(d)
        lines += [f"**结果：{len(diffs)} 处差异（覆盖 {len(by_region)}/{regions} 个利润中心）。**", "",
                  "| 利润中心 | 工作表 | 单元格 | 财务手工版 | 工具复现 |",
                  "|---|---|---|---|---|"]
        for d in diffs[:400]:
            lines.append(
                f"| {d['region']} | {d['sheet']} | {d['cell']} | "
                f"`{d['expected']}` | `{d['actual']}` |"
            )
        if len(diffs) > 400:
            lines.append(f"| … | … | … | 其余 {len(diffs) - 400} 处见结果包 JSON | |")
        lines += ["", "## 差异归因", "",
                  "差异主要来自两类：① 财务手工版为消除浮点小数对历史列做的 ROUND / 固化；",
                  "② 财务手工版新增的重复 202607 外链索引（已归一化，若仍有差异需逐格核对）。",
                  "所有差异均如实列出，不做静默修正。"]
    return "\n".join(lines) + "\n"


DELIVERY_NOTE = """# 交付说明

1. 本结果包**只含公式版**简表（4 份 .xlsx），不含静态数值版。
2. 请用 **Excel / WPS** 打开并**允许自动重算**；若询问是否更新外部链接，
   请选择「**不更新**」——新月份源报表的缓存值已内嵌在文件里，外部路径在本机不存在。
3. 「外包服务成本：高管工资」由财务手工查账录入；留空时该格为空，
   下游毛利润加回 / 人工合计 / 其他管理费用保留公式，录入后打开即自动重算。
4. 校验数值一律由工具在 Python 侧独立累加，见 `校验报告.md`；
   openpyxl 保存后 sheet 内公式缓存会被清空，请勿用 `data_only=True` 读取本结果包。
5. 上月及更早的历史列沿用模板原有公式，其跨簿缓存值保留在 `xl/externalLinks/` 中。
"""


def build_zip(month: str, results: list[RegionResult], checks: list[dict],
              diffs: list[dict], warnings: list[str]) -> bytes:
    buf = io.BytesIO()
    prov = [row for r in results for row in r.provenance]
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for r in results:
            zout.writestr(f"{r.label}-简表-{month}.xlsx", r.data)
        zout.writestr("取数明细.xlsx", build_provenance_xlsx(prov))
        zout.writestr("校验报告.md", build_verify_report(checks, month))
        zout.writestr("复现比对.md", build_diff_report(diffs, month, len(results)))
        zout.writestr("交付说明.md", DELIVERY_NOTE)
        meta = [
            f"生成时间: {datetime.now().isoformat(timespec='seconds')}",
            f"月份: {month}",
            f"校验项: {len(checks)}，通过: {sum(1 for c in checks if c['pass'])}",
            f"逐格差异: {len(diffs)}",
            "提示:", *[f"- {w}" for w in warnings],
        ]
        zout.writestr("运行摘要.txt", "\n".join(meta) + "\n")
    return buf.getvalue()


def run_engine(files: list[tuple[str, bytes]], month: str,
               exec_salaries: dict[str, float],
               templates: dict[str, bytes] | None = None) -> dict:
    """主入口：返回前端契约所需字段 + 结果包字节。"""
    year, mon, period, prev_period = parse_month(month)
    inputs = classify(files, month, templates)
    validate_inputs(inputs)
    salaries = {k: _to_number_or_none(v) for k, v in (exec_salaries or {}).items()}
    warnings = list(inputs.warnings)

    results: list[RegionResult] = []
    for spec in REGIONS:
        target_name = f"{spec.report_stem}{period}.XLS"
        results.append(run_region(
            spec, inputs, year, mon, period, prev_period,
            salaries.get(spec.key), target_name,
        ))

    checks = verify(inputs, period, prev_period, salaries)
    diffs: list[dict] = []
    for r in results:
        reference = inputs.references.get(r.key)
        if reference:
            spec = next(s for s in REGIONS if s.key == r.key)
            found = reproduce(r.data, reference, spec)
            diffs.extend(found)
            warnings.append(f"{r.label} 逐格比对：{len(found)} 处差异")
        warnings.extend(r.warnings)

    zip_bytes = build_zip(month, results, checks, diffs, warnings)
    return {
        "checks": checks,
        "diffs": diffs,
        "warnings": warnings,
        "download_name": f"利润中心上报-{month}.zip",
        "zip_bytes": zip_bytes,
    }
