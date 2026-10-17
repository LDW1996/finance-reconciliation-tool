import pytest
import json

from app.ilog_unlock import parse_reply, outcome, reserve_operation


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
