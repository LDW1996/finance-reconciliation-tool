# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent
sys.path.insert(0,str(ROOT / "packaging"))
from bundle_assets import write_bundle_report, NUMPY_RUNTIME_IMPORTS

EXCLUDED = ['app.ilog_query', 'app.mail_insights', 'app.mail_workspace', 'app.ilog_tasks', 'app.mail_attachments', 'app.ilog_retention', 'app.ilog_unlock', 'app.mail_storage', 'app.accounts', 'app.mail_matching', 'app.ilog_batch', 'app.outlook_probe', 'app.mail_workers', 'app.mail_scope', 'app.mail_tasks', 'app.mail_sync', 'app.main', 'app.bundle_probe', 'app.mail_online', 'app.google_sheets', 'playwright', 'extract_msg', 'rapidocr_onnxruntime', 'onnxruntime', 'pypdfium2', 'pypdfium2_raw', 'cv2', 'win32com', 'pythoncom', 'pywintypes']
# Fail before creating an EXE if the wrong frontend was built.
for script in (ROOT / 'frontend' / 'dist' / 'assets').glob('*.js'):
    content=script.read_text(encoding='utf-8')
    if any(value in content for value in ('/api/ilog','/api/mail','iLog批量解锁')):
        raise RuntimeError('Build the Excel frontend with VITE_EDITION=excel first')

datas = [
    (str(ROOT / "frontend" / "dist"), "frontend_dist"),
]
binaries = []
hiddenimports = ['app.excel_main', 'app.excel_routes', 'app.excel_probe', 'app.reconciliation', 'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto', 'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan', 'uvicorn.lifespan.on', 'pandas', 'openpyxl', 'xlrd']

# Preserve complete NumPy/pandas collection. Native
# extensions can import Python modules that static analysis cannot discover.
for package_name in ["numpy", "pandas"]:
    package_datas, package_binaries, package_hiddenimports = collect_all(package_name)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports
hiddenimports += NUMPY_RUNTIME_IMPORTS


a = Analysis(
    [str(ROOT / "backend" / "launcher_excel.py")],
    pathex=[str(ROOT / "backend")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDED,
    noarchive=False,
)
for name, *_ in a.pure:
    if any(name == blocked or name.startswith(blocked+'.') for blocked in EXCLUDED):
        raise RuntimeError('Excel edition contains excluded module: '+name)
write_bundle_report([*a.binaries, *a.datas, *a.pure], ROOT / "build" / "bundle-size-report.json")
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Excel对账工具",
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
