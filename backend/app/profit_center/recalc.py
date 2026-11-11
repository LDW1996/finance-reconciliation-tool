"""给结果文件里的公式格补写数值缓存（``<v>``）。

为什么必须做这件事
------------------
openpyxl 保存工作簿时会把**所有**公式格的缓存值清空，落盘成
``<c r="K14"><f>...</f><v /></c>``。Excel 打开这类文件会按
``fullCalcOnLoad`` 重算，但 WPS 表格在「外部链接不更新」的场景下并不重算，
于是财务看到的是整列空白 —— 2026-10 反馈的「帕鲁 9/10 月管理费用的数去
哪里了」「帕鲁 2026 毛利/管理费用/净利润是空的」都是这个原因，与公式本身
是否正确无关。

所以保存后必须自己算一遍、把结果写回 ``<v>``，让文件「不重算也能看」。
重算时用的外部值就是同一份文件里 externalLink 部件的缓存值 —— 所见即
所算，不会再出现「外链缓存与显示值不一致」。

公式语言很窄（四份简表只有 SUM / ROUND / 四则运算 / 单元格引用），
因此这里用最小递归下降求值器，不引入 formulas / pycel 这类重依赖。
"""
from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree as ET

from openpyxl.utils import column_index_from_string, get_column_letter

_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

_CELL_RE = re.compile(r'<c r="([A-Z]+\d+)"([^>]*?)(/>|>(.*?)</c>)', re.S)
_FORMULA_RE = re.compile(r"<f[^>]*>(.*?)</f>", re.S)
_VALUE_RE = re.compile(r"<v\b[^>]*>(.*?)</v>|<v\s*/>", re.S)

# [11]管理费用表!$G$5 / [1]Data!$J$27
_EXT_REF = re.compile(
    r"\[(\d+)\](?:'([^']+)'|([^!\s+\-*/(),]+))!\$?([A-Z]{1,3})\$?(\d+)"
)
# '25年管理费用'!K14 / 2026年!B5
_SHEET_REF = re.compile(
    r"(?:'([^']+)'|([A-Za-z0-9\u4e00-\u9fff_][\w\u4e00-\u9fff]*))!\$?([A-Z]{1,3})\$?(\d+)"
)
# $D$5 / K14
_LOCAL_REF = re.compile(r"\$?([A-Z]{1,3})\$?(\d+)")


class Unsupported(Exception):
    """公式超出最小求值器的能力范围（保留原缓存，不写错值）。"""


class _Ctx:
    def __init__(self, formulas, constants, snapshot, links):
        self.formulas: dict[tuple[str, str], str] = formulas
        self.constants: dict[tuple[str, str], object] = constants
        self.snapshot: dict[tuple[str, str], object] = snapshot
        self.links: dict[int, dict[str, dict[str, float]]] = links
        self.memo: dict[tuple[str, str], float | None] = {}
        self.busy: set[tuple[str, str]] = set()

    # -- 取值 -------------------------------------------------------------
    def cell(self, sheet: str, coord: str) -> float | None:
        key = (sheet, coord.upper())
        if key in self.memo:
            return self.memo[key]
        if key in self.busy:  # 循环引用：不猜，交给上层回退
            return None
        self.busy.add(key)
        try:
            value = self._resolve(key)
        finally:
            self.busy.discard(key)
        self.memo[key] = value
        return value

    def _resolve(self, key) -> float | None:
        sheet, coord = key
        formula = self.formulas.get(key)
        if formula is not None:
            try:
                return _eval(formula.lstrip("="), sheet, self)
            except (Unsupported, ValueError, ZeroDivisionError, RecursionError):
                return _num_or_none(self.snapshot.get(key))
        if key in self.constants:
            return _num_or_none(self.constants[key])
        if key in self.snapshot:
            return _num_or_none(self.snapshot[key])
        return 0.0  # 空白格按 Excel 语义参与运算即为 0

    def external(self, idx: int, sheet: str, coord: str) -> float:
        return float(self.links.get(idx, {}).get(sheet, {}).get(coord.upper(), 0.0) or 0.0)


