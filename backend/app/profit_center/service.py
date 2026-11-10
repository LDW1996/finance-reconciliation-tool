"""利润中心上报的任务编排层。

职责：
    1. 模板管理：4 份简表模板持久化保存，一次上传、每月复用；
    2. 调用 engine 生成公式版结果包；
    3. 把结果包暂存在进程内 job store（不落库、不进仓库）供一次性下载。

job store 只存活于当前进程：进程重启即清空，另有 TTL / 容量上限兜底，
避免长时间运行后结果包一直占着内存。模板则落盘保存，跨进程可用。
"""
from __future__ import annotations

import io
import json
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

import openpyxl

from .config import REGIONS, REGION_BY_KEY, config_payload, default_month
from .engine import ProfitCenterError, run_engine

__all__ = [
    "ProfitCenterError", "config_payload", "get_job", "run_job",
    "parse_exec_salaries", "list_templates", "save_templates",
    "delete_template", "load_templates",
]

JOB_TTL_SECONDS = 30 * 60
MAX_JOBS = 12
MAX_TOTAL_BYTES = 200 * 1024 * 1024
MAX_FILES = 40
MAX_TEMPLATE_BYTES = 20 * 1024 * 1024


# --------------------------------------------------------------------------
# 模板管理（持久化：模板一次上传，每月 /run 只收数据源文件）
# --------------------------------------------------------------------------

def _template_dir() -> Path:
    override = os.environ.get("FINANCE_PC_TEMPLATE_DIR")
    if override:
        base = Path(override)
    elif getattr(sys, "frozen", False):           # PyInstaller EXE：程序旁 data/
        base = Path(sys.executable).resolve().parent / "data"
    else:                                          # 开发环境：仓库根 data/
        base = Path(__file__).resolve().parents[3] / "data"
    directory = base / "profit-center-templates"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _template_path(key: str) -> Path:
    return _template_dir() / f"{key}.xlsx"


def _meta_path(key: str) -> Path:
    return _template_dir() / f"{key}.json"


def _read_meta(key: str) -> dict:
    path = _meta_path(key)
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def list_templates() -> list[dict]:
    """模板管理页签的 4 个槽位状态。"""
    out: list[dict] = []
    for spec in REGIONS:
        path = _template_path(spec.key)
        meta = _read_meta(spec.key)
        uploaded = path.exists()
        out.append({
            "key": spec.key,
            "label": spec.label,
            "template": spec.template,          # 期望的标准模板文件名
            "uploaded": uploaded,
            "stored_name": meta.get("stored_name") if uploaded else None,
            "size": path.stat().st_size if uploaded else 0,
            "uploaded_at": meta.get("uploaded_at") if uploaded else None,
        })
    return out


def load_templates() -> dict[str, bytes]:
    """读取已保存的模板（key -> bytes），供 run_engine 使用。"""
    out: dict[str, bytes] = {}
    for spec in REGIONS:
        path = _template_path(spec.key)
        if path.exists():
            out[spec.key] = path.read_bytes()
    return out


def _match_template(name: str) -> str | None:
    """按文件名识别上传文件对应哪个利润中心的模板。"""
    for spec in REGIONS:
        if name == spec.template:
            return spec.key
    lower = name.lower()
    if lower.startswith("办事处简表") and lower.endswith((".xlsx", ".xlsm")):
        for spec in REGIONS:
            if spec.label in name:
                return spec.key
    return None


def save_templates(files: list[tuple[str, bytes]]) -> dict:
    """保存模板文件；全部识别通过才落盘（要么全成功，要么不动）。"""
    if not files:
        raise ProfitCenterError("请选择要上传的模板文件")
    matched: dict[str, tuple[str, bytes]] = {}
    unmatched: list[str] = []
    for raw_name, data in files:
        name = PurePosixPath(str(raw_name).replace("\\", "/")).name
        if not data:
            continue
        key = _match_template(name)
        if not key or key in matched:
            unmatched.append(name)
            continue
        matched[key] = (name, data)
    if unmatched:
        raise ProfitCenterError(
            "无法识别为四地简表模板：" + "、".join(unmatched)
            + "。文件名需与模板一致或形如「办事处简表-物流xx.xlsx」。"
        )
    if not matched:
        raise ProfitCenterError("请选择要上传的模板文件")
    for key, (name, data) in matched.items():
        if len(data) > MAX_TEMPLATE_BYTES:
            raise ProfitCenterError(f"{name} 超过 20 MB，模板文件异常")
        try:
            openpyxl.load_workbook(io.BytesIO(data), read_only=True).close()
        except Exception as exc:  # noqa: BLE001 - 统一转成业务错误
            raise ProfitCenterError(f"{name} 不是有效的 .xlsx 文件") from exc
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for key, (name, data) in matched.items():
        _template_path(key).write_bytes(data)
        _meta_path(key).write_text(
            json.dumps({"stored_name": name, "uploaded_at": stamp},
                      ensure_ascii=False),
            encoding="utf-8",
        )
    return {"templates": list_templates()}


