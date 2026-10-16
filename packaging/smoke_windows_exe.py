from __future__ import annotations

from io import BytesIO
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pandas as pd
from openpyxl import load_workbook


APP_URL = "http://127.0.0.1:8765"


def wait_for_health(timeout_seconds: int = 45) -> None:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            response = httpx.get(f"{APP_URL}/api/health", timeout=2)
            if response.status_code == 200 and response.json().get("status") == "ok":
                return
        except Exception as exc:
            last_error = exc
        time.sleep(1)
    raise RuntimeError(f"EXE did not expose /api/health in time: {last_error}")


def make_sample_workbook(path: Path) -> None:
    rows = [
        {"公司代码or伙伴公司": "1703", "凭证编号": "A-1", "分配": "OK001", "文本": "抵消", "原币金额": 100},
        {"公司代码or伙伴公司": "2615", "凭证编号": "B-1", "分配": "OK001", "文本": "抵消", "原币金额": -100},
        {"公司代码or伙伴公司": "1703", "凭证编号": "A-2", "分配": "DIFF001", "文本": "差异", "原币金额": 100},
        {"公司代码or伙伴公司": "2615", "凭证编号": "B-2", "分配": "DIFF001", "文本": "差异", "原币金额": 80},
        {"公司代码or伙伴公司": "1703", "凭证编号": "A-3", "分配": "", "文本": "空分配", "原币金额": 50},
    ]
    pd.DataFrame(rows).to_excel(path, index=False)


def assert_frontend_served() -> None:
    response = httpx.get(APP_URL, timeout=10)
    response.raise_for_status()
    if '<div id="app"></div>' not in response.text:
        raise AssertionError("Frontend index.html was not served by the EXE")
    asset = re.search(r'src="(/assets/[^"]+\.js)"', response.text)
    if not asset:
        raise AssertionError("Frontend JavaScript asset was not linked")
    script = httpx.get(f"{APP_URL}{asset.group(1)}", timeout=10)
    script.raise_for_status()
    if "原表标色" not in script.text:
        raise AssertionError("The EXE contains an outdated frontend build")
    commit = os.environ.get("GITHUB_SHA", "")
    if commit and commit[:8] not in script.text:
        raise AssertionError("The EXE does not contain the current workflow commit")


def assert_excel_flow(sample_path: Path) -> None:
    with sample_path.open("rb") as file_obj:
        response = httpx.post(
            f"{APP_URL}/api/inspect-combined",
            files={"workbook": (sample_path.name, file_obj, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"match_field": "分配", "amount_field": "原币金额"},
            timeout=20,
        )
    response.raise_for_status()
    report = response.json()
    if not report.get("valid"):
        raise AssertionError(f"Inspect endpoint rejected valid workbook: {report}")
    if report.get("companyCodes") != ["1703", "2615"]:
        raise AssertionError(f"Unexpected company codes: {report}")

    with sample_path.open("rb") as file_obj:
        response = httpx.post(
            f"{APP_URL}/api/analyze-combined",
            files={"workbook": (sample_path.name, file_obj, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"match_field": "分配", "amount_field": "原币金额"},
            timeout=30,
        )
    response.raise_for_status()
    if response.headers.get("x-allocation-matched") != "1":
        raise AssertionError(f"Unexpected matched count headers: {response.headers}")
    if len(response.content) < 5000:
        raise AssertionError("Generated Excel response is unexpectedly small")
    detailed = load_workbook(BytesIO(response.content), read_only=True)
    if "处理汇总" not in detailed.sheetnames or "原始数据" not in detailed.sheetnames:
        raise AssertionError(f"Detailed export has unexpected sheets: {detailed.sheetnames}")

    with sample_path.open("rb") as file_obj:
        marked_response = httpx.post(
            f"{APP_URL}/api/analyze-combined",
            files={"workbook": (sample_path.name, file_obj, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"output_mode": "marked", "tolerance": "100"},
            timeout=30,
        )
    marked_response.raise_for_status()
    marked = load_workbook(BytesIO(marked_response.content), read_only=False)
    if marked.sheetnames != ["Sheet1"] or marked.active["A2"].fill.fgColor.rgb != "FFD2EEDA":
        raise AssertionError("Marked export did not color the original sheet")


def assert_ilog_local_services() -> None:
    # Never log in to iLog or submit business operations in CI.
    for endpoint in ('/api/ilog/runtime','/api/ilog/retention','/api/ilog/tasks?limit=20&offset=0'):
        response=httpx.get(APP_URL+endpoint,timeout=10)
        response.raise_for_status()
    headers={'X-Finance-Local':'1'}
    created=httpx.post(APP_URL+'/api/ilog/accounts',json={
        'name':'Smoke test','username':'smoke-test-only','password':'not-a-real-password'},headers=headers,timeout=10)
    created.raise_for_status()
    account=created.json()
    if not account.get('hasPassword') or 'not-a-real-password' in created.text:
        raise AssertionError('Account storage or secret redaction failed')
    response=httpx.delete(APP_URL+'/api/ilog/accounts/'+account['id'],headers=headers,timeout=10)
    response.raise_for_status()


def main() -> int:
    exe_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dist") / "财务对账工具.exe"
    if not exe_path.exists():
        raise FileNotFoundError(exe_path)

    try:
        httpx.get(f"{APP_URL}/api/health", timeout=2)
    except httpx.RequestError:
        pass
    else:
        raise RuntimeError("Port 8765 is already serving an app; cannot verify the new EXE")

    sandbox = tempfile.TemporaryDirectory(prefix='finance-exe-smoke-')
    process = subprocess.Popen([str(exe_path)], env={**os.environ,'FINANCE_RECONCILE_DATA_DIR':sandbox.name})
    try:
        wait_for_health()
        assert_frontend_served()
        assert_ilog_local_services()
        with tempfile.TemporaryDirectory() as temp_dir:
            sample_path = Path(temp_dir) / "sample.xlsx"
            make_sample_workbook(sample_path)
            assert_excel_flow(sample_path)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        sandbox.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
