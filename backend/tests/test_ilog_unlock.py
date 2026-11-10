import pytest
import json

from app.ilog_unlock import parse_reply, outcome, reserve_operation, record_step, compact_success, error_context, retry_verification


def test_private_report_writes_utf8_under_windows_default_encoding(tmp_path, monkeypatch):
    import os
    from app.ilog_unlock import private_report
    original = os.fdopen
    def windows_fdopen(fd, mode='r', *args, **kwargs):
        kwargs.setdefault('encoding', 'cp1252')
        return original(fd, mode, *args, **kwargs)
    monkeypatch.setattr(os, 'fdopen', windows_fdopen)
    path = tmp_path / 'report.json'
    data = {'message': '查询失败，请核实记录', 'status': 'needs_verification'}
    private_report(path, data)
    assert json.loads(path.read_text(encoding='utf-8')) == data
    assert not list(tmp_path.glob('*.tmp'))


def test_verification_recovers_on_third_retry(monkeypatch):
    from playwright.sync_api import TimeoutError
    delays = []
    monkeypatch.setattr('app.ilog_unlock.time.sleep', delays.append)
    calls = []
    result = {'stage':'verify_wait_query_response'}
    def query():
        calls.append('read_only')
        if len(calls) < 4:
            raise TimeoutError('Page.expect_response: Timeout 20000ms exceeded.')
        return 'U'
    assert retry_verification(query, result) == 'U'
    assert len(calls) == 4 and delays == [1, 2, 3]
    assert len(result['verification_attempts']) == 4


def test_verification_stops_after_three_retries(monkeypatch):
    from playwright.sync_api import TimeoutError
    monkeypatch.setattr('app.ilog_unlock.time.sleep', lambda _: None)
    calls = []
    def query():
        calls.append(1)
        raise TimeoutError('Page.goto: Timeout 20000ms exceeded.')
    result = {'stage':'verify_open_page'}
    with pytest.raises(TimeoutError):
        retry_verification(query, result)
    assert len(calls) == 4
    assert result['verification_attempts'][-1]['stage'] == 'verify_open_page'


def test_verification_never_retries_ambiguous_or_invalid_results(monkeypatch):
    monkeypatch.setattr('app.ilog_unlock.time.sleep', lambda _: pytest.fail('Unexpected retry'))
    def query():
        raise RuntimeError('Verification record mismatch')
    with pytest.raises(RuntimeError):
        retry_verification(query, {})


def test_initial_query_retries_without_using_verification_diagnostics(monkeypatch):
    from playwright.sync_api import TimeoutError
    monkeypatch.setattr('app.ilog_unlock.time.sleep',lambda _:None)
    calls=[]
    result={'stage':'query_wait_store_update'}
    def query():
        calls.append(1)
        if len(calls)==1: raise TimeoutError('Page.wait_for_function: Timeout 20000ms exceeded.')
        return [{'id':'target'}]
    assert retry_verification(query,result,phase='query') == [{'id':'target'}]
    assert result['query_retries']==1 and len(result['query_attempts'])==2
    assert 'verification_attempts' not in result


def test_search_waits_for_initial_store_then_clicks_search_icon():
    from app.ilog_unlock import search_bill
    events=[]
    class Response:
        ok=True
        def text(self): return '{"root":[{"jobOrderId":"record1"}]}'
    class Pending:
        value=Response()
        def __enter__(self): return self
        def __exit__(self,*args): pass
    class Locator:
        def fill(self,value): events.append(('fill',value))
        def locator(self,selector): events.append(('locator',selector)); return self
        def click(self): events.append(('click',))
        def press(self,*args): pytest.fail('Search must use the visible magnifier')
    class Page:
        def locator(self,selector): return Locator()
        def wait_for_function(self,script,**kwargs): events.append(('wait',script))
        def expect_response(self,predicate):
            from types import SimpleNamespace
            req=SimpleNamespace(url='https://example.com/actionOrder!executeAll?query=All',post_data='condt.blno=BILL123456')
            assert predicate(SimpleNamespace(url='https://example.com/actionOrder!executeAll?query=All',request=req))
            assert not predicate(SimpleNamespace(url='https://example.com/actionOrder!executeAll?other=BILL123456',request=SimpleNamespace(url='https://example.com/actionOrder!executeAll?other=BILL123456',post_data='')))
            return Pending()
        def evaluate(self,script): return [{'id':'record1','mbl':'BILL123456'}]
    assert search_bill(Page(),'BILL123456')[0]['id']=='record1'
    assert 'reader.jsonData' in events[0][1]
    assert next(i for i,e in enumerate(events) if e[0]=='fill') > 0
    assert ('locator','.x-form-search-trigger') in events
    assert ('click',) in events


