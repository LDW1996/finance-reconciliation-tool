"""Compatibility contract for UI, task creation and read-only queries."""
import pytest
from app.bill_validation import normalize_bills, valid_bill
from app.ilog_query import validate_bill
from app.ilog_tasks import TaskManager

@pytest.mark.parametrize('value', ['082600028858', 'TRBT', '2609', 'A-1/2_3', 'A' * 80, '_', '/'])
def test_existing_query_formats_remain_supported(value):
    assert valid_bill(value)
    assert validate_bill(' ' + value.lower() + ' ') == value.lower()
    assert TaskManager.normalize_bills([value.lower(), value]) == [value.upper()]

@pytest.mark.parametrize('value', ['', 'A' * 81, '中文', 'A^B', '<script>', 'A B'])
def test_invalid_query_formats_stay_rejected(value):
    assert not valid_bill(value)
    with pytest.raises(ValueError, match='Invalid bill number'):
        validate_bill(value)
    with pytest.raises(ValueError, match='存在格式异常的单号'):
        normalize_bills([value])

def test_list_errors_and_stable_order_remain_compatible():
    assert normalize_bills([' b ', 'A', 'B', '0826']) == ['B', 'A', '0826']
    assert normalize_bills([]) == []
    for value, message in [('A', '单号必须为列表'), ([None], '单号格式异常')]:
        with pytest.raises(ValueError, match=message):
            normalize_bills(value)
