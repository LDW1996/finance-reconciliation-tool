"""Reviewed vessel/voyage targets; platform scope is broader than query results."""
import hashlib
import json
import os
from urllib.parse import parse_qs, urlparse


def build_groups(queries):
    groups = {}
    for query in queries:
        for record in query['records']:
            ship, voyage = record.get('ship'), record.get('voyage')
            if not record.get('id') or not ship or not voyage or '/' in ship or '/' in voyage:
                raise ValueError('缺少可靠的船名、航次或业务记录编号，无法定位航次')
            identity = (query['account_id'], ship, voyage, query['kind'])
            key = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
            group = groups.setdefault(key, dict(key=key, account_id=query['account_id'],
                account_name=query['account_name'], bill=query['bill'], bills=[], kind=query['kind'],
                ship=ship, voyage=voyage, records=[], affected_count=None))
            if query['bill'] not in group['bills']: group['bills'].append(query['bill'])
            if str(record['id']) not in {str(r['id']) for r in group['records']}:
                group['records'].append(record)
    return list(groups.values())


def validate_target(found, target):
    seeds = [r for r in target['records'] if r.get('mbl') == target['bill'] or r.get('hbl') == target['bill']]
    # A partial bill may not equal either field; require a record from this reviewed group.
    ids = {str(r['id']) for r in (seeds or target['records'])}
    matching = [r for r in found if str(r.get('id')) in ids]
    if not matching or any((r.get('ship'), r.get('voyage')) != (target['ship'], target['voyage']) for r in matching):
        raise ValueError('代表提单对应的船名、航次或业务记录已变化，请重新查询核对')
    return matching[0]


def voyage_reply_status(reply):
    if reply is None: return 'needs_verification'
    return 'voyage_platform_confirmed' if reply.get('success') is True else 'rejected'


def execute_voyage(page, found, result, report_path, directory, step, target):
    from .ilog_unlock import private_report, parse_reply, search_bill, retry_verification
    from .ilog_query import URL
    seed = validate_target(found, target)
    if target['account_id'] != result['account'] or target['kind'] != result['kind']:
        raise ValueError('航次核对账号或操作类型不一致')
    action = '33' if result['kind'] == 'fee' else '44'
    scope_id = target['ship'] + '/' + target['voyage']
    result.update(voyage=target, before=seed, action=action)
    journal_dir = directory / 'submitted-voyages'; journal_dir.mkdir(parents=True, exist_ok=True)
    journal = journal_dir / (hashlib.sha256(json.dumps([result['account'], scope_id, action]).encode()).hexdigest()+'.json')
    # An uncertain prior submission must never be automatically replayed.
    previous = None
    if journal.exists():
        previous = json.loads(journal.read_text(encoding='utf-8'))
        if previous.get('status') != 'voyage_platform_confirmed':
            result.update(status='blocked_duplicate', reason='该航次存在尚未核实的历史提交，请先核对平台')
            return
        journal.unlink()
    fd = os.open(journal, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600); os.close(fd)
    result['previous_submission'] = ({k:previous.get(k) for k in ('time','status','report','platform_success')} if previous else None)
    private_report(journal, result)
    try:
        step('select_record')
        grid_id = page.evaluate("""()=>{let c;Ext.ComponentMgr.all.each(x=>{if(typeof x.doCancelOrder==='function')c=x});return c.grid.getEl().dom.id}""")
        grid = page.locator('[id="'+grid_id+'"]')
        grid.locator('.x-grid3-row').nth(next(i for i,r in enumerate(found) if r['id']==seed['id'])).click()
        selected = page.evaluate("""()=>{let c;Ext.ComponentMgr.all.each(x=>{if(typeof x.doCancelOrder==='function')c=x});return c.grid.getSelectionModel().getSelections().map(r=>String(r.get('jobOrderId')))}""")
        if selected != [str(seed['id'])]: raise RuntimeError('Selection mismatch')
        def expected(request):
            data = parse_qs(request.post_data or '')
            return request.method == 'POST' and data.get('query') == [action] and data.get('ids') == [scope_id]
        def guard(route):
            if expected(route.request) and not result.get('request_observed'):
                result['request_observed'] = True; route.continue_()
            else: route.abort()
        page.route('**/order_unlock', guard)
        step('open_unlock_menu')
        arrow = page.get_by_role('button', name='订单解锁', exact=True).locator('..')
        bounds = arrow.bounding_box(); arrow.click(position={'x':bounds['width']-4,'y':bounds['height']/2})
        step('choose_unlock_action')
        page.locator('.x-menu:visible').get_by_text('费用解锁(Vessel)' if result['kind']=='fee' else '订单解锁(Vessel)', exact=True).click()
        step('read_confirmation')
        if page.locator('.ext-mb-text:visible').inner_text() != '确认取消锁定吗?': raise RuntimeError('Unexpected confirmation')
        result['submitted'] = True; private_report(journal, result)
        step('submit_and_wait_response')
        with page.expect_response(lambda r:urlparse(r.url).path.endswith('/order_unlock') and expected(r.request)) as pending:
            page.get_by_role('button', name='Yes', exact=True).click()
        response = pending.value; result['http_status'] = response.status
        reply = parse_reply(response.text()) if response.ok else None
        if reply:
            result.update(platform_success=reply['success'], message=str(reply.get('msg',''))[:3000])
        result['status'] = voyage_reply_status(reply)
        def verify():
            step('verify_open_page'); page.goto(URL)
            page.get_by_text('委托单列表', exact=True).click(); page.locator('input[name=search]').wait_for()
            after = search_bill(page, result['bill'])
            current = validate_target(after, target); result['after'] = current
            if reply and reply.get('success') and (current['lock'] != 'U' if result['kind']=='order' else current['lock'] not in ('B','U')):
                raise RuntimeError('Lock state not yet updated')
        retry_verification(verify, result)
        result['verification_scope'] = 'representative_only'
        result['reason'] = '平台已确认航次操作；已复查代表提单，全航次影响数量未由平台提供'
    except Exception:
        result['status'] = 'needs_verification'
        raise
    finally:
        private_report(journal, result)