def test_error_context_extracts_operation_without_secrets():
    error = RuntimeError('Page.goto: Timeout 20000ms exceeded.\nCall log: password=secret https://example.com/?token=secret')
    assert error_context(error) == {'operation': 'Page.goto', 'timeout_ms': 20000}
    assert error_context(RuntimeError('private-data')) == {}


def test_execution_steps_have_order_and_timestamps(tmp_path):
    result = {}
    report = tmp_path / 'report.json'
    record_step(result, report, 'select_record')
    record_step(result, report, 'open_unlock_menu')
    saved = json.loads(report.read_text())
    assert saved['stage'] == 'open_unlock_menu'
    assert [step['stage'] for step in saved['steps']] == ['select_record', 'open_unlock_menu']
    assert all(step['time'] for step in saved['steps'])


def test_only_success_discards_diagnostic_details():
    success = {'status': 'verified_unlocked', 'steps': [{'stage':'login'}],
               'stage':'completed', 'selected_ids':['record1'], 'confirmation_text':'confirm',
               'before':{'id':'record1','lock':'B'}, 'after':{'id':'record1','lock':'U'},
               'platform_success':True, 'submitted':True, 'screenshot':'proof.png'}
    compact_success(success)
    assert 'steps' not in success and 'stage' not in success and 'selected_ids' not in success
    assert success['after']['lock'] == 'U' and success['submitted']
    assert success['screenshot'] == 'proof.png'
    failure = {'status':'needs_verification','steps':[{'stage':'read_confirmation'}],
               'failed_stage':'read_confirmation','error_type':'TimeoutError'}
    compact_success(failure)
    assert failure['steps'] and failure['failed_stage'] == 'read_confirmation'


def test_legacy_reply_is_parsed_as_data():
    assert parse_reply("{success:true,msg:'成功',}")['success'] is True
    assert parse_reply('{"success":false,"msg":"无权限"}')['success'] is False


@pytest.mark.parametrize('raw', ['alert(1)', '{success:"true"}', '<html>failure</html>'])
def test_invalid_reply_cannot_report_success(raw):
    with pytest.raises(ValueError):
        parse_reply(raw)


def test_uncertain_result_is_not_success():
    assert outcome(None, 'A', 'B') == 'needs_verification'
    assert outcome({'success': False}, 'A', 'B') == 'rejected'
    assert outcome({'success': True}, 'A', 'B') == 'platform_confirmed'
    assert outcome({'success': True}, 'A', 'U') == 'verified_unlocked'
    assert outcome({'success': True}, 'A', 'A') == 'needs_verification'
    assert outcome({'success': True}, 'A', None) == 'needs_verification'
    assert outcome({'success': True}, 'A', 'B', 'fee') == 'verified_fee_unlocked'
    assert outcome({'success': True}, 'A', 'B', 'order') == 'platform_confirmed'


@pytest.mark.parametrize('kind,lock', [('fee', 'A'), ('order', 'B')])
@pytest.mark.parametrize('history', ['reserved_before_submission', 'needs_verification', 'verified_unlocked', 'unreadable'])
def test_locked_record_can_submit_again_and_keeps_history(tmp_path, kind, lock, history):
    legacy = reserve_operation(tmp_path, 'west', 'BILL123456', kind)
    original = reserve_operation(tmp_path, 'west', 'record1', kind)
    raw = b'invalid JSON' if history == 'unreadable' else json.dumps({'status': history}).encode()
    original.write_bytes(raw)

    retry = reserve_operation(tmp_path, 'west', 'record1', kind,
                              current_lock=lock, legacy_bill='BILL123456')
    assert retry != original
    assert original.read_bytes() == raw
    assert legacy.exists()
    assert json.loads(retry.read_text())['previous_submission'] == original.name


def test_attempt_links_legacy_bill_history(tmp_path):
    legacy = reserve_operation(tmp_path, 'west', 'BILL123456', 'fee')
    attempt = reserve_operation(tmp_path, 'west', 'record1', 'fee',
                                current_lock='A', legacy_bill='BILL123456')
    assert json.loads(attempt.read_text())['previous_submission'] == legacy.name


@pytest.mark.parametrize('reply,expected', [({'success':True},'platform_accepted'),({'success':False},'rejected'),(None,'needs_verification'),({},'needs_verification')])
def test_platform_response_without_verification(reply, expected):
    from app.ilog_unlock import finish_submission
    result = {'submitted':True}
    finish_submission(result, reply)
    assert result['status'] == expected
    assert result['verification_performed'] is False
    assert 'after' not in result
