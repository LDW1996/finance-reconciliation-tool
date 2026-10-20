import pytest
import json

from app.ilog_unlock import parse_reply, outcome, reserve_operation, record_step, compact_success, error_context, retry_verification


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


def test_reservation_prevents_duplicate_submission(tmp_path):
    path = reserve_operation(tmp_path, 'west', 'BILL123456', 'fee')
    assert path.exists()
    with pytest.raises(FileExistsError):
        reserve_operation(tmp_path, 'west', 'BILL123456', 'fee')
    assert reserve_operation(tmp_path, 'west', 'BILL123456', 'order') != path


def test_relocked_order_keeps_previous_success_and_reserves_new_attempt(tmp_path):
    path = reserve_operation(tmp_path, 'west', 'record1', 'order')
    previous = {'status': 'verified_unlocked', 'platform_success': True,
                'after': {'id': 'record1', 'lock': 'U'}}
    path.write_text(json.dumps(previous))
    retry = reserve_operation(tmp_path, 'west', 'record1', 'order', current_lock='B')
    assert retry != path
    assert json.loads(path.read_text()) == previous
    assert json.loads(retry.read_text())['previous_submission'] == path.name
    with pytest.raises(FileExistsError):
        reserve_operation(tmp_path, 'west', 'record1', 'order', current_lock='B')


@pytest.mark.parametrize('status,success,lock', [
    ('needs_verification', True, 'U'), ('verified_unlocked', False, 'U'),
    ('verified_unlocked', True, 'B'), ('reserved_before_submission', True, 'U')])
def test_uncertain_history_still_blocks_relocked_order(tmp_path, status, success, lock):
    path = reserve_operation(tmp_path, 'west', 'record1', 'order')
    path.write_text(json.dumps({'status': status, 'platform_success': success,
                               'after': {'id': 'record1', 'lock': lock}}))
    with pytest.raises(FileExistsError):
        reserve_operation(tmp_path, 'west', 'record1', 'order', current_lock='B')


def test_successful_repeated_attempt_and_legacy_identity(tmp_path):
    legacy = reserve_operation(tmp_path, 'west', 'BILL123456', 'order')
    success = {'status': 'verified_unlocked', 'platform_success': True,
               'after': {'id': 'record1', 'lock': 'U'}}
    legacy.write_text(json.dumps(success))
    first = reserve_operation(tmp_path, 'west', 'record1', 'order',
                              current_lock='B', legacy_bill='BILL123456')
    first.write_text(json.dumps(success))
    second = reserve_operation(tmp_path, 'west', 'record1', 'order',
                               current_lock='A', legacy_bill='BILL123456')
    assert second != first
    assert json.loads(second.read_text())['previous_submission'] == first.name


@pytest.mark.parametrize('kind,lock,record', [('fee', 'B', 'record1'),
    ('order', 'U', 'record1'), ('order', '?', 'record1'), ('order', 'B', 'other')])
def test_no_bypass_for_fee_unknown_state_or_different_record(tmp_path, kind, lock, record):
    path = reserve_operation(tmp_path, 'west', 'record1', kind)
    path.write_text(json.dumps({'status': 'verified_unlocked', 'platform_success': True,
                               'after': {'id': record, 'lock': 'U'}}))
    with pytest.raises(FileExistsError):
        reserve_operation(tmp_path, 'west', 'record1', kind, current_lock=lock)
