"""Thread-confined, account-isolated authenticated page lifetime."""
from threading import get_ident
from .ilog_query import URL


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
            browser.close()
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
                browser.close()
            except Exception:
                # Never return a page from a failed close to the pool.
                try:
                    page.close()
                except Exception:
                    pass

    def close(self):
        self._check()
        try:
            for browser in self._browsers:
                try:
                    browser.close()
                except Exception:
                    pass
        finally:
            self._pages.clear()
            self._page_browsers.clear()
            self._browsers.clear()
            self._closed = True
            if self._runtime is not None:
                self._runtime.stop()

    def __exit__(self, *exc):
        self.close()
