# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).parent
sys.path.insert(0,str(ROOT / "packaging"))
from bundle_assets import runtime_data, validate_runtime_assets, write_bundle_report

datas = [
    (str(ROOT / "frontend" / "dist"), "frontend_dist"),
]
binaries = []
hiddenimports = [
    "app.main",
    "app.reconciliation",
    "app.accounts",
    "app.ilog_tasks",
    "app.ilog_retention",
    "app.ilog_unlock",
    "app.ilog_query",
    "app.ilog_batch",
    "app.outlook_probe",
    "app.mail_tasks",
    "app.mail_matching",
    "app.mail_insights",
    "app.mail_workspace",
    "app.mail_sync",
    "app.mail_scope",
    "app.mail_online",
    "app.google_sheets",
    "app.mail_attachments",
    "pythoncom",
    "pywintypes",
    "win32com.client",
    "win32timezone",
    "playwright.sync_api",
    "json5",
    "cryptography.fernet",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "pandas",
    "openpyxl",
    "xlrd",
]

# Rely on upstream hooks for native libraries and Playwright's Node driver.
# collect_all previously forced tests, optional tools and source files into the EXE.
datas += runtime_data(collect_data_files)


a = Analysis(
    [str(ROOT / "backend" / "launcher.py")],
    pathex=[str(ROOT / "backend")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "_pytest", "pandas.tests", "numpy.tests"],
    noarchive=False,
)
validate_runtime_assets([*a.binaries, *a.datas], windows=sys.platform == "win32")
write_bundle_report([*a.binaries, *a.datas, *a.pure], ROOT / "build" / "bundle-size-report.json")
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ilog解锁工具",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

write_bundle_report([*a.binaries, *a.datas, *a.pure], ROOT / "build" / "bundle-size-report.json", exe.name)