def _num_or_none(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _eval(text: str, sheet: str, ctx: _Ctx) -> float:
    parser = _Parser(text, sheet, ctx)
    value = parser.expr()
    parser.skip()
    if parser.pos != len(parser.text):
        raise Unsupported(f"尾部残留: {parser.text[parser.pos:]!r}")
    if value is None:
        raise Unsupported("无值")
    return float(value)


class _Parser:
    def __init__(self, text: str, sheet: str, ctx: _Ctx):
        self.text = text.replace(" ", "")
        self.pos = 0
        self.sheet = sheet
        self.ctx = ctx

    def skip(self):
        while self.pos < len(self.text) and self.text[self.pos] == " ":
            self.pos += 1

    def peek(self) -> str:
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def expr(self):
        value = self.term()
        while self.peek() and self.peek() in "+-":
            op = self.text[self.pos]
            self.pos += 1
            right = self.term()
            if value is None or right is None:
                value = None
            elif op == "+":
                value += right
            else:
                value -= right
        return value

    def term(self):
        value = self.unary()
        while self.peek() and self.peek() in "*/":
            op = self.text[self.pos]
            self.pos += 1
            right = self.unary()
            if value is None or right is None:
                value = None
            elif op == "*":
                value *= right
            else:
                if right == 0:
                    raise ZeroDivisionError
                value /= right
        return value

    def unary(self):
        self.skip()
        if self.peek() and self.peek() in "+-":
            op = self.text[self.pos]
            self.pos += 1
            value = self.unary()
            if value is None:
                return None
            return -value if op == "-" else value
        return self.atom()

    def atom(self):
        self.skip()
        char = self.peek()
        if char == "(":
            self.pos += 1
            value = self.expr()
            if self.peek() != ")":
                raise Unsupported("括号不匹配")
            self.pos += 1
            return value
        if char.isdigit():
            m = re.match(r"\d+(?:\.\d+)?(?:[Ee][+-]?\d+)?", self.text[self.pos:])
            self.pos += m.end()
            return float(m.group(0))
        # 函数（支持嵌套，如 ROUND(SUM(A1:A5)/3,0)）
        name = re.match(r"([A-Z][A-Z0-9\.]*)\(", self.text[self.pos:])
        if name:
            self.pos += name.end()
            args_text, self.pos = _read_args(self.text, self.pos)
            return self.function(name.group(1).upper(), args_text)
        return self.reference()

    def function(self, name: str, args_text: str):
        if name == "SUM":
            total = 0.0
            for part in _split_args(args_text):
                if ":" in part:
                    total += self.range_sum(part)
                else:
                    value = _eval(part, self.sheet, self.ctx)
                    total += 0.0 if value is None else value
            return total
        if name == "ROUND":
            args = _split_args(args_text)
            if len(args) != 2:
                raise Unsupported("ROUND 参数个数")
            value = _eval(args[0], self.sheet, self.ctx)
            digits = _eval(args[1], self.sheet, self.ctx)
            if value is None or digits is None:
                return None
            return round(value, int(digits))
        raise Unsupported(f"不支持的函数 {name}")

    def range_sum(self, part: str) -> float:
        head, tail = part.split(":", 1)
        sheet = self.sheet
        head_sheet = _SHEET_REF.match(head) if "!" in head else None
        if head_sheet:
            sheet = head_sheet.group(1) or head_sheet.group(2)
            head = f"{head_sheet.group(3)}{head_sheet.group(4)}"
        hm = _LOCAL_REF.fullmatch(head.strip("$"))
        tm = _LOCAL_REF.fullmatch(tail.strip("$"))
        if not (hm and tm):
            raise Unsupported("区域引用")
        c1, r1 = column_index_from_string(hm.group(1)), int(hm.group(2))
        c2, r2 = column_index_from_string(tm.group(1)), int(tm.group(2))
        total = 0.0
        for row in range(min(r1, r2), max(r1, r2) + 1):
            for col in range(min(c1, c2), max(c1, c2) + 1):
                value = self.ctx.cell(sheet, f"{get_column_letter(col)}{row}")
                if value is not None:
                    total += value
        return total

    def reference(self):
        ext = _EXT_REF.match(self.text, self.pos)
        if ext:
            self.pos = ext.end()
            sheet = ext.group(2) or ext.group(3)
            return self.ctx.external(int(ext.group(1)), sheet,
                                     f"{ext.group(4)}{ext.group(5)}")
        ref = _SHEET_REF.match(self.text, self.pos)
        if ref:
            self.pos = ref.end()
            sheet = ref.group(1) or ref.group(2)
            return self.ctx.cell(sheet, f"{ref.group(3)}{ref.group(4)}")
        local = _LOCAL_REF.match(self.text, self.pos)
        if local:
            self.pos = local.end()
            return self.ctx.cell(self.sheet, f"{local.group(1)}{local.group(2)}")
        raise Unsupported(f"无法解析: {self.text[self.pos:self.pos + 20]!r}")


def _read_args(text: str, start: int) -> tuple[str, int]:
    """从函数名后的 '(' 之后读到配对的 ')'，返回 (参数串, 结束位置)。"""
    depth = 1
    pos = start
    while pos < len(text):
        char = text[pos]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[start:pos], pos + 1
        pos += 1
    raise Unsupported("函数括号不匹配")


def _split_args(text: str) -> list[str]:
    out, depth, buf = [], 0, []
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            out.append("".join(buf))
            buf = []
            continue
        buf.append(char)
    if buf:
        out.append("".join(buf))
    return [a for a in out if a]


# --------------------------------------------------------------------------
# 外链缓存解析 / 部件定位
# --------------------------------------------------------------------------


def parse_link_caches(parts: dict[str, bytes]) -> dict[int, dict[str, dict[str, float]]]:
    """把 externalLinkN.xml 的缓存读成 {索引: {sheet: {坐标: 值}}}。"""
    out: dict[int, dict[str, dict[str, float]]] = {}
    for name, blob in parts.items():
        m = re.fullmatch(r"xl/externalLinks/externalLink(\d+)\.xml", name)
        if not m:
            continue
        root = ET.fromstring(blob)
        names = [e.get("val") for e in root.findall(
            f".//{{{_MAIN_NS}}}sheetNames/{{{_MAIN_NS}}}sheetName")]
        cache: dict[str, dict[str, float]] = {}
        for sd in root.findall(f".//{{{_MAIN_NS}}}sheetData"):
            sid = int(sd.get("sheetId", "0"))
            sheet = names[sid] if sid < len(names) else "?"
            for cell in sd.findall(f".//{{{_MAIN_NS}}}cell"):
                text = (cell.findtext(f"{{{_MAIN_NS}}}v") or "").strip()
                try:
                    cache.setdefault(sheet, {})[cell.get("r")] = float(text)
                except ValueError:
                    cache.setdefault(sheet, {})[cell.get("r")] = 0.0
        out[int(m.group(1))] = cache
    return out


def sheet_parts(parts: dict[str, bytes]) -> dict[str, str]:
    """sheet 名 -> xl/worksheets/sheetN.xml。"""
    wb_xml = parts.get("xl/workbook.xml", b"").decode("utf-8")
    rels = parts.get("xl/_rels/workbook.xml.rels", b"").decode("utf-8")
    rid_target = dict(re.findall(
        r'Id="(rId\d+)"[^>]*Target="([^"]+)"', rels))
    rid_target.update({m.group(2): m.group(1) for m in re.finditer(
        r'Target="([^"]+)"[^>]*Id="(rId\d+)"', rels)})
    out = {}
    for name, rid in re.findall(r'<sheet name="([^"]+)"[^>]*r:id="(rId\d+)"', wb_xml):
        target = rid_target.get(rid)
        if not target:
            continue
        path = target.lstrip("/")
        if not path.startswith("xl/"):
            path = "xl/" + path
        out[name.replace("&amp;", "&")] = path
    return out


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------


def fill_formula_caches(data: bytes, wb, snap) -> tuple[bytes, int]:
    """给所有可求值的公式格写回 ``<v>``；返回 (新字节, 写入格数)。"""
    parts = {name: blob for name, blob in _iter_zip(data)}
    links = parse_link_caches(parts)
    mapping = sheet_parts(parts)

    formulas: dict[tuple[str, str], str] = {}
    constants: dict[tuple[str, str], object] = {}
    snapshot: dict[tuple[str, str], object] = {}
    writable: set[tuple[str, str]] = set()
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                value = cell.value
                key = (ws.title, cell.coordinate)
                if isinstance(value, str) and value.startswith("="):
                    formulas[key] = value
                    writable.add(key)
                elif value is not None:
                    constants[key] = value
    for ws in snap.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if cell.value is not None:
                    snapshot[(ws.title, cell.coordinate)] = cell.value

    ctx = _Ctx(formulas, constants, snapshot, links)
    values: dict[str, dict[str, float]] = {}
    for sheet, coord in writable:
        value = ctx.cell(sheet, coord)
        if value is None:
            continue
        values.setdefault(sheet, {})[coord] = value

    written = 0
    for sheet, cells in values.items():
        path = mapping.get(sheet)
        if not path or path not in parts:
            continue
        parts[path], count = _patch_sheet(parts[path], cells)
        written += count

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, blob in parts.items():
            zout.writestr(name, blob)
    return buf.getvalue(), written


def _iter_zip(data: bytes):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            yield name, z.read(name)


def _xml_number(value: float) -> str:
    if value != value or value in (float("inf"), float("-inf")):
        return "0"
    if float(value).is_integer():
        return str(int(value))
    return repr(float(value))


def _patch_sheet(blob: bytes, cells: dict[str, float]) -> tuple[bytes, int]:
    xml = blob.decode("utf-8")
    written = 0

    def repl(m: re.Match) -> str:
        nonlocal written
        coord, attrs, tail = m.group(1), m.group(2), m.group(3)
        if coord not in cells or tail == "/>":
            return m.group(0)
        body = m.group(4) or ""
        if not _FORMULA_RE.search(body):
            return m.group(0)
        value = _xml_number(cells[coord])
        # 去掉可能存在的错误类型标记（t="str" 会让数字显示成 #VALUE!）
        attrs = re.sub(r'\st="[^"]*"', "", attrs)
        body = _VALUE_RE.sub("", body).rstrip()
        written += 1
        return f'<c r="{coord}"{attrs}>{body}<v>{value}</v></c>'

    xml = _CELL_RE.sub(repl, xml)
    return xml.encode("utf-8"), written