def delete_template(key: str) -> dict:
    spec = REGION_BY_KEY.get(key)
    if not spec:
        raise ProfitCenterError("未知的利润中心")
    for path in (_template_path(key), _meta_path(key)):
        if path.exists():
            path.unlink()
    return {"templates": list_templates()}


@dataclass
class _Job:
    download_name: str
    zip_bytes: bytes
    created_at: float


_lock = threading.Lock()
_jobs: dict[str, _Job] = {}


def _prune_locked(now: float) -> None:
    expired = [key for key, job in _jobs.items()
               if now - job.created_at > JOB_TTL_SECONDS]
    for key in expired:
        _jobs.pop(key, None)
    while len(_jobs) > MAX_JOBS or sum(
        len(job.zip_bytes) for job in _jobs.values()
    ) > MAX_TOTAL_BYTES:
        oldest = min(_jobs, key=lambda key: _jobs[key].created_at)
        _jobs.pop(oldest, None)


def parse_exec_salaries(raw: str | None) -> dict[str, float]:
    """前端把高管工资字典序列化成 JSON 字符串放在 form 里。"""
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ProfitCenterError("高管工资参数不是合法 JSON") from exc
    if not isinstance(value, dict):
        raise ProfitCenterError("高管工资参数应为 {\"地区 key\": 金额} 对象")
    out: dict[str, float] = {}
    for key, amount in value.items():
        if amount is None or amount == "":
            continue
        try:
            number = float(str(amount).replace(",", "").strip())
        except (TypeError, ValueError) as exc:
            raise ProfitCenterError(f"高管工资「{key}」不是数字：{amount}") from exc
        if number < 0:
            raise ProfitCenterError(f"高管工资「{key}」不能为负数")
        out[str(key)] = number
    return out


def run_job(files: list[tuple[str, bytes]], month: str | None,
            exec_salaries: dict[str, float] | None = None) -> dict:
    """同步执行引擎（route 通过 run_in_threadpool 调用），返回前端契约字段。

    注意：结果包本身不通过 JSON 返回，只返回 job_id 供 /download 取回。
    简表模板来自模板管理（持久化），不随本批文件上传。
    """
    if not files:
        raise ProfitCenterError("请至少上传一组源报表与印尼汇总表")
    if len(files) > MAX_FILES:
        raise ProfitCenterError(f"单次最多上传 {MAX_FILES} 个文件")

    total = sum(len(data) for _, data in files)
    if total > MAX_TOTAL_BYTES:
        raise ProfitCenterError("本批文件合计超过 200 MB，请拆分后重试")

    payload = run_engine(files, month or default_month(),
                         exec_salaries or {}, load_templates())
    zip_bytes = payload.pop("zip_bytes")
    job_id = uuid.uuid4().hex
    with _lock:
        _jobs[job_id] = _Job(
            download_name=payload["download_name"],
            zip_bytes=zip_bytes,
            created_at=time.monotonic(),
        )
        _prune_locked(time.monotonic())
        # 刚创建的结果不允许因容量清理被自己挤掉
        if job_id not in _jobs:
            raise ProfitCenterError("结果包暂存失败，请重试")
    payload["job_id"] = job_id
    return payload


def get_job(job_id: str):
    """取回并移除结果包；同一 job_id 只能下载一次。"""
    if not job_id:
        return None
    with _lock:
        job = _jobs.pop(job_id, None)
        if job is None:
            return None
        if time.monotonic() - job.created_at > JOB_TTL_SECONDS:
            _prune_locked(time.monotonic())
            return None
        return {"download_name": job.download_name, "zip_bytes": job.zip_bytes}
