"""Single-page query-result unlock with per-record journals."""

import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

SUCCESS = {'verified_unlocked','verified_fee_unlocked','already_unlocked','already_fee_unlocked'}


def plan_batch(found, bill, kind):
    ids = [r.get('id') for r in found]
    if not found or not all(ids) or len(set(ids)) != len(ids):
        raise ValueError('Missing or duplicate platform record IDs')
    if any(r.get('lock') not in ('A','B','U') for r in found):
        raise ValueError('Unknown lock state')
    return [dict(bill=bill,kind=kind,before=r,submitted=False,
                 status='already_unlocked' if r['lock']=='U' else
                 'already_fee_unlocked' if kind=='fee' and r['lock']=='B' else 'needs_verification') for r in found]


def batch_status(rows, kind):
    if not all(r['status'] in SUCCESS for r in rows):
        return 'batch_attention'
    if all(r['status'] in ('already_unlocked','already_fee_unlocked') for r in rows):
        return 'already_unlocked' if all(r['status']=='already_unlocked' for r in rows) else 'already_fee_unlocked'
    return 'verified_unlocked' if kind=='order' else 'verified_fee_unlocked'


def execute_batch(page, found, result, report_path, directory, step):
    from .ilog_query import URL
    from .ilog_unlock import reserve_operation, private_report, outcome, parse_reply, search_bill, retry_verification, compact_success, error_context
    rows = plan_batch(found, result['bill'], result['kind'])
    result['records'] = rows
    journals = []
    reply = None
    try:
        for row in rows:
            row.update(account=result['account'],time=datetime.now(timezone.utc).isoformat())
            if row['status'] in SUCCESS:
                continue
            step('reserve_submission')
            try:
                path=reserve_operation(directory/'submitted',result['account'],str(row['before']['id']),result['kind'],
                                       current_lock=row['before']['lock'],legacy_bill=result['bill'])
            except FileExistsError:
                row.update(status='blocked_duplicate',reason='有历史未核实提交，本条未再次解锁')
                continue
            row['previous_submission']=json.loads(path.read_text(encoding='utf-8')).get('previous_submission')
            row['relocked_resubmission']=bool(row['previous_submission'])
            journals.append((row,path))
            private_report(path,row)
        eligible = [row for row,path in journals]
        if not eligible:
            return
        ids=[str(row['before']['id']) for row in eligible]
        action='3' if result['kind']=='fee' else '4'
        def expected(request):
            data=parse_qs(request.post_data or '')
            actual=data.get('ids',[''])[0].split(',')
            return request.method=='POST' and data.get('query')==[action] and len(actual)==len(ids) and set(actual)==set(ids)
        def guard(route):
            if expected(route.request) and not result.get('request_observed'):
                result['request_observed']=True
                route.continue_()
            else:
                route.abort()
        page.route('**/order_unlock',guard)
        step('select_record')
        grid_id=page.evaluate('''()=>{let c;Ext.ComponentMgr.all.each(x=>{if(typeof x.doCancelOrder==='function')c=x});return c.grid.getEl().dom.id}''')
        grid=page.locator('[id="'+grid_id+'"]')
        if len(eligible)==len(found):
            grid.locator('.x-grid3-hd-inner > .x-grid3-hd-checker').click()
        else:
            for index,record in enumerate(found):
                if str(record['id']) in ids:
                    grid.locator('.x-grid3-row').nth(index).locator('.x-grid3-row-checker').click()
        selected=page.evaluate('''()=>{let c;Ext.ComponentMgr.all.each(x=>{if(typeof x.doCancelOrder==='function')c=x});return c.grid.getSelectionModel().getSelections().map(r=>String(r.get('jobOrderId')))}''')
        if len(selected)!=len(ids) or set(selected)!=set(ids):
            raise RuntimeError('Selection mismatch')
        result['selected_ids']=selected
        step('open_unlock_menu')
        arrow=page.get_by_role('button',name='订单解锁',exact=True).locator('..')
        bounds=arrow.bounding_box()
        arrow.click(position={'x':bounds['width']-4,'y':bounds['height']/2})
        step('choose_unlock_action')
        page.locator('.x-menu:visible').get_by_text('费用解锁' if result['kind']=='fee' else '订单解锁',exact=True).click()
        step('read_confirmation')
        if page.locator('.ext-mb-text:visible').inner_text()!='确认取消锁定吗?':
            raise RuntimeError('Unexpected confirmation')
        result['submitted']=True
        for row,path in journals:
            row['submitted']=True
            private_report(path,row)
        step('submit_and_wait_response')
        try:
            with page.expect_response(lambda r:urlparse(r.url).path.endswith('/order_unlock') and expected(r.request)) as pending:
                page.get_by_role('button',name='Yes',exact=True).click()
            response=pending.value
            result['http_status']=response.status
            if response.ok:
                step('parse_platform_response')
                reply=parse_reply(response.text())
                result.update(platform_success=reply['success'],message=str(reply.get('msg',''))[:3000])
        except Exception as error:
            result.update(response_error_type=type(error).__name__,error_context=error_context(error),
                          failed_stage=result.get('stage'),error_type=type(error).__name__,
                          failure_cause='批量提交未取得可确认的返回，需要逐条核对平台状态',
                          next_action='先核对业务号和订单跟踪，不重复提交',
                          response_note='批量提交响应未确认，不重复提交解锁')
        def verify():
            step('verify_open_page');page.goto(URL)
            step('verify_open_list');page.get_by_text('委托单列表',exact=True).click()
            step('verify_wait_search');page.locator('input[name=search]').wait_for()
            after=search_bill(page,result['bill'],trace=lambda phase:step('verify_'+phase))
            step('verify_order_state')
            if len(after)!=len(found) or {r['id'] for r in after}!={r['id'] for r in found}:
                raise RuntimeError('Verification record mismatch')
            current={str(r['id']):r for r in after}
            for row in eligible:
                row['after']=current[str(row['before']['id'])]
                row['status']=outcome(reply,row['before']['lock'],row['after']['lock'],result['kind'])
            if reply and reply['success'] and any(row['status'] not in SUCCESS for row in eligible):
                raise RuntimeError('Lock state not yet updated')
        retry_verification(verify,result)
    except Exception as error:
        causes={'Selection mismatch':'选中的记录 ID 与本次目标不一致，已停止提交',
                'Unexpected confirmation':'批量确认窗口内容与预期不同，未继续确认',
                'Verification record mismatch':'复查时业务记录发生变化或数量不一致，未再次提交',
                'Lock state not yet updated':'只读复查重试后，部分业务仍未达到目标锁状态'}
        result.update(failed_stage=result.get('stage'),error_type=type(error).__name__,error_context=error_context(error),
                      failure_cause=causes.get(str(error),'批量流程在当前步骤未完成（'+str(result.get('stage'))+'），请结合逐条锁状态及失败日志核实'),
                      next_action='按业务号逐条核对 iLog 状态，不要盲目重复提交')
    finally:
        screenshot=report_path.with_suffix('.png')
        try:
            page.screenshot(path=str(screenshot),full_page=True,timeout=5000)
            screenshot.chmod(0o600);result['screenshot']=str(screenshot)
        except Exception:
            result['screenshot_note']='Batch screenshot unavailable'
        for row in rows:
            row.update(report=str(report_path))
            if result.get('screenshot'):row['screenshot']=result['screenshot']
            for key in ('platform_success','http_status','message','screenshot','verification_retries'):
                if key in result and row['submitted']:
                    row[key]=result[key]
            if row['status'] not in SUCCESS:
                for key in ('failed_stage','error_type','error_context','failure_cause','next_action'):
                    if key in result and row['status']!='blocked_duplicate':row[key]=result[key]
                row['steps']=result.get('steps',[])
                row['verification_attempts']=result.get('verification_attempts',[])
            compact_success(row)
        for row,path in journals:private_report(path,row)
        result['status']=batch_status(rows,result['kind'])
        if result['status'] in SUCCESS:result.pop('matches',None)
