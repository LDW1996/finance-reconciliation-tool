from __future__ import annotations

import socket
import threading
import time
import webbrowser

import uvicorn


HOST = "127.0.0.1"
PORT = 8765
URL = f"http://{HOST}:{PORT}"


def wait_for_server(timeout_seconds: int = 20) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex((HOST, PORT)) == 0:
                return True
        time.sleep(0.2)
    return False


def open_browser_when_ready() -> None:
    if wait_for_server():
        webbrowser.open(URL)


def main() -> None:
    threading.Thread(target=open_browser_when_ready, daemon=True).start()
    uvicorn.run(
        "app.main:app",
        host=HOST,
        port=PORT,
        log_level="warning",
        reload=False,
    )


if __name__ == "__main__":
    main()
