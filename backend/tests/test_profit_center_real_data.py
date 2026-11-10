"""阶段 A 独立复现回归（需显式提供真实附件目录）。

本地执行：
    FINANCE_PC_FIXTURES=/path/to/attachments python -m pytest \
        tests/test_profit_center_real_data.py -q

CI 未设置 FINANCE_PC_FIXTURES 时自动跳过：仓库不含任何财务原始报表，
也不含具体金额，只保存由校验结果派生出的 SHA-256 指纹。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

FIXTURES = os.environ.get("FINANCE_PC_FIXTURES", "")
FIXTURE_DIR = Path(FIXTURES) if FIXTURES else None
FINGERPRINT = (
    Path(__file__).parent / "fixtures" / "august_2026_fingerprint.json"
)

pytestmark = pytest.mark.skipif(
    not FIXTURE_DIR or not FIXTURE_DIR.is_dir(),
    reason="未设置 FINANCE_PC_FIXTURES 或目录不存在，跳过真实附件回归",
)

TEMPLATE_FILES = [
    "办事处简表-物流巴淡.xlsx",
    "办事处简表-物流雅加达-更新.xlsx",
    "办事处简表-物流三宝垄.xlsx",
    "办事处简表-物流帕鲁.xlsx",
]

REQUIRED = [
    *TEMPLATE_FILES,
    "办事处简表-物流巴淡(1).xlsx",
    "办事处简表-物流雅加达.xlsx",
    "办事处简表-物流三宝垄(1).xlsx",
    "办事处简表-物流帕鲁(1).xlsx",
    *[f"财务报表-物流{r}-{p}.XLS"
      for r in ("巴淡", "雅加达", "三宝垄", "帕鲁")
      for p in ("202607", "202608")],
    "财务报表-物流印尼202608.XLSX",
    "2025年外包服务成本.xlsx",
]

SALARIES = {"batam": 104167017, "jakarta": 97688048}


@pytest.fixture(autouse=True)
def _isolated_template_store(tmp_path, monkeypatch):
    """模板库指向临时目录：service 用例走「先存模板、再传数据」的新链路。"""
    monkeypatch.setenv("FINANCE_PC_TEMPLATE_DIR", str(tmp_path / "tpl"))


def _canonical(checks):
    return json.dumps(checks, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def test_august_2026_reproduction_matches_finance_version():
    from app.profit_center.engine import run_engine

    missing = [name for name in REQUIRED if not (FIXTURE_DIR / name).exists()]
    assert not missing, f"附件目录缺少: {missing}"
    files = [(name, (FIXTURE_DIR / name).read_bytes()) for name in REQUIRED]
    result = run_engine(files, "2026-08", SALARIES)

    failed = [c for c in result["checks"] if not c["pass"]]
    assert not failed, f"双口径校验未通过: {failed}"
    assert result["diffs"] == [], (
        f"独立复现与财务手工版存在 {len(result['diffs'])} 处差异: "
        f"{result['diffs'][:5]}"
    )

    expected = json.loads(FINGERPRINT.read_text(encoding="utf-8"))
    digest = hashlib.sha256(_canonical(result["checks"])).hexdigest()
    assert len(result["checks"]) == expected["check_count"]
    assert digest == expected["checks_sha256"], "双口径校验数值指纹发生变化"


def test_service_and_download_route_with_real_files():
    """产品链路：模板先入模板库 -> 数据源走 service.run_job -> download。

    HTTP 传输 / 鉴权 / POST 校验由 test_profit_center_service.py 的
    TestClient 端到端用例覆盖；这里直接串起 service + engine + download，
    避免真实附件用例依赖登录会话。
    """
    import asyncio
    import io
    import zipfile

    from app.profit_center import routes as pc_routes
    from app.profit_center import service as pc_service

    pc_service.save_templates(
        [(name, (FIXTURE_DIR / name).read_bytes()) for name in TEMPLATE_FILES]
    )
    data_files = [
        (name, (FIXTURE_DIR / name).read_bytes())
        for name in REQUIRED if name not in TEMPLATE_FILES
    ]
    pc_service._jobs.clear()
    body = pc_service.run_job(data_files, "2026-08", SALARIES)
    assert body["diffs"] == []
    assert all(c["pass"] for c in body["checks"])
    assert body["download_name"] == "利润中心上报-2026-08.zip"

    response = pc_routes.download(body["job_id"])
    assert response.status_code == 200

    async def collect():
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)
        return b"".join(chunks)

    content = asyncio.run(collect())
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert len([n for n in archive.namelist() if n.endswith(".xlsx")]) == 5
        assert "0 处差异" in archive.read("复现比对.md").decode("utf-8")
    assert pc_service.get_job(body["job_id"]) is None


def test_result_workbooks_are_formula_only_and_loadable():
    """公式格必须是 data_type='f'，且注入的外链可被 openpyxl 解析。"""
    import io
    import zipfile

    import openpyxl

    from app.profit_center.engine import run_engine

    files = [(name, (FIXTURE_DIR / name).read_bytes()) for name in REQUIRED]
    result = run_engine(files, "2026-08", SALARIES)
    with zipfile.ZipFile(io.BytesIO(result["zip_bytes"])) as archive:
        members = [n for n in archive.namelist() if n.endswith(".xlsx")
                   and n != "取数明细.xlsx"]
        assert len(members) == 4
        for name in members:
            wb = openpyxl.load_workbook(io.BytesIO(archive.read(name)),
                                        data_only=False, keep_links=True)
            ws = wb["2026年"]
            assert ws["I2"].data_type == "f", f"{name} I2 不是公式"
            assert ws["I2"].value.startswith("=")
            assert len(wb._external_links) >= 1
            wb.close()
