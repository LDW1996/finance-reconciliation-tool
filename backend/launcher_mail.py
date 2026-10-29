from __future__ import annotations

import os
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
import traceback
import socket
import threading
import time
import webbrowser
import sys

import uvicorn


HOST = "127.0.0.1"
PORT = 8765
URL = f"http://{HOST}:{PORT}"


def port_in_use() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((HOST, PORT)) == 0


def open_browser_when_ready(server: uvicorn.Server, timeout_seconds: int = 20) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if server.started:
            webbrowser.open(URL)
            return
        if server.should_exit:
            return
        time.sleep(0.2)


def show_port_error() -> None:
    message = "Port 8765 is already in use. Close the existing Finance or Mail Reconciliation application in Task Manager, then reopen this application."
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "Mail Reconciliation cannot start", 0x10)
    else:
        print(message, file=sys.stderr)


def main(app_target="app.mail_main:app", bundle_report=None) -> None:
    if len(sys.argv)==3 and sys.argv[1]=='--verify-bundle':
        if bundle_report is None:
            from app.mail_probe import write_report
            bundle_report=write_report
        raise SystemExit(0 if bundle_report(sys.argv[2]) else 1)
    if port_in_use():
        show_port_error()
        return
    config = uvicorn.Config(
        app_target,
        host=HOST,
        port=PORT,
        log_level="warning",
        log_config=None,
        access_log=False,
        reload=False,
    )
    server = uvicorn.Server(config)
    threading.Thread(target=open_browser_when_ready, args=(server,), daemon=True).start()
    server.run()


def run_with_diagnostics(app_target="app.mail_main:app", bundle_report=None) -> int:
    # CI opts in explicitly. Normal desktop launches keep their existing behavior.
    target=os.environ.get('FINANCE_RECONCILE_STARTUP_LOG')
    if not target:
        main(app_target, bundle_report)
        return 0
    path=Path(target);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a',encoding='utf-8',buffering=1) as log, redirect_stdout(log), redirect_stderr(log):
        try:main(app_target, bundle_report)
        except SystemExit as error:return error.code if isinstance(error.code,int) else (0 if error.code is None else 1)
        except Exception:
            traceback.print_exc()
            return 1
    return 0


if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    raise SystemExit(run_with_diagnostics())
