from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, MagicMock
import pytest
from app.ilog_metrics import Timings
from app.ilog_query import LOGIN_URL, ORDER_LIST_PATH
from app.ilog_session import SessionPool
from app.ilog_unlock import compact_success


def test_compact_keeps_monotonic_timings(monkeypatch):
    ticks = iter([10, 11, 13, 16])
    monkeypatch.setattr('app.ilog_metrics.monotonic', lambda: next(ticks))
    result = {'status': 'verified_unlocked'}
    timer = Timings(result)
    timer.stage('login')
    timer.stage('query_order')
    timer.finish()
    compact_success(result)
    assert result['total_seconds'] == 6
    assert result['detail']['timings'] == [
        {'stage': 'login', 'seconds': 2}, {'stage': 'query', 'seconds': 3}]


def mock_browser():
    browser = Mock()
    page = browser.new_page.return_value
    page.goto.side_effect = lambda url: setattr(page, 'url', url)
    page.locator.return_value.count.return_value = 0
    return browser


def test_pool_reuse_isolated_and_closed():
    pool = SessionPool(login_url=LOGIN_URL)
    runtime = Mock()
    pool._runtime = runtime
    runtime.chromium.launch.return_value = mock_browser()
    page1 = pool.page('a', {'username': 'one'}, 'secret')
    assert pool.page('a', {'username': 'one'}, 'secret') is page1
    assert runtime.chromium.launch.call_count == 1
    runtime.chromium.launch.return_value = mock_browser()
    page2 = pool.page('b', {'username': 'two'}, 'secret')
    assert page2 is not page1
    with ThreadPoolExecutor() as worker:
        with pytest.raises(RuntimeError):
            worker.submit(pool.page, 'a', {'username': 'one'}, 'secret').result()
    pool.close()
    runtime.stop.assert_called_once()
    with pytest.raises(RuntimeError):
        pool.page('a', {'username': 'one'}, 'secret')


def test_invalidate_closes_only_target_and_forces_new_login():
    pool = SessionPool(login_url=LOGIN_URL)
    pool._runtime = Mock()
    first_browser = mock_browser()
    other_browser = mock_browser()
    replacement_browser = mock_browser()
    pool._runtime.chromium.launch.side_effect = [first_browser, other_browser, replacement_browser]
    first = pool.page('a', {'username': 'one'}, 'secret')
    other = pool.page('b', {'username': 'two'}, 'secret')
    pool.invalidate('a')
    first_browser.close.assert_called_once()
    other_browser.close.assert_not_called()
    assert pool.page('b', {'username': 'two'}, 'secret') is other
    assert pool.page('a', {'username': 'one'}, 'secret') is not first
    pool.invalidate('missing')
    pool.close()


def test_voyage_preview_without_target_is_read_only(monkeypatch, tmp_path):
    from app import ilog_unlock, ilog_voyage
    store = Mock()
    store.list_accounts.return_value = [{'id': 'a', 'username': 'one'}]
    store.password_for.return_value = 'test-password'
    monkeypatch.setattr(ilog_unlock, 'AccountStore', lambda: store)
    monkeypatch.setattr(ilog_unlock, 'account_data_dir', lambda: tmp_path)
    found = [{'id': 'record1', 'ship': 'SHIP', 'voyage': 'V001', 'lock': 'A'}]
    query = Mock(return_value=found)
    monkeypatch.setattr(ilog_unlock, 'search_bill', query)
    execute = Mock()
    monkeypatch.setattr(ilog_voyage, 'execute_voyage', execute)
    pool = Mock()
    result = ilog_unlock.run('a', 'TEST001', scope='voyage', execute=False, session_pool=pool)
    assert result['status'] == 'inspected'
    assert result['matches'] == found
    assert result['submitted'] is False
    query.assert_called_once()
    execute.assert_not_called()
    pool.invalidate.assert_not_called()
    pool.page.return_value.screenshot.assert_not_called()


def test_voyage_execute_without_target_rejected_before_login(monkeypatch):
    from app import ilog_unlock
    store = Mock()
    monkeypatch.setattr(ilog_unlock, 'AccountStore', store)
    pool = Mock()
    with pytest.raises(ValueError, match='Voyage target required'):
        ilog_unlock.run('a', 'TEST001', scope='voyage', execute=True, session_pool=pool)
    store.assert_not_called()
    pool.page.assert_not_called()


def test_finish_copies_final_timings_into_batch_children(monkeypatch):
    ticks = iter([0, 1, 4, 6])
    monkeypatch.setattr('app.ilog_metrics.monotonic', lambda: next(ticks))
    result = {'status': 'verified_unlocked'}
    timer = Timings(result)
    timer.stage('query_order')
    # Batch children only exist after query/execution, before run's finally.
    result['records'] = [{'status': 'verified_unlocked'}, {'status': 'already_unlocked'}]
    timer.stage('save')
    timer.finish()
    expected = [{'stage': 'query', 'seconds': 3}, {'stage': 'save', 'seconds': 2}]
    for record in [result, *result['records']]:
        compact_success(record)
        assert record['timings'] == expected
        assert record['detail']['timings'] == expected
        assert record['total_seconds'] == 6
    result['records'][0]['timings'][0]['seconds'] = 999
    assert result['timings'] == expected
    assert result['records'][1]['timings'] == expected


def test_disabled_timings_do_not_add_fields_to_children():
    result = {'records': [{}]}
    timer = Timings(result, enabled=False)
    timer.stage('save')
    timer.finish()
    assert result == {'records': [{}]}


@pytest.mark.parametrize('verify_after,expected,queries', [(True,'verified_fee_unlocked',2),(False,'platform_accepted',1)])
def test_single_mode_controls_post_submission_query(tmp_path, monkeypatch, verify_after, expected, queries):
    from app import ilog_unlock
    from types import SimpleNamespace
    store=Mock();store.list_accounts.return_value=[{'id':'a','username':'one'}];store.password_for.return_value='test'
    monkeypatch.setattr(ilog_unlock,'AccountStore',lambda:store)
    monkeypatch.setattr(ilog_unlock,'account_data_dir',lambda:tmp_path)
    before={'id':'one','lock':'A','booking':'JOB1'}
    query=Mock(side_effect=[[before],[{**before,'lock':'B'}]])
    monkeypatch.setattr(ilog_unlock,'search_bill',query)
    pool=Mock();page=pool.page.return_value
    page.url = LOGIN_URL + ORDER_LIST_PATH
    page.locator.return_value.count.return_value=1
    page.locator.return_value.inner_text.return_value='确认取消锁定吗?'
    page.evaluate.return_value=['one']
    page.get_by_role.return_value.locator.return_value.bounding_box.return_value={'width':100,'height':30}
    response=SimpleNamespace(ok=True,status=200,text=lambda:'{"success":true}')
    page.expect_response.return_value=MagicMock()
    page.expect_response.return_value.__enter__.return_value.value=response
    result=ilog_unlock.run('a','TEST001',execute=True,session_pool=pool,verify_after=verify_after)
    assert result['status']==expected
    assert query.call_count==queries
    assert result['verification_performed'] is verify_after
    pool.invalidate.assert_not_called()
    if verify_after:
        page.goto.assert_called_once_with(LOGIN_URL + ORDER_LIST_PATH)
    else:
        page.goto.assert_not_called()
