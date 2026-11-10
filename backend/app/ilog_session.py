"""Thread-confined, account-isolated authenticated page lifetime."""
import os
import signal
import subprocess
from threading import Event, Thread, get_ident
from .ilog_query import URL

# Sync Playwright never raises when the browser/driver process dies mid-call:
# the pending command just blocks forever (observed twice in production). Every
# wait therefore needs an upper bound; when one trips we kill the underlying
# processes, which reliably wakes the blocked call with an exception.
CLOSE_TIMEOUT_SECONDS = 15.0
CLOSE_KILL_GRACE_SECONDS = 5.0


def _process_table():
    """Best-effort {pid: (ppid, command)} snapshot; empty on failure."""
    try:
        out = subprocess.run(['ps', '-axo', 'pid=,ppid=,command='],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return {}
    table = {}
    for line in out.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        table[int(parts[0])] = (int(parts[1]), parts[2])
    return table


def browser_process_pids():
    """PIDs of this process's playwright driver and its launched browsers."""
    mine = os.getpid()
    table = _process_table()

    def reaches_root(pid):
        seen = set()
        while pid in table and pid not in seen:
            if pid == mine:
                return True
            seen.add(pid)
            pid = table[pid][0]
        return False

    matched = []
    for pid, (_ppid, command) in table.items():
        if pid == mine:
            continue
        hit = ('ms-playwright' in command
               or ('playwright' in command and 'run-driver' in command)
               or '--remote-debugging-pipe' in command)
        if hit and reaches_root(pid):
            matched.append(pid)
    return matched


def kill_browser_processes():
    """Force-kill stuck playwright driver/browsers; returns killed pids."""
    killed = []
    for pid in browser_process_pids():
        try:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/F', '/PID', str(pid)],
                               capture_output=True, timeout=10)
            else:
                os.kill(pid, signal.SIGKILL)
            killed.append(pid)
        except Exception:
            continue
    return killed


class SessionPool:
    def __init__(self):
        self._owner = get_ident()
        self._pages = {}
        self._browsers = []
        self._page_browsers = {}
        self._runtime = None
        self._closed = False

    def _check(self):
        if get_ident() != self._owner or self._closed:
            raise RuntimeError('SessionPool must be used in its owning task thread before close')

    def __enter__(self):
        self._check()
        return self

    def _close_bounded(self, browser):
        """close() on a half-dead browser blocks forever (observed in
        production); bound it and kill the processes to wake the call."""
        done = Event()

        def finish():
            try:
                browser.close()
            except Exception:
                pass
            finally:
                done.set()

        Thread(target=finish, daemon=True).start()
        if done.wait(CLOSE_TIMEOUT_SECONDS):
            return
        try:
            kill_browser_processes()
        except Exception:
            pass
        done.wait(CLOSE_KILL_GRACE_SECONDS)

    def page(self, account_id, account, password, channel='chrome', headed=False):
        self._check()
        key = (account_id, account['username'], password, channel, headed)
        if key in self._pages:
            return self._pages[key]
        if self._runtime is None:
            from playwright.sync_api import sync_playwright
            self._runtime = sync_playwright().start()
        browser = self._runtime.chromium.launch(channel=channel, headless=not headed)
        self._browsers.append(browser)
        try:
            page = browser.new_page(viewport={'width':1440,'height':1000})
            page.set_default_timeout(20000)
            page.goto(URL)
            language = page.locator('[name=language]')
            language.wait_for(state='attached')
            if not language.input_value():
                language.locator('..').locator('img').click()
                with page.expect_navigation():
                    page.get_by_text('简体中文', exact=True).click()
            page.locator('[name=adminUserName]').fill(account['username'])
            page.locator('[name=adminUserPassword]').fill(password)
            page.locator('[name=status]').uncheck()
            page.get_by_role('button', name='登陆', exact=True).click()
            page.get_by_text('委托单列表', exact=True).click()
            page.locator('input[name=search]').wait_for()
            self._pages[key] = page
            self._page_browsers[key] = browser
            return page
        except BaseException:
            self._browsers.remove(browser)
            self._close_bounded(browser)
            raise

    def invalidate(self, account_id):
        """Discard every cached login for an account without touching other accounts."""
        self._check()
        for key in list(self._pages):
            if key[0] != account_id:
                continue
            page = self._pages.pop(key)
            browser = self._page_browsers.pop(key)
            self._browsers.remove(browser)
            try:
                self._close_bounded(browser)
            except Exception:
                # Never return a page from a failed close to the pool.
                try:
                    page.close()
                except Exception:
                    pass

    def close(self):
        # No owner check here: the watchdog thread below must be able to call it.
        if self._closed:
            return
        try:
            for browser in self._browsers:
                try:
                    self._close_bounded(browser)
                except Exception:
                    pass
        finally:
            self._pages.clear()
            self._page_browsers.clear()
            self._browsers.clear()
            self._closed = True
            if self._runtime is not None:
                self._runtime.stop()

    def _close_with_watchdog(self):
        """close() can block forever on a dead driver; bound it, then kill."""
        if self._closed:
            return
        done = Event()

        def finish():
            try:
                self.close()
            except Exception:
                pass
            finally:
                done.set()

        Thread(target=finish, daemon=True).start()
        if done.wait(CLOSE_TIMEOUT_SECONDS):
            return
        # Close is stuck (browser/driver died mid-operation). Kill the processes
        # to give close() a chance to raise and return, then abandon it anyway.
        try:
            kill_browser_processes()
        except Exception:
            pass
        done.wait(CLOSE_KILL_GRACE_SECONDS)

    def __exit__(self, *exc):
        self._close_with_watchdog()
