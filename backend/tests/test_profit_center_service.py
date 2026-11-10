"""无状态 job store、模板管理与 /api/profit-center/* 端到端契约测试。"""
from __future__ import annotations

import io
import zipfile

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.profit_center import service as pc_service
from app.profit_center.config import REGIONS
from app.profit_center.service import ProfitCenterError, parse_exec_salaries

MUTATION_HEADERS = {"X-Finance-Local": "1"}


@pytest.fixture(autouse=True)
def _isolated_template_store(tmp_path, monkeypatch):
    """模板落盘目录指向临时目录，避免测试污染真实模板库。"""
    monkeypatch.setenv("FINANCE_PC_TEMPLATE_DIR", str(tmp_path / "tpl"))


def _fake_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("demo.txt", "ok")
    return buf.getvalue()


def _tiny_xlsx(name: str) -> tuple[str, bytes]:
    wb = openpyxl.Workbook()
    wb.active["A1"] = name
    buf = io.BytesIO()
    wb.save(buf)
    return name, buf.getvalue()


def _fake_engine(files, month, salaries, templates=None):
    return {
        "checks": [{"name": "净利润-2026", "scope": "当月", "sum": 1.0,
                    "benchmark": 1.0, "delta": 0.0, "pass": True, "note": ""}],
        "diffs": [],
        "warnings": [],
        "download_name": f"利润中心上报-{month}.zip",
        "zip_bytes": _fake_zip(),
    }


def test_parse_exec_salaries():
    assert parse_exec_salaries('{"batam": "104,167,017", "jakarta": null}') == {
        "batam": 104167017.0
    }
    assert parse_exec_salaries("") == {}
    for bad in ("not-json", "[1,2]", '{"batam": "abc"}', '{"batam": -1}'):
        with pytest.raises(ProfitCenterError):
            parse_exec_salaries(bad)


def test_job_store_is_one_time_and_size_bounded(monkeypatch):
    monkeypatch.setattr(pc_service, "run_engine", _fake_engine)
    pc_service._jobs.clear()
    payload = pc_service.run_job([("a.xlsx", b"x")], "2026-08", {"batam": 1})
    assert "zip_bytes" not in payload
    assert payload["job_id"]
    assert payload["download_name"] == "利润中心上报-2026-08.zip"
    assert payload["checks"][0]["pass"] is True

    job = pc_service.get_job(payload["job_id"])
    assert job["zip_bytes"] == _fake_zip()
    assert pc_service.get_job(payload["job_id"]) is None
    assert pc_service.get_job("nope") is None

    with pytest.raises(ProfitCenterError, match="至少上传"):
        pc_service.run_job([], "2026-08", {})
    with pytest.raises(ProfitCenterError, match="最多上传"):
        pc_service.run_job([("a.xlsx", b"x")] * 41, "2026-08", {})


def test_job_store_expires(monkeypatch):
    monkeypatch.setattr(pc_service, "run_engine", _fake_engine)
    pc_service._jobs.clear()
    payload = pc_service.run_job([("a.xlsx", b"x")], "2026-08", {})
    pc_service._jobs[payload["job_id"]].created_at -= (
        pc_service.JOB_TTL_SECONDS + 1)
    assert pc_service.get_job(payload["job_id"]) is None


def test_run_download_end_to_end(monkeypatch):
    monkeypatch.setattr(pc_service, "run_engine", _fake_engine)
    pc_service._jobs.clear()
    with TestClient(app) as client:
        response = client.post(
            "/api/profit-center/run",
            headers=MUTATION_HEADERS,
            files={"files": ("dummy.xlsx", b"dummy", "application/octet-stream")},
            data={"month": "2026-08", "exec_salaries": '{"batam": 1}'},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"job_id", "download_name", "checks", "diffs", "warnings"}

        downloaded = client.get(f"/api/profit-center/download/{body['job_id']}")
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.headers["content-type"].startswith("application/zip")
        with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
            assert archive.read("demo.txt") == b"ok"

        # 结果包一次性取回，重复下载应 404
        assert client.get(
            f"/api/profit-center/download/{body['job_id']}"
        ).status_code == 404


def test_run_rejects_bad_salary_payload():
    with TestClient(app) as client:
        response = client.post(
            "/api/profit-center/run",
            headers=MUTATION_HEADERS,
            files={"files": ("dummy.xlsx", b"dummy")},
            data={"exec_salaries": "not-json"},
        )
    assert response.status_code == 400, response.text
    assert "高管工资" in response.json()["detail"]


def test_run_surfaces_business_errors_as_400():
    # 模板库为空时（fixture 已隔离），run 应报「缺少简表模板」并引导到模板管理
    with TestClient(app) as client:
        response = client.post(
            "/api/profit-center/run",
            headers=MUTATION_HEADERS,
            files={"files": ("dummy.xlsx", b"dummy")},
        )
    assert response.status_code == 400, response.text
    assert "缺少简表模板" in response.json()["detail"]
    assert "模板管理" in response.json()["detail"]


# --------------------------------------------------------------------------
# 模板管理：一次上传、每月复用、与 /run 的数据源分离
# --------------------------------------------------------------------------

def test_template_store_save_list_delete():
    pc_service.save_templates([_tiny_xlsx(r.template) for r in REGIONS])
    listed = {t["key"]: t for t in pc_service.list_templates()}
    assert all(t["uploaded"] for t in listed.values())
    assert listed["batam"]["stored_name"] == "办事处简表-物流巴淡.xlsx"
    assert listed["batam"]["uploaded_at"]

    assert set(pc_service.load_templates()) == {r.key for r in REGIONS}

    pc_service.delete_template("batam")
    listed = {t["key"]: t for t in pc_service.list_templates()}
    assert listed["batam"]["uploaded"] is False
    assert "batam" not in pc_service.load_templates()
    assert all(listed[r.key]["uploaded"] for r in REGIONS if r.key != "batam")


def test_template_store_rejects_unrecognized_or_invalid():
    with pytest.raises(ProfitCenterError, match="无法识别"):
        pc_service.save_templates([_tiny_xlsx("随便什么文件.xlsx")])
    # 识别通过但内容不是 xlsx
    with pytest.raises(ProfitCenterError, match="不是有效的"):
        pc_service.save_templates([("办事处简表-物流巴淡.xlsx", b"not-a-zip")])
    # 拒绝后不能有半写入
    assert all(not t["uploaded"] for t in pc_service.list_templates())
    with pytest.raises(ProfitCenterError, match="未知"):
        pc_service.delete_template("nonexistent")


def test_templates_endpoints_over_http():
    with TestClient(app) as client:
        empty = client.get("/api/profit-center/templates")
        assert empty.status_code == 200, empty.text
        assert {t["key"] for t in empty.json()["templates"]} == {
            r.key for r in REGIONS
        }
        assert all(not t["uploaded"] for t in empty.json()["templates"])

        name, data = _tiny_xlsx("办事处简表-物流巴淡.xlsx")
        uploaded = client.post(
            "/api/profit-center/templates",
            headers=MUTATION_HEADERS,
            files={"files": (name, data)},
        )
        assert uploaded.status_code == 200, uploaded.text
        batam = next(t for t in uploaded.json()["templates"]
                     if t["key"] == "batam")
        assert batam["uploaded"] is True
        assert batam["stored_name"] == name

        removed = client.delete(
            "/api/profit-center/templates/batam", headers=MUTATION_HEADERS)
        assert removed.status_code == 200, removed.text
        batam = next(t for t in removed.json()["templates"]
                     if t["key"] == "batam")
        assert batam["uploaded"] is False
