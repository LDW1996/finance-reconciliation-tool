import pytest

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
