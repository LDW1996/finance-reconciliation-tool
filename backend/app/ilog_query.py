"""Read-only iLog browser workflow. No business mutation actions are included."""

import argparse
import json
import re
from tempfile import NamedTemporaryFile
from urllib.parse import unquote_plus, urlsplit
from uuid import uuid4

from app.accounts import AccountStore, account_data_dir
from app.bill_validation import valid_bill

LOGIN_URL = 'https://jk.sitc.com/ilog/'
ORDER_LIST_PATH = 'desktop/kanbanm/booking.html'


def normalize_platform_url(value):
    parsed = urlsplit(value.strip())
    host = parsed.hostname or ''
    if (
        parsed.scheme != 'https'
        or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+sitc\.com', host)
        or parsed.netloc.lower() != host
        or parsed.path not in ('', '/', '/ilog', '/ilog/')
        or parsed.query or parsed.fragment
    ):
        raise ValueError('请输入 SITC 的 HTTPS 登录地址，例如 https://jk.sitc.com/ilog/')
    return f'https://{host}/ilog/'


def platform_url():
    try:
        return normalize_platform_url((account_data_dir() / 'ilog-platform-url.txt').read_text(encoding='utf-8'))
    except FileNotFoundError:
        return LOGIN_URL


def save_platform_url(url):
    directory = account_data_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory, delete=False) as file:
        temporary = directory / file.name
        try:
            file.write(url)
            file.close()
            temporary.replace(directory / 'ilog-platform-url.txt')
        finally:
            temporary.unlink(missing_ok=True)


def order_list_url(page):
    parsed = urlsplit(getattr(page, 'url', LOGIN_URL))
    return normalize_platform_url(f'{parsed.scheme}://{parsed.netloc}') + ORDER_LIST_PATH


def check_login_origin(page, login_url):
    if urlsplit(page.url).netloc != urlsplit(login_url).netloc or urlsplit(page.url).scheme != 'https':
        raise RuntimeError('iLog login redirected outside the configured origin')



def open_order_menu(page):
    menu = page.get_by_text('委托单列表', exact=True)
    menu.wait_for()
    close_update_log = page.locator(
        '.x-window:visible:has(.x-window-header-text:text-is("Update Log")) .x-tool-close'
    )
    if close_update_log.count() == 1:
        close_update_log.click()
    menu.click()


def validate_bill(value: str) -> str:
    value = value.strip()
    if not valid_bill(value):
        raise ValueError('Invalid bill number')
    return value


def query_bill(account_id: str, bill: str, *, headless=True, channel='chrome'):
    from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout

    bill = validate_bill(bill)
    store = AccountStore()
    account = next((a for a in store.list_accounts() if a['id'] == account_id), None)
    if account is None:
        raise ValueError('Unknown account')
    password = store.password_for(account_id)
    if not password:
        raise ValueError('Account has no saved password')
    result = {'account': account['name'], 'bill': bill, 'status': 'needs_manual', 'stage': 'browser'}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=channel, headless=headless)
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.set_default_timeout(20000)
            result['stage'] = 'login'
            login_url = platform_url()
            page.goto(login_url)
            check_login_origin(page, login_url)
            language = page.locator('[name=language]')
            language.wait_for(state='attached')
            # Fresh ExtJS sessions have an empty required language field.
            if not language.input_value():
                language.locator('..').locator('img').click()
                with page.expect_navigation():
                    page.get_by_text('简体中文', exact=True).click()
            check_login_origin(page, login_url)
            page.locator('[name=adminUserName]').fill(account['username'])
            page.locator('[name=adminUserPassword]').fill(password)
            page.locator('[name=status]').uncheck()
            page.get_by_role('button', name='登陆', exact=True).click()
            page.wait_for_url(login_url + 'SysLogin!index')
            page.goto(login_url + ORDER_LIST_PATH)
            result['stage'] = 'query'
            open_order_menu(page)
            search = page.locator('input[name=search]')
            search.wait_for()
            # Wait for the initial list before submitting the bill filter.
            page.wait_for_function("() => !Array.from(document.querySelectorAll('.ext-el-mask')).some(e => e.offsetWidth && e.offsetHeight)")
            search.fill(bill)
            def is_query(response):
                request = response.request
                data = unquote_plus(request.url + ' ' + (request.post_data or ''))
                return request.resource_type == 'xhr' and bill in data
            with page.expect_response(is_query) as pending:
                search.press('Enter')
            response = pending.value
            if not response.ok:
                result['status'] = 'query_failed'
                return result
            response.body()
            # Allow the ExtJS response callback to commit its store and view.
            page.wait_for_function("() => !window.Ext || !Ext.Ajax.isLoading()")
            page.wait_for_function("() => !Array.from(document.querySelectorAll('.ext-el-mask')).some(e => e.offsetWidth && e.offsetHeight)")
            rows = page.locator('.x-grid3-row:visible')
            texts = rows.all_inner_texts()
            matched = [text for text in texts if bill.casefold() in text.casefold()]
            empty = page.get_by_text(re.compile(r'^no data\.?$', re.I)).filter(visible=True)
            if matched:
                result.update(status='found', count=len(matched), rows=matched[:10])
            elif not texts and empty.count():
                result.update(status='not_found', count=0)
            else:
                result.update(status='needs_manual', reason='Unexpected or stale query result')
            directory = account_data_dir() / 'query-results'
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            screenshot = directory / f'{uuid4().hex}.png'
            page.screenshot(path=str(screenshot), full_page=True)
            screenshot.chmod(0o600)
            result['screenshot'] = str(screenshot)
            return result
        except BrowserTimeout:
            result['reason'] = 'Page timed out; stopped without retrying or changing business data'
            return result
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser(description='Read-only iLog bill query')
    parser.add_argument('--account', required=True)
    parser.add_argument('--bill', required=True)
    parser.add_argument('--show-browser', action='store_true')
    parser.add_argument('--channel', choices=['chrome', 'msedge'], default='chrome')
    args = parser.parse_args()
    try:
        result = query_bill(args.account, args.bill, headless=not args.show_browser, channel=args.channel)
    except Exception:
        # Browser exception details may contain sensitive form contents.
        result = {'status': 'failed', 'reason': 'Check browser installation, account configuration and network'}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] in ('found', 'not_found') else 1


if __name__ == '__main__':
    raise SystemExit(main())
