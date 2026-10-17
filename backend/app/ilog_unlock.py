"""Single-order runner: inspection by default, explicit execution, no retries."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
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


def search_bill(page, bill):
    import json5
    field = page.locator('input[name=search]')
    page.wait_for_function('() => !Ext.Ajax.isLoading()')
    field.fill(bill)
    def matches(response):
        request = response.request
        params = parse_qs(urlparse(request.url).query)
        params.update(parse_qs(request.post_data or ''))
        return ('actionOrder!executeAll' in urlparse(request.url).path
                and any(bill == value for values in params.values() for value in values))
    with page.expect_response(matches) as pending:
        field.press('Enter')
    if not pending.value.ok:
        raise RuntimeError('Query failed')
    payload = json5.loads(pending.value.text())
    if not isinstance(payload, dict) or not isinstance(payload.get('root'), list):
        raise RuntimeError('Unexpected query response')
    expected_ids = [r['jobOrderId'] for r in payload['root']]
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
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=channel, headless=not headed)
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
            found = search_bill(page, bill)
            if len(found) != 1 or bill not in (found[0]['mbl'], found[0]['hbl']):
                result['status'] = 'not_found' if not found else 'ambiguous'
                return result
            record = found[0]
            result['before'] = record
            if not execute:
                result['status'] = 'inspected'
            elif record['lock'] == 'U':
                result['status'] = 'already_unlocked'
            elif kind == 'fee' and record['lock'] == 'B':
                result['status'] = 'already_fee_unlocked'
            else:
                rows = page.locator('.x-grid3-row:visible')
                if rows.count() != 1:
                    raise RuntimeError('Visible list mismatch')
                rows.click()
                selected = page.evaluate('''()=>{let c;Ext.ComponentMgr.all.each(x=>{
                  if(typeof x.doCancelOrder==='function')c=x});
                  return c.grid.getSelectionModel().getSelections().map(r=>r.get('jobOrderId'))}''')
                if selected != [record['id']]:
                    raise RuntimeError('Selection mismatch')
                action = '3' if kind == 'fee' else '4'
                button = page.get_by_role('button', name='订单解锁', exact=True)
                arrow = button.locator('..')
                bounds = arrow.bounding_box()
                arrow.click(position={'x':bounds['width']-4,'y':bounds['height']/2})
                page.locator('.x-menu:visible').get_by_text('费用解锁' if kind == 'fee' else '订单解锁', exact=True).click()
                confirm = page.locator('.ext-mb-text:visible').inner_text()
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
                    with page.expect_response(lambda r: urlparse(r.url).path.endswith('/order_unlock') and expected(r.request)) as pending:
                        page.get_by_role('button', name='Yes', exact=True).click()
                    response = pending.value
                    result['http_status'] = response.status
                    if response.ok:
                        reply = parse_reply(response.text())
                        result['platform_success'] = reply['success']
                        result['message'] = str(reply.get('msg', ''))[:3000]
                except Exception:
                    result['response_note'] = 'Unrecognized reply or timeout; never automatically resubmit'
                # A new authenticated query does not depend on dismissing a result dialog.
                page.goto(URL)
                page.get_by_text('委托单列表', exact=True).click()
                page.locator('input[name=search]').wait_for()
                after = search_bill(page, bill)
                if len(after) == 1 and after[0]['id'] == record['id']:
                    result['after'] = after[0]
                result['status'] = outcome(reply, record['lock'], result.get('after', {}).get('lock'),kind)
            screenshot = directory / f'{report_path.stem}.png'
            page.screenshot(path=str(screenshot), full_page=True)
            screenshot.chmod(0o600)
            result['screenshot'] = str(screenshot)
        except FileExistsError:
            result['status'] = 'blocked_duplicate'
            result['reason'] = 'Prior submission exists; inspect before deciding whether any further operation is needed'
        except Exception:
            result['reason'] = 'Stopped on page or network error; no automatic retry'
        finally:
            result['report'] = str(report_path)
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
