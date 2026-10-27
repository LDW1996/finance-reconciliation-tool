from __future__ import annotations

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
    message = "本地端口 8765 已被占用。请在任务管理器中结束旧版财务对账工具，再重新打开新版程序。"
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "财务对账工具无法启动", 0x10)
    else:
        print(message, file=sys.stderr)


def main() -> None:
    if len(sys.argv)==3 and sys.argv[1]=='--verify-bundle':
        from app.bundle_probe import write_report
        raise SystemExit(0 if write_report(sys.argv[2]) else 1)
    if port_in_use():
        show_port_error()
        return
    config = uvicorn.Config(
        "app.main:app",
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


if __name__ == "__main__":
    main()
