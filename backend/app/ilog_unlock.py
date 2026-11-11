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
from app.ilog_query import open_order_menu, order_list_url, validate_bill


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



def submission_outcome(reply):
    """A platform acknowledgement is not an independently verified lock state."""
    if not reply or type(reply.get('success')) is not bool:
        return 'needs_verification'
    return 'platform_accepted' if reply['success'] else 'rejected'


def finish_submission(result, reply):
    result.update(status=submission_outcome(reply), verification_performed=False)
    if result['status'] == 'needs_verification':
        result.setdefault('next_action', '平台响应未确认，请人工核实；不要重复提交')


# Hit-test the search trigger exactly the way a click would: whatever sits on top
# decides whether the next bill can be queried at all.
_HIT_TEST = '''pt => {
  const el = document.elementFromPoint(pt.x, pt.y);
  if (!el) return 'unknown';
  if (el.closest('.x-form-search-trigger')) return 'trigger';
  if (el.closest('.x-mask, .ext-el-mask, .x-mask-msg')) return 'mask';
  const win = el.closest('.x-window');
  if (win) {
    const title = win.querySelector('.x-window-header-text, .x-title-text');
    return 'window:' + ((title && title.textContent) || '').trim().slice(0, 24);
  }
  return 'other:' + el.tagName;
}'''

_CLOSE_MSG = '''() => {
  if (!window.Ext) return false;
  if (Ext.Msg && Ext.Msg.isVisible && Ext.Msg.isVisible()) { Ext.Msg.hide(); return true; }
  return false;
}'''

# A window that is already gone can leave its Ext 3 modal mask behind; it is safe
# to hide that leftover mask only when no window is visible any more.
_HIDE_ORPHAN_MASK = '''() => {
  const shown = el => {
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  if ([...document.querySelectorAll('.x-window')].some(shown)) return 0;
  let hidden = 0;
  document.querySelectorAll('.ext-el-mask').forEach(mask => {
    if (mask.style.display !== 'none' && shown(mask)) { mask.style.display = 'none'; hidden += 1; }
  });
  return hidden;
}'''


def search_trigger_hit(page):
    """Describe what a click on the search icon would actually hit."""
    try:
        trigger = page.locator('input[name=search]').locator('..').locator('.x-form-search-trigger').first
        try:
            trigger.scroll_into_view_if_needed(timeout=2000)
        except Exception:
            pass
        box = trigger.bounding_box()
        if not box:
            return 'unknown'
        point = {'x': box['x'] + box['width'] / 2, 'y': box['y'] + box['height'] / 2}
        return str(page.evaluate(_HIT_TEST, point))
    except Exception:
        return 'unknown'


def close_result_overlays(page):
    """Close the message box and the platform's asynchronous Tips window."""
    closed = False
    try:
        closed = bool(page.evaluate(_CLOSE_MSG))
    except Exception:
        pass
    for text in ('Tips', '提示'):
        try:
            window = page.locator('.x-window:visible').filter(has_text=text)
            if window.count() == 0:
                continue
            button = window.first.get_by_role('button', name='Close', exact=True)
            if button.count() == 0:
                continue
            button.first.click(timeout=2500)
            closed = True
            page.wait_for_timeout(300)
        except Exception:
            continue
    try:
        if page.evaluate(_HIDE_ORPHAN_MASK):
            closed = True
    except Exception:
        pass
    return closed


def wait_for_clickable_list(page, seconds=5.0, confirmations=1):
    """Wait until the search icon is really clickable; None means it is."""
    deadline = time.time() + seconds
    stable = 0
    last = 'unknown'
    while time.time() < deadline:
        hit = search_trigger_hit(page)
        last = hit
        if hit == 'trigger':
            stable += 1
            if stable >= confirmations:
                return None
        else:
            stable = 0
            if hit.startswith(('mask', 'window')):
                close_result_overlays(page)
        time.sleep(0.6)
    return last


def reset_order_list(page):
    """Return the authenticated session to a known list state without logging in again."""
    page.goto(order_list_url(page))
    open_order_menu(page)
    page.locator('input[name=search]').wait_for()


def dismiss_result_dialog(page, result):
    # Keep the authenticated list usable for the next bill, without navigating or
    # issuing a second order query. The platform shows its unlock result in a
    # separate "Tips" window that appears after the confirmation box is hidden, so
    # hiding Ext.Msg alone leaves a modal mask that swallows the next search click.
    try:
        page.wait_for_function('() => !Ext.Ajax.isLoading()')
    except Exception as error:
        result['session_dirty'] = True
        result['session_cleanup_note'] = {'error_type': type(error).__name__}
        return
    close_result_overlays(page)
    leftover = wait_for_clickable_list(page, seconds=5.0, confirmations=2)
    # Only a positively identified blocker makes the session dirty; an unreadable
    # probe is left to the next bill's pre-flight check instead of forcing a relogin.
    if leftover is not None and leftover.startswith(('mask', 'window', 'other')):
        result['session_dirty'] = True
        result['session_cleanup_note'] = {'reason': 'result dialog still blocks the list',
                                          'hit': leftover[:80]}


