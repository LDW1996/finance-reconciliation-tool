import socket

import launcher


def test_port_in_use_detects_existing_server(monkeypatch):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        monkeypatch.setattr(launcher, "PORT", listener.getsockname()[1])
        assert launcher.port_in_use() is True


def test_launcher_does_not_open_browser_when_port_is_occupied(monkeypatch):
    shown = []
    monkeypatch.setattr(launcher, "port_in_use", lambda: True)
    monkeypatch.setattr(launcher, "show_port_error", lambda: shown.append(True))
    monkeypatch.setattr(launcher.webbrowser, "open", lambda _: shown.append("browser"))

    launcher.main()

    assert shown == [True]


def test_browser_opens_only_after_own_server_starts(monkeypatch):
    opened = []
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)
    server = type("Server", (), {"started": True, "should_exit": False})()

    launcher.open_browser_when_ready(server)

    assert opened == [launcher.URL]


def test_diagnostics_capture_startup_exception_without_server_dialog(tmp_path,monkeypatch):
    log=tmp_path/'startup.log'
    monkeypatch.setenv('FINANCE_RECONCILE_STARTUP_LOG',str(log))
    def failed(*args):raise ModuleNotFoundError('synthetic_runtime_dependency')
    monkeypatch.setattr(launcher,'main',failed)
    assert launcher.run_with_diagnostics()==1
    assert 'synthetic_runtime_dependency' in log.read_text(encoding='utf-8')
