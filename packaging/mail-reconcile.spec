# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent
sys.path.insert(0,str(ROOT / "packaging"))
from bundle_assets import validate_runtime_assets, write_bundle_report, NUMPY_RUNTIME_IMPORTS

# Reject accidental packaging of the Chinese/full frontend.
index=ROOT/'frontend'/'dist'/'index.html'
if not index.exists() or 'lang="en"' not in index.read_text(encoding='utf-8'):
    raise RuntimeError('Build the English mail frontend with VITE_EDITION=mail_en first')
for script in (ROOT/'frontend'/'dist'/'assets').glob('*.js'):
    if '/api/ilog' in script.read_text(encoding='utf-8'):
        raise RuntimeError('English mail edition contains unrelated iLog frontend code')

datas = [
    (str(ROOT / "frontend" / "dist"), "frontend_dist"),
]
binaries = []
hiddenimports = [
    "app.mail_main",
    
    "app.accounts",
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

# Preserve the previously working complete dependency collection. Native
# extensions can import Python modules that static analysis cannot discover.
for package_name in ["numpy", "pandas", "extract_msg", "rapidocr_onnxruntime", "onnxruntime", "pypdfium2", "pypdfium2_raw"]:
    package_datas, package_binaries, package_hiddenimports = collect_all(package_name)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports
hiddenimports += NUMPY_RUNTIME_IMPORTS


a = Analysis(
    [str(ROOT / "backend" / "launcher_mail.py")],
    pathex=[str(ROOT / "backend")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["playwright", "app.main", "app.reconciliation", "app.excel_routes", "app.ilog_tasks", "app.ilog_retention", "app.ilog_unlock", "app.ilog_query", "app.ilog_batch"],
    noarchive=False,
)
for name,*_ in a.pure:
    if name=='playwright' or name.startswith('playwright.') or name in {'app.main','app.reconciliation','app.excel_routes','app.ilog_tasks','app.ilog_unlock'}:
        raise RuntimeError('English mail edition contains unrelated service: '+name)
validate_runtime_assets([*a.binaries, *a.datas], windows=False)
if sys.platform == "win32" and not any(name.replace("\\", "/")=="pypdfium2_raw/pdfium.dll" for name, _, _ in [*a.binaries,*a.datas]):
    raise RuntimeError("Required PDF runtime asset missing: pypdfium2_raw/pdfium.dll")
write_bundle_report([*a.binaries, *a.datas, *a.pure], ROOT / "build" / "bundle-size-report.json")
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Mail Reconciliation",
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