def ensure_clickable_list(pool, page, account_id, account, password, channel, headed):
    """Guarantee a clickable list before every bill, whatever the page was left in."""
    hit = search_trigger_hit(page)
    if hit == 'trigger' or not hit.startswith(('mask', 'window')):
        return page, None
    close_result_overlays(page)
    if wait_for_clickable_list(page, seconds=4.0) is None:
        return page, 'dialog_closed'
    try:
        reset_order_list(page)
        if wait_for_clickable_list(page, seconds=8.0) is None:
            return page, 'list_reloaded'
    except Exception:
        pass
    pool.invalidate(account_id)
    return pool.page(account_id, account, password, channel, headed), 'relogin'


def reserve_operation(directory, account, bill, kind, *, current_lock=None, legacy_bill=None):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = hashlib.sha256(json.dumps([account, bill, kind]).encode()).hexdigest()
    path = directory / f'{key}.json'
    previous = None
    if legacy_bill is not None:
        legacy_key = hashlib.sha256(json.dumps([account, legacy_bill, kind]).encode()).hexdigest()
        legacy = directory / f'{legacy_key}.json'
        if legacy != path and legacy.exists():
            previous = legacy.name
    index = 1
    # The caller checks the live lock state. History only links audit records.
    while path.exists():
        previous = path.name
        path = directory / f'{key}.{index}.json'
        index += 1
    with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'w', encoding='utf-8') as f:
        json.dump({'account': account, 'bill': bill, 'kind': kind,
                   'previous_submission': previous, 'current_lock': current_lock,
                   'status': 'reserved_before_submission',
                   'time': datetime.now(timezone.utc).isoformat()}, f)
        f.flush()
        os.fsync(f.fileno())
    return path


