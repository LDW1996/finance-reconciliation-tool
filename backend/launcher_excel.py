"""Fixed Excel-only entry point; environment variables cannot enable full services."""
from multiprocessing import freeze_support
from launcher import run_with_diagnostics
from app.excel_probe import write_report

if __name__=='__main__':
    freeze_support()
    raise SystemExit(run_with_diagnostics('app.excel_main:app',write_report))
