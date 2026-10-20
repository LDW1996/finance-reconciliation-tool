"""Single-order runner: inspection by default, explicit execution, no retries."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from app.accounts import AccountStore, account_data_dir
from app.ilog_query import URL, validate_bill


def parse_reply(raw):
    import json5
    if len(raw) > 100000:
        raise ValueError('Reply too large')
    try:
        value = json5.loads(raw, allow_duplicate_keys=False)
    except Exception:
        raise ValueError('Unrecognized platform reply') from None
    if not isinstance(value, dict) or type(value.get('success')) is not bool:
        raise ValueError('Missing boolean success')
    return value


def outcome(reply, before, after, kind=None):
    if not reply:
        return 'needs_verification'
    if reply.get('success') is False:
        return 'rejected'
    if after == 'U':
        return 'verified_unlocked'
    if kind == 'fee' and before == 'A' and after == 'B':
        return 'verified_fee_unlocked'
    if before and after and before != after:
        return 'platform_confirmed'
    return 'needs_verification'


def reserve_operation(directory, account, bill, kind, *, current_lock=None, legacy_bill=None):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = hashlib.sha256(json.dumps([account, bill, kind]).encode()).hexdigest()
    path = directory / f'{key}.json'
    previous = None
    candidates = []
    if legacy_bill is not None:
        legacy_key = hashlib.sha256(json.dumps([account, legacy_bill, kind]).encode()).hexdigest()
        legacy = directory / f'{legacy_key}.json'
        if legacy != path and legacy.exists():
            candidates.append(legacy)
    if path.exists():
        candidates.append(path)
    index = 1
    while (directory / f'{key}.{index}.json').exists():
        candidates.append(directory / f'{key}.{index}.json')
        index += 1
    for candidate in candidates:
        try:
            prior = json.loads(candidate.read_text())
            after = prior.get('after') or {}
            allowed = (kind == 'order' and current_lock in ('A', 'B')
                       and prior.get('status') == 'verified_unlocked'
                       and prior.get('platform_success') is True
                       and str(after.get('id')) == str(bill) and after.get('lock') == 'U')
        except (ValueError, OSError, AttributeError):
            allowed = False
        if not allowed:
            raise FileExistsError('Prior submission is not verified successful')
        previous = candidate.name
    if candidates:
        path = directory / f'{key}.{index}.json'
    with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'w') as f:
        json.dump({'account': account, 'bill': bill, 'kind': kind,
                   'previous_submission': previous, 'current_lock': current_lock,
                   'status': 'reserved_before_submission',
                   'time': datetime.now(timezone.utc).isoformat()}, f)
        f.flush()
        os.fsync(f.fileno())
    return path


def private_report(path, data):
    temp = path.with_name(uuid4().hex + '.tmp')
    with os.fdopen(os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def records(page):
    # Read the ExtJS store instead of inferring state from colored icons.
    return page.evaluate('''() => {
      const components=[];
      Ext.ComponentMgr.all.each(c=>{if(typeof c.doCancelOrder==='function')components.push(c)});
      if(components.length!==1)throw Error('Unexpected order list');
      return components[0].grid.store.getRange().map(r=>({
        id:r.get('jobOrderId'),mbl:r.get('mblNo'),hbl:r.get('hblNo'),
        booking:r.get('bkNo'),lock:r.get('lockState')}));
    }''')


def record_step(result, report_path, stage):
    result['stage'] = stage
    result.setdefault('steps', []).append({'stage': stage, 'time': datetime.now(timezone.utc).isoformat()})
    private_report(report_path, result)


def compact_success(result):
    if result.get('status') in ('inspected', 'already_unlocked', 'already_fee_unlocked',
                                'verified_unlocked', 'verified_fee_unlocked'):
        for key in ('steps', 'stage', 'selected_ids', 'matched_count', 'confirmation_text',
                    'action', 'response_error_type', 'response_note', 'error_context', 'request_diagnostics',
                    'verification_attempts'):
            result.pop(key, None)


def error_context(error):
    # Extract only operation and timeout; Playwright call logs may contain credentials.
    text = str(error)
    context = {}
    operation = re.match(r'([A-Za-z]+\.[A-Za-z_]+):', text)
    timeout = re.search(r'Timeout (\d+)ms exceeded', text)
    if operation:
        context['operation'] = operation.group(1)
    if timeout:
        context['timeout_ms'] = int(timeout.group(1))
    return context


def retry_verification(operation, result):
    """Only the read-only post-submission query belongs inside operation."""
    from playwright.sync_api import Error as BrowserError
    for attempt in range(4):
        entry = {'attempt': attempt + 1, 'time': datetime.now(timezone.utc).isoformat()}
        result.setdefault('verification_attempts', []).append(entry)
        try:
            value = operation()
            entry['status'] = 'completed'
            result['verification_retries'] = attempt
            return value
        except Exception as error:
            entry.update(status='failed', stage=result.get('stage'),
                         error_type=type(error).__name__, **error_context(error))
            result['verification_retries'] = attempt
            transient = isinstance(error, BrowserError) or (
                isinstance(error, RuntimeError) and str(error) in ('Query failed', 'Lock state not yet updated'))
            if not transient or attempt == 3:
                raise
            time.sleep(attempt + 1)


def search_bill(page, bill, trace=None):
    import json5
    def mark(phase):
        if trace:
            trace(phase)
    field = page.locator('input[name=search]')
    mark('wait_idle')
    page.wait_for_function('() => !Ext.Ajax.isLoading()')
    mark('fill_bill')
    field.fill(bill)
    def matches(response):
        request = response.request
        params = parse_qs(urlparse(request.url).query)
        params.update(parse_qs(request.post_data or ''))
        return ('actionOrder!executeAll' in urlparse(request.url).path
                and any(bill == value for values in params.values() for value in values))
    mark('wait_query_response')
    with page.expect_response(matches) as pending:
        field.press('Enter')
    if not pending.value.ok:
        raise RuntimeError('Query failed')
    mark('read_query_response')
    payload = json5.loads(pending.value.text())
    if not isinstance(payload, dict) or not isinstance(payload.get('root'), list):
        raise RuntimeError('Unexpected query response')
    expected_ids = [r['jobOrderId'] for r in payload['root']]
    mark('wait_store_update')
    page.wait_for_function('''ids=>{
      let c;Ext.ComponentMgr.all.each(x=>{if(typeof x.doCancelOrder==='function')c=x});
      if(!c)return false;
      const actual=c.grid.store.getRange().map(r=>r.get('jobOrderId'));
      return JSON.stringify(actual)===JSON.stringify(ids);
    }''', arg=expected_ids)
    page.wait_for_function('() => !Ext.Ajax.isLoading()')
    return records(page)


def run(account_id, bill, kind='fee', *, execute=False, channel='chrome', headed=False):
    from playwright.sync_api import sync_playwright

    bill = validate_bill(bill).upper()
    if kind not in ('fee', 'order'):
        raise ValueError('Unknown lock kind')
    store = AccountStore()
    account = next(a for a in store.list_accounts() if a['id'] == account_id)
    password = store.password_for(account_id)
    if not password:
        raise ValueError('Missing saved password')
    directory = account_data_dir() / 'unlock-results'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    report_path = directory / f'{uuid4().hex}.json'
    result = {'account': account_id, 'bill': bill, 'kind': kind,
              'mode': 'execute' if execute else 'inspect', 'status': 'needs_verification',
              'submitted': False, 'time': datetime.now(timezone.utc).isoformat()}
    journal = None
    page = None
    authenticated = False
    requests = {}
    events = []
    def request_info(request):
        path = urlparse(request.url).path
        endpoint = ('order_query' if 'actionOrder!executeAll' in path else
                    'unlock' if path.endswith('/order_unlock') else request.resource_type)
        return {'endpoint': endpoint, 'method': request.method,
                'time': datetime.now(timezone.utc).isoformat()}
    def request_started(request):
        requests[id(request)] = request_info(request)
    def request_finished(request):
        requests.pop(id(request), None)
    def request_failed(request):
        entry = requests.pop(id(request), request_info(request))
        entry['error'] = str(request.failure or 'request_failed')[:120]
        events.append(entry)
    def response_received(response):
        if response.status >= 400:
            events.append({**request_info(response.request), 'http_status': response.status})
    def step(stage):
        record_step(result, report_path, stage)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=channel, headless=not headed)
        try:
            page = browser.new_page(viewport={'width':1440,'height':1000})
            page.set_default_timeout(20000)
            step('login')
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
            authenticated = True
            page.on('request', request_started)
            page.on('requestfinished', request_finished)
            page.on('requestfailed', request_failed)
            page.on('response', response_received)
            step('query_order')
            page.locator('input[name=search]').wait_for()
            found = search_bill(page, bill, trace=lambda phase: step('query_' + phase))
            if len(found) != 1 or bill not in (found[0]['mbl'], found[0]['hbl']):
                result['status'] = 'not_found' if not found else 'ambiguous'
                return result
            record = found[0]
            result['before'] = record
            result['matched_count'] = len(found)
            if not execute:
                result['status'] = 'inspected'
            elif record['lock'] == 'U':
                result['status'] = 'already_unlocked'
            elif kind == 'fee' and record['lock'] == 'B':
                result['status'] = 'already_fee_unlocked'
            else:
                step('select_record')
                rows = page.locator('.x-grid3-row:visible')
                if rows.count() != 1:
                    raise RuntimeError('Visible list mismatch')
                rows.click()
                selected = page.evaluate('''()=>{let c;Ext.ComponentMgr.all.each(x=>{
                  if(typeof x.doCancelOrder==='function')c=x});
                  return c.grid.getSelectionModel().getSelections().map(r=>r.get('jobOrderId'))}''')
                if selected != [record['id']]:
                    raise RuntimeError('Selection mismatch')
                result['selected_ids'] = selected
                action = '3' if kind == 'fee' else '4'
                result['action'] = action
                step('open_unlock_menu')
                button = page.get_by_role('button', name='订单解锁', exact=True)
                arrow = button.locator('..')
                bounds = arrow.bounding_box()
                arrow.click(position={'x':bounds['width']-4,'y':bounds['height']/2})
                step('choose_unlock_action')
                page.locator('.x-menu:visible').get_by_text('费用解锁' if kind == 'fee' else '订单解锁', exact=True).click()
                step('read_confirmation')
                confirm = page.locator('.ext-mb-text:visible').inner_text()
                result['confirmation_text'] = confirm[:500]
                if confirm != '确认取消锁定吗?':
                    raise RuntimeError('Unexpected confirmation')
                def expected(request):
                    data = parse_qs(request.post_data or '')
                    return (request.method == 'POST' and data.get('query') == [action]
                            and data.get('ids') == [str(record['id'])])
                def guard(route):
                    if expected(route.request):
                        route.continue_()
                    else:
                        route.abort()
                page.route('**/order_unlock', guard)
                step('reserve_submission')
                # Use the record ID so mbl/hbl aliases cannot bypass duplicate protection.
                journal = reserve_operation(directory / 'submitted', account_id, str(record['id']), kind,
                                            current_lock=record['lock'], legacy_bill=bill)
                reservation = json.loads(journal.read_text())
                result['previous_submission'] = reservation['previous_submission']
                result['relocked_resubmission'] = bool(reservation['previous_submission'])
                result['submitted'] = True  # Intent is durable before the irreversible click.
                private_report(journal, result)
                reply = None
                try:
                    step('submit_and_wait_response')
                    with page.expect_response(lambda r: urlparse(r.url).path.endswith('/order_unlock') and expected(r.request)) as pending:
                        page.get_by_role('button', name='Yes', exact=True).click()
                    response = pending.value
                    result['http_status'] = response.status
                    if response.ok:
                        step('parse_platform_response')
                        reply = parse_reply(response.text())
                        result['platform_success'] = reply['success']
                        result['message'] = str(reply.get('msg', ''))[:3000]
                except Exception as error:
                    result['response_error_type'] = type(error).__name__
                    result['error_context'] = error_context(error)
                    result['failed_stage'] = result['stage']
                    result['error_type'] = type(error).__name__
                    result['failure_cause'] = ('平台已返回响应，但无法可靠解析解锁结果'
                        if result['stage'] == 'parse_platform_response' else '提交确认或等待平台响应时发生异常，是否实际执行需核实')
                    result['next_action'] = '先在 iLog 核对锁状态及订单跟踪，不要直接重复解锁'
                    result['response_note'] = 'Unrecognized reply or timeout; never automatically resubmit'
                # A new authenticated query does not depend on dismissing a result dialog.
                def verify():
                    step('verify_open_page')
                    page.goto(URL)
                    step('verify_open_list')
                    page.get_by_text('委托单列表', exact=True).click()
                    step('verify_wait_search')
                    page.locator('input[name=search]').wait_for()
                    after = search_bill(page, bill, trace=lambda phase: step('verify_' + phase))
                    step('verify_order_state')
                    if len(after) != 1 or after[0]['id'] != record['id']:
                        raise RuntimeError('Verification record mismatch')
                    result['after'] = after[0]
                    if reply and reply.get('success') is True and outcome(reply,record['lock'],after[0]['lock'],kind) not in ('verified_unlocked','verified_fee_unlocked'):
                        raise RuntimeError('Lock state not yet updated')
                    return after
                retry_verification(verify, result)
                result['status'] = outcome(reply, record['lock'], result.get('after', {}).get('lock'),kind)
                if result['status'] == 'needs_verification' and not result.get('failed_stage'):
                    result.update(failed_stage='verify_order_state', error_type='VerificationIncomplete',
                        failure_cause=f"平台返回未充分确认成功，或复查未达到目标状态（HTTP {result.get('http_status', '未知')}，复查状态 {result.get('after', {}).get('lock', '未取得')}）",
                        next_action='先在 iLog 核对锁状态及订单跟踪，不要直接重复解锁')
            step('capture_result')
            screenshot = directory / f'{report_path.stem}.png'
            page.screenshot(path=str(screenshot), full_page=True)
            screenshot.chmod(0o600)
            result['screenshot'] = str(screenshot)
            step('completed')
        except FileExistsError:
            result['status'] = 'blocked_duplicate'
            result['reason'] = 'Prior submission exists; inspect before deciding whether any further operation is needed'
        except Exception as error:
            result['failed_stage'] = result.get('stage', 'browser_setup')
            result['error_type'] = type(error).__name__
            result['error_context'] = error_context(error)
            known_errors = {
                'Visible list mismatch': '页面可见记录数量与查询结果不一致，已停止以避免选错订单',
                'Selection mismatch': '页面实际选中的记录与目标订单不一致，已停止以避免误操作',
                'Unexpected confirmation': '平台确认窗口的内容与预期不同，未继续确认',
                'Query failed': '订单查询接口返回异常，未能取得可靠查询结果',
                'Unexpected query response': '订单查询返回的数据格式与预期不同',
                'Verification record mismatch': '复查记录数量或平台记录编号与原订单不一致，已停止且不重试',
                'Lock state not yet updated': '平台返回解锁成功，但复查及重试后仍未达到目标锁状态',
            }
            result['failure_cause'] = known_errors.get(str(error),
                '等待页面控件或平台响应超时；具体是加载缓慢、页面变化还是网络问题，尚未确认'
                if type(error).__name__ == 'TimeoutError' else '页面或执行流程异常；具体原因尚未确认，请结合失败步骤和截图核查')
            if type(error).__name__ == 'TimeoutError':
                timeout_causes = {
                    'verify_open_page': '解锁后重新打开平台页面，等待页面加载超时；尚不能确认是页面资源还是网络问题',
                    'verify_open_list': '解锁后未能在限定时间内找到或打开委托单列表',
                    'verify_wait_search': '委托单列表的查询输入框未在限定时间内就绪',
                    'wait_idle': '页面仍有未结束的查询请求，等待平台空闲超时',
                    'fill_bill': '单号查询输入框未能完成输入，控件可能尚未就绪',
                    'wait_query_response': '等待该单号对应的查询响应超时，未取得可核对的返回',
                    'read_query_response': '已收到查询响应，但读取返回内容超时',
                    'wait_store_update': '查询接口已返回，但页面列表未同步为该次查询结果',
                }
                phase = re.sub(r'^(query_|verify_)', '', result['failed_stage'])
                result['failure_cause'] = timeout_causes.get(result['failed_stage'], timeout_causes.get(phase, result['failure_cause']))
            result['next_action'] = ('先在 iLog 核对锁状态及订单跟踪，不要直接重复解锁'
                                     if result['submitted'] else '先检查失败截图、登录状态及订单页面，再决定是否重新发起任务')
            result['reason'] = 'Stopped on page or network error; no automatic retry'
        finally:
            if result['status'] not in ('inspected','already_unlocked','already_fee_unlocked','verified_unlocked','verified_fee_unlocked'):
                result['request_diagnostics'] = {'failed': events[-20:], 'pending': list(requests.values())[-20:]}
            if result['status'] in ('needs_verification', 'blocked_duplicate') and authenticated and page:
                try:
                    screenshot = directory / f'{report_path.stem}-failure.png'
                    page.screenshot(path=str(screenshot), full_page=True, timeout=5000)
                    screenshot.chmod(0o600)
                    result['screenshot'] = str(screenshot)
                except Exception:
                    result['screenshot_note'] = 'Failure screenshot unavailable'
            result['report'] = str(report_path)
            compact_success(result)
            private_report(report_path, result)
            if journal:
                private_report(journal, result)
            browser.close()
    return result


def main():
    parser = argparse.ArgumentParser(description='Single-order iLog unlock; read-only by default')
    parser.add_argument('--account', required=True)
    parser.add_argument('--bill', required=True)
    parser.add_argument('--kind', choices=['fee','order'], default='fee')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--confirm-bill')
    parser.add_argument('--channel', choices=['chrome','msedge'], default='chrome')
    parser.add_argument('--show-browser', action='store_true')
    args = parser.parse_args()
    if args.execute and args.confirm_bill != args.bill:
        parser.error('--execute requires --confirm-bill matching --bill exactly')
    try:
        result = run(args.account, args.bill, args.kind, execute=args.execute,
                     channel=args.channel, headed=args.show_browser)
    except Exception:
        result = {'status':'failed','reason':'Check saved account, installed browser and network'}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] in ('inspected','already_unlocked','already_fee_unlocked','verified_unlocked','verified_fee_unlocked') else 1


if __name__ == '__main__':
    raise SystemExit(main())