def private_report(path, data):
    temp = path.with_name(uuid4().hex + '.tmp')
    with os.fdopen(os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'w', encoding='utf-8') as f:
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
        booking:r.get('bkNo'),lock:r.get('lockState'),
        ship:r.get('vessName'),voyage:r.get('voyNo')}));
    }''')


def record_step(result, report_path, stage):
    result['stage'] = stage
    result.setdefault('steps', []).append({'stage': stage, 'time': datetime.now(timezone.utc).isoformat()})
    private_report(report_path, result)


def compact_success(result):
    if result.get('status') in ('inspected', 'already_unlocked', 'already_fee_unlocked',
                                'verified_unlocked', 'verified_fee_unlocked', 'platform_accepted', 'voyage_platform_confirmed'):
        for key in ('steps', 'stage', 'selected_ids', 'matched_count', 'confirmation_text',
                    'action', 'response_error_type', 'response_note', 'error_context', 'request_diagnostics',
                    'verification_attempts', 'query_attempts'):
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


def retry_verification(operation, result, *, phase='verification'):
    """Only the read-only post-submission query belongs inside operation."""
    from playwright.sync_api import Error as BrowserError
    for attempt in range(4):
        entry = {'attempt': attempt + 1, 'time': datetime.now(timezone.utc).isoformat()}
        result.setdefault(phase + '_attempts', []).append(entry)
        try:
            value = operation()
            entry['status'] = 'completed'
            result[phase + '_retries'] = attempt
            return value
        except Exception as error:
            entry.update(status='failed', stage=result.get('stage'),
                         error_type=type(error).__name__, **error_context(error))
            result[phase + '_retries'] = attempt
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
    mark('wait_initial_store')
    # HttpProxy's initial request can outlive Ext.Ajax's global loading flag.
    page.wait_for_function('''() => {
      let c; Ext.ComponentMgr.all.each(x=>{if(typeof x.doCancelOrder==='function')c=x});
      if(!c || !c.grid || !c.grid.store.reader.jsonData)return false;
      const data=c.grid.store.reader.jsonData;
      return Array.isArray(data.root) && !Ext.Ajax.isLoading();
    }''')
    mark('fill_bill')
    field.fill(bill)
    def matches(response):
        request = response.request
        params = parse_qs(urlparse(request.url).query)
        params.update(parse_qs(request.post_data or ''))
        return ('actionOrder!executeAll' in urlparse(request.url).path
                and params.get('condt.blno') == [bill])
    mark('wait_query_response')
    with page.expect_response(matches) as pending:
        field.locator('..').locator('.x-form-search-trigger').click()
    if not pending.value.ok:
        raise RuntimeError('Query failed')
    mark('read_query_response')
    payload = json5.loads(pending.value.text())
    if not isinstance(payload, dict) or not isinstance(payload.get('root'), list):
        raise RuntimeError('Unexpected query response')
    if int(payload.get('totalProperty',len(payload['root']))) > len(payload['root']):
        raise RuntimeError('Query results span multiple pages')
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


def run(account_id, bill, kind='fee', *, execute=False, channel='chrome', headed=False, expected_record_ids=None, screenshot_policy='errors', record_timings=True, session_pool=None, scope='bill', voyage_target=None, verify_after=True):
    from contextlib import nullcontext
    from .ilog_session import SessionPool
    from .ilog_metrics import Timings

    if type(verify_after) is not bool:
        raise ValueError('Invalid verification mode')
    if screenshot_policy not in ('errors', 'all', 'none'):
        raise ValueError('Unknown screenshot policy')
    if scope not in ('bill', 'voyage'):
        raise ValueError('Unknown scope')
    if execute and scope == 'voyage' and voyage_target is None:
        raise ValueError('Voyage target required')
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
    timings = Timings(result, record_timings)
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
        timings.stage(stage)
        record_step(result, report_path, stage)
    with (SessionPool() if session_pool is None else nullcontext(session_pool)) as pool:
        try:
            step('login')
            page = pool.page(account_id, account, password, channel, headed)
            authenticated = True
            page, recovery = ensure_clickable_list(pool, page, account_id, account, password, channel, headed)
            if recovery:
                result['page_recovery'] = recovery
                private_report(report_path, result)
            page.on('request', request_started)
            page.on('requestfinished', request_finished)
            page.on('requestfailed', request_failed)
            page.on('response', response_received)
            step('query_order')
            page.locator('input[name=search]').wait_for()
            found = retry_verification(
                lambda: search_bill(page, bill, trace=lambda phase: step('query_' + phase)),
                result, phase='query')
            if expected_record_ids is not None and (len(found)!=len(expected_record_ids) or
                    {str(r['id']) for r in found}!=set(expected_record_ids)):
                raise RuntimeError('Verification record mismatch')
            if scope == 'voyage' and found:
                result.update(matches=found, matched_count=len(found))
                if execute:
                    from .ilog_voyage import execute_voyage
                    execute_voyage(page, found, result, report_path, directory, step, voyage_target, verify_after=verify_after)
                else:
                    result['status'] = 'inspected'
                return result
            if len(found)>1:
                from .ilog_batch import plan_batch, execute_batch
                try:
                    plan_batch(found,bill,kind)
                except ValueError:
                    result.update(status='ambiguous',matches=found,matched_count=len(found),
                                  ambiguity_reason='匹配结果存在重复编号或未知锁状态，未提交')
                    return result
                result.update(matches=found,matched_count=len(found))
                if execute:
                    execute_batch(page,found,result,report_path,directory,step,verify_after=verify_after)
                else:
                    result['status']='inspected'
                return result
            if not found:
                result.update(status='not_found',matched_count=0,matches=[])
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
            elif record['lock'] not in ('A', 'B'):
                result['status'] = 'ambiguous'
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
                reservation = json.loads(journal.read_text(encoding='utf-8'))
                result['previous_submission'] = reservation['previous_submission']
                result['relocked_resubmission'] = bool(reservation['previous_submission'])
                result['submitted'] = True  # Intent is durable before the irreversible click.
                private_report(journal, result)
                reply = _submit_single_order(page, result, step, expected)
                _finish_single_order(page, bill, record, reply, kind, result, step, verify_after)

        except Exception as error:
            _record_execution_failure(result, error)
        finally:
            if result['status'] not in ('inspected','already_unlocked','already_fee_unlocked','verified_unlocked','verified_fee_unlocked','platform_accepted','voyage_platform_confirmed'):
                result['request_diagnostics'] = {'failed': events[-20:], 'pending': list(requests.values())[-20:]}
            success = result['status'] in ('inspected','already_unlocked','already_fee_unlocked','verified_unlocked','verified_fee_unlocked','platform_accepted','voyage_platform_confirmed')
            _save_execution_screenshot(page, result, report_path, timings, screenshot_policy, success)
            # Take diagnostic screenshots before discarding a possibly dirty page.
            reusable = success or result['status'] in ('not_found', 'ambiguous')
            invalidate = not reusable or bool(result.get('error_type') or result.get('failed_stage') or result.get('session_dirty'))
            try:
                if page:
                    for event, handler in (('request', request_started), ('requestfinished', request_finished),
                                           ('requestfailed', request_failed), ('response', response_received)):
                        page.remove_listener(event, handler)
                    page.unroute('**/order_unlock')
            except Exception as error:
                invalidate = True
                result['session_cleanup_note'] = {'error_type': type(error).__name__, **error_context(error)}
            finally:
                if invalidate:
                    pool.invalidate(account_id)
            timings.stage('save')
            result['report'] = str(report_path)
            compact_success(result)
            private_report(report_path, result)
            if journal:
                private_report(journal, result)
            timings.finish()
            private_report(report_path, result)
            if journal:
                private_report(journal, result)
    return result


def main():
    parser = argparse.ArgumentParser(description='Single-order iLog unlock; read-only by default')
    parser.add_argument('--account', required=True)
    parser.add_argument('--bill', required=True)
    parser.add_argument('--kind', choices=['fee','order'], default='fee')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--confirm-bill')
    parser.add_argument('--confirm-record-ids', help='Optional exact comma-separated platform IDs for a scoped live test')
    parser.add_argument('--channel', choices=['chrome','msedge'], default='chrome')
    parser.add_argument('--show-browser', action='store_true')
    args = parser.parse_args()
    if args.execute and args.confirm_bill != args.bill:
        parser.error('--execute requires --confirm-bill matching --bill exactly')
    try:
        result = run(args.account, args.bill, args.kind, execute=args.execute,
                     channel=args.channel, headed=args.show_browser,
                     expected_record_ids=args.confirm_record_ids.split(',') if args.confirm_record_ids else None)
    except Exception:
        result = {'status':'failed','reason':'Check saved account, installed browser and network'}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] in ('inspected','already_unlocked','already_fee_unlocked','verified_unlocked','verified_fee_unlocked') else 1


if __name__ == '__main__':
    raise SystemExit(main())


def _record_execution_failure(result, error):
    result['failed_stage'] = result.get('stage', 'browser_setup')
    result['error_type'] = type(error).__name__
    result['error_context'] = error_context(error)
    known_errors = {
        '代表提单对应的船名、航次或业务记录已变化，请重新查询核对': '代表提单对应的船名、航次或业务记录已变化；本次未再次提交，请重新查询核对',
        '航次核对账号或操作类型不一致': '航次核对账号或操作类型与当前任务不一致，已停止提交',
        'Visible list mismatch': '页面可见记录数量与查询结果不一致，已停止以避免选错订单',
        'Selection mismatch': '页面实际选中的记录与目标订单不一致，已停止以避免误操作',
        'Unexpected confirmation': '平台确认窗口的内容与预期不同，未继续确认',
        'Query failed': '订单查询接口返回异常，未能取得可靠查询结果',
        'Unexpected query response': '订单查询返回的数据格式与预期不同',
        'Query results span multiple pages': '查询结果跨多页，未取得完整记录，本次不解锁',
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
            'wait_initial_store': '平台默认订单列表尚未完成首次加载，未触发提单查询',
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



def _save_execution_screenshot(page, result, report_path, timings, screenshot_policy, success):
    if page and (screenshot_policy == 'all' or (screenshot_policy == 'errors' and not success)):
        timings.stage('screenshot')
        try:
            screenshot = report_path.with_suffix('.png')
            page.screenshot(path=str(screenshot), full_page=True, timeout=5000)
            screenshot.chmod(0o600)
            result['screenshot'] = str(screenshot)
        except Exception as error:
            result['screenshot_note'] = {'error_type': type(error).__name__, **error_context(error)}



def _verify_single_order(page, bill, record, reply, kind, result, step):
    step('verify_open_page')
    page.goto(order_list_url(page))
    step('verify_open_list')
    open_order_menu(page)
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



def _submit_single_order(page, result, step, expected):
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
    return reply


def _finish_single_order(page, bill, record, reply, kind, result, step, verify_after):
    if verify_after:
        # A new authenticated query does not depend on dismissing a result dialog.
        def verify():
            return _verify_single_order(page, bill, record, reply, kind, result, step)
        retry_verification(verify, result)
        result['status'] = outcome(reply, record['lock'], result.get('after', {}).get('lock'),kind)
        if result['status'] == 'needs_verification' and not result.get('failed_stage'):
            result.update(failed_stage='verify_order_state', error_type='VerificationIncomplete',
                failure_cause=f"平台返回未充分确认成功，或复查未达到目标状态（HTTP {result.get('http_status', '未知')}，复查状态 {result.get('after', {}).get('lock', '未取得')}）",
                next_action='先在 iLog 核对锁状态及订单跟踪，不要直接重复解锁')
        result['verification_performed'] = True
    else:
        finish_submission(result, reply)
        if reply is not None:
            step('dismiss_platform_result')
            dismiss_result_dialog(page, result)
